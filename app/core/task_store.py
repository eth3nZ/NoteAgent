"""Transactional checkpoints and a conservative request ledger, using only sqlite3."""

import json
import math
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(RuntimeError):
    pass


class TaskBusy(RuntimeError):
    pass


def micros(usd):
    value = float(usd)
    if not math.isfinite(value) or value < 0:
        raise ValueError("Budget and cost must be finite non-negative values")
    return math.ceil(value * 1_000_000)


class TaskStore:
    CACHE_RETENTION_DAYS = 30
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path or Path.home() / ".noteagent" / "tasks.sqlite3")
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, cache_key TEXT NOT NULL, doc_hash TEXT NOT NULL,
                    options TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                    retry_at REAL, quota_day TEXT, paid_eligible INTEGER NOT NULL DEFAULT 0,
                    page_count INTEGER NOT NULL DEFAULT 0, markdown TEXT NOT NULL DEFAULT '',
                    output_path TEXT NOT NULL DEFAULT '', updated REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_task ON tasks(status) WHERE status='running';
                CREATE TABLE IF NOT EXISTS segments (
                    cache_key TEXT NOT NULL, first_page INTEGER NOT NULL, last_page INTEGER NOT NULL,
                    notes TEXT NOT NULL, model TEXT NOT NULL, provider TEXT NOT NULL,
                    PRIMARY KEY(cache_key, first_page, last_page)
                );
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    month TEXT NOT NULL, model TEXT NOT NULL, paid INTEGER NOT NULL,
                    reserved INTEGER NOT NULL, actual INTEGER,
                    state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    markdown TEXT NOT NULL, created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS revisions_by_task ON revisions(task_id,id);
                PRAGMA user_version=2;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def recover_interrupted(self):
        """Call once at app startup, under the app's single-instance lock."""
        with self._db() as db:
            db.execute("UPDATE tasks SET status='paused', reason='interrupted', updated=? WHERE status='running'", (self.clock(),))
            db.execute("UPDATE requests SET state='uncertain' WHERE state='reserved'")
            for row in db.execute("SELECT id,options FROM tasks WHERE status='completed'").fetchall():
                options = json.loads(row["options"])
                if options.get("publication_state") == "sending":
                    options["publication_state"] = "uncertain"
                    db.execute("UPDATE tasks SET options=?,reason='notion_failed' WHERE id=?",
                               (json.dumps(options), row["id"]))

    def latest(self):
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks ORDER BY updated DESC LIMIT 1").fetchone()
            return self._task(row) if row else None

    def _cache_plan(self, db, retention_days, protect_task_id=None):
        """Plan against one DB snapshot. Billing rows and exported files are never candidates."""
        if retention_days < 0:
            raise ValueError("Cache retention cannot be negative")
        tasks = [self._task(row) for row in db.execute("SELECT * FROM tasks ORDER BY updated DESC")]
        groups = {}
        for task in tasks:
            groups.setdefault(task['cache_key'], []).append(task)
        segments = {}
        for row in db.execute("SELECT cache_key,notes FROM segments"):
            segments.setdefault(row['cache_key'], []).append(row['notes'])
        uncertain_keys = {row[0] for row in db.execute("""SELECT DISTINCT t.cache_key FROM requests r
            JOIN tasks t ON t.id=r.task_id WHERE r.paid=1 AND r.state IN ('reserved','uncertain')""")}
        revised_keys = {row[0] for row in db.execute("SELECT DISTINCT t.cache_key FROM revisions r JOIN tasks t ON t.id=r.task_id")}
        keep = {tasks[0]['cache_key']} if tasks else set()
        keep.update(task['cache_key'] for task in tasks if task['id'] == protect_task_id)
        cutoff = self.clock() - retention_days * 86400
        expired, duplicate_ids = set(), []
        def has_saved_output(members):
            for task in members:
                try:
                    if task['output_path'] and Path(task['output_path']).is_file() and Path(task['output_path']).stat().st_size:
                        return True
                except OSError:
                    pass
            return False
        for key, members in groups.items():
            protected = key in uncertain_keys or key in revised_keys or any(
                task['status'] != 'completed' or task['reason'] or
                task['options'].get('publication_state') in {'sending','uncertain'} for task in members)
            if protected:
                continue
            if (key not in keep and max(task['updated'] for task in members) < cutoff and
                    (segments.get(key) or any(task['markdown'] for task in members)) and has_saved_output(members)):
                expired.add(key)
                continue
            # Segments remain the source of truth. Only compact duplicate bodies when
            # every page can still be reconstructed without an API request.
            try:
                pages = {int(page) for value in segments.get(key, []) for page in json.loads(value)}
            except (ValueError, TypeError):
                continue
            seen = set()
            for task in members:
                markdown = task['markdown']
                if not markdown:
                    continue
                if (markdown in seen and task['id'] != protect_task_id and not task['options'].get('published_signature') and task['page_count'] > 0 and
                        set(range(1, task['page_count'] + 1)) <= pages):
                    duplicate_ids.append(task['id'])
                else:
                    seen.add(markdown)
        orphaned = set(segments) - set(groups)
        removable_keys = expired | orphaned
        body_ids = {task['id'] for task in tasks if task['cache_key'] in expired} | set(duplicate_ids)
        size = lambda text: len(text.encode('utf-8'))
        total = sum(size(task['markdown']) for task in tasks) + sum(size(value) for values in segments.values() for value in values)
        reclaimable = sum(size(task['markdown']) for task in tasks if task['id'] in body_ids)
        reclaimable += sum(size(value) for key, values in segments.items() if key in removable_keys for value in values)
        return {'cache_bytes':total, 'reclaimable_bytes':reclaimable, 'expired_keys':expired,
                'orphaned_keys':orphaned, 'body_ids':body_ids, 'duplicate_bodies':len(duplicate_ids)}

    def cache_info(self, retention_days=CACHE_RETENTION_DAYS, protect_task_id=None):
        with self._db() as db:
            plan = self._cache_plan(db, retention_days, protect_task_id)
        return {'cache_bytes':plan['cache_bytes'], 'reclaimable_bytes':plan['reclaimable_bytes'],
                'expired_groups':len(plan['expired_keys']), 'duplicate_bodies':plan['duplicate_bodies'],
                'database_bytes':self.path.stat().st_size, 'retention_days':retention_days}

    def clean_cache(self, retention_days=CACHE_RETENTION_DAYS, protect_task_id=None, cancel_callback=None):
        """Remove expired completed content and duplicate bodies; retain task/charge history."""
        with self._db() as db:
            if cancel_callback:
                db.set_progress_handler(lambda: int(cancel_callback()), 1000)
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone():
                raise TaskBusy("Wait for the current task before cleaning its cache")
            plan = self._cache_plan(db, retention_days, protect_task_id)
            deleted_segments = 0
            for key in plan['expired_keys'] | plan['orphaned_keys']:
                deleted_segments += db.execute("DELETE FROM segments WHERE cache_key=?", (key,)).rowcount
            # Do not change updated: maintenance must never make an old task the latest.
            for task_id in plan['body_ids']:
                db.execute("UPDATE tasks SET markdown='' WHERE id=?", (task_id,))
        if plan['reclaimable_bytes']:
            # SQLite may reuse freed pages even if another reader prevents compaction.
            try:
                with self._db() as db:
                    if cancel_callback:
                        db.set_progress_handler(lambda: int(cancel_callback()), 1000)
                    db.execute("VACUUM")
            except sqlite3.OperationalError:
                pass
        result = self.cache_info(retention_days, protect_task_id)
        result.update(removed_bytes=plan['reclaimable_bytes'], removed_segments=deleted_segments,
                      compacted_bodies=plan['duplicate_bodies'])
        return result

    def latest_for_cache(self, cache_key):
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE cache_key=? ORDER BY updated DESC LIMIT 1", (cache_key,)).fetchone()
            return self._task(row) if row else None

    def get(self, task_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("Saved task was not found")
            return self._task(row)

    @staticmethod
    def _task(row):
        task = dict(row)
        task["options"] = json.loads(task["options"])
        return task

    def create(self, cache_key, doc_hash, options):
        task_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("INSERT INTO tasks(id,cache_key,doc_hash,options,status,updated) VALUES(?,?,?,?,?,?)",
                       (task_id, cache_key, doc_hash, json.dumps(options), "ready", self.clock()))
        return task_id

    def latest_revision(self, task_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM revisions WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
            return dict(row) if row else None

    def save_revision(self, task_id, markdown):
        """Append a human edit without changing generated segments or publishing anything."""
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("A revision cannot be empty")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task['status'] != 'completed':
                raise ValueError("Complete the notes before saving a revision")
            latest = db.execute("SELECT id,markdown FROM revisions WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,)).fetchone()
            if latest and latest['markdown'] == markdown:
                return latest['id']
            revision = db.execute("INSERT INTO revisions(task_id,markdown,created) VALUES(?,?,?)", (task_id, markdown, self.clock())).lastrowid
            db.execute("UPDATE tasks SET updated=? WHERE id=?", (self.clock(), task_id))
            return revision

    def update(self, task_id, **values):
        allowed = {"status", "reason", "retry_at", "quota_day", "paid_eligible", "page_count", "markdown", "output_path", "options"}
        if not values.keys() <= allowed:
            raise ValueError("Unknown task field")
        if "options" in values:
            values["options"] = json.dumps(values["options"])
        values["updated"] = self.clock()
        with self._db() as db:
            db.execute("UPDATE tasks SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                       (*values.values(), task_id))

    def acquire(self, task_id):
        try:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not row or row[0] == "running":
                    raise TaskBusy("A task is already running")
                db.execute("UPDATE tasks SET status='running',reason='',updated=? WHERE id=?", (self.clock(), task_id))
        except sqlite3.IntegrityError as exc:
            raise TaskBusy("Another task is already running") from exc

    def segments(self, cache_key):
        with self._db() as db:
            rows = db.execute("SELECT * FROM segments WHERE cache_key=? ORDER BY first_page", (cache_key,)).fetchall()
            return [{**dict(row), "notes": {int(k): v for k, v in json.loads(row["notes"]).items()}} for row in rows]

    def cost(self, task_id):
        with self._db() as db:
            # A document's spend includes previous jobs with the same source bytes.
            doc = db.execute("SELECT doc_hash FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            row = db.execute("""SELECT COALESCE(SUM(COALESCE(r.actual,r.reserved)),0) FROM requests r
                JOIN tasks t ON t.id=r.task_id WHERE t.doc_hash=? AND r.paid=1 AND r.state!='released'""", (doc,)).fetchone()
            return row[0] / 1_000_000

    def reserve(self, task_id, model, paid, amount, document_limit=.10, monthly_limit=1):
        amount = micros(amount)
        month = datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m")
        request_id = uuid.uuid4().hex
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if paid:
                doc = db.execute("SELECT doc_hash FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
                document = db.execute("""SELECT COALESCE(SUM(COALESCE(r.actual,r.reserved)),0) FROM requests r
                    JOIN tasks t ON t.id=r.task_id WHERE t.doc_hash=? AND r.paid=1 AND r.state!='released'""", (doc,)).fetchone()[0]
                monthly = db.execute("SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM requests WHERE month=? AND paid=1 AND state!='released'", (month,)).fetchone()[0]
                if document + amount > micros(document_limit) or monthly + amount > micros(monthly_limit):
                    raise BudgetExceeded("The next request's maximum cost exceeds the document or monthly budget.")
            db.execute("INSERT INTO requests(id,task_id,month,model,paid,reserved,state,created) VALUES(?,?,?,?,?,?,?,?)",
                       (request_id, task_id, month, model, int(paid), amount, "reserved", self.clock()))
        return request_id

    def uncertain(self, task_id):
        with self._db() as db:
            doc = db.execute("SELECT doc_hash FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
            return [dict(r) for r in db.execute("""SELECT r.* FROM requests r JOIN tasks t ON t.id=r.task_id
                WHERE t.doc_hash=? AND r.paid=1 AND r.state IN ('uncertain','reserved')""", (doc,))]

    def fail_request(self, request_id, uncertain=False, detail=""):
        with self._db() as db:
            db.execute("UPDATE requests SET state=?,actual=?,detail=? WHERE id=?",
                       ("uncertain" if uncertain else "released", None if uncertain else 0, detail, request_id))

    def commit_response(self, request_id, cache_key, response, save_notes):
        """Successful notes and their charge are committed in the SAME transaction."""
        cost = response.cost
        with self._db() as db:
            row = db.execute("SELECT paid,reserved FROM requests WHERE id=?", (request_id,)).fetchone()
            actual = 0 if not row["paid"] else (micros(cost) if cost is not None else None)
            state = "confirmed" if actual is not None else "uncertain"
            db.execute("UPDATE requests SET actual=?,state=?,detail=? WHERE id=?",
                       (actual, state, f"{response.finish_reason}; provider reference: {response.provider_request_id or 'not reported'}", request_id))
            if save_notes:
                pages = sorted(response.notes)
                db.execute("INSERT OR REPLACE INTO segments VALUES(?,?,?,?,?,?)", (
                    cache_key, pages[0], pages[-1], json.dumps(response.notes, ensure_ascii=False), response.model, response.provider))

    def reconcile(self, request_id, actual_usd):
        """Explicit user-entered amount after checking the provider's usage dashboard."""
        value = micros(actual_usd)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT t.doc_hash FROM requests r JOIN tasks t ON t.id=r.task_id
                WHERE r.id=? AND r.state='uncertain'""", (request_id,)).fetchone()
            if not row:
                raise ValueError("This request no longer needs reconciliation")
            db.execute("UPDATE requests SET actual=?,state='confirmed',detail='manually reconciled' WHERE id=? AND state='uncertain'", (value, request_id))
            pending = db.execute("""SELECT 1 FROM requests r JOIN tasks t ON t.id=r.task_id
                WHERE t.doc_hash=? AND r.paid=1 AND r.state IN ('uncertain','reserved') LIMIT 1""", (row['doc_hash'],)).fetchone()
            if not pending:
                db.execute("UPDATE tasks SET reason='' WHERE doc_hash=? AND reason='uncertain_cost'", (row['doc_hash'],))
