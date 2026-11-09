"""Local cache maintenance. Exported files and request ledgers are never removed."""

import json
import math
import os
import shutil
import tempfile
import time
from pathlib import Path

from app.core.task_store import TaskStore


WORKSPACE_MARKER = '.noteagent-workspace.json'


def create_workspace(kind, root=None):
    if kind not in {'pdf', 'pptx'}:
        raise ValueError('Unknown workspace kind')
    path = Path(tempfile.mkdtemp(prefix=f'noteagent_{kind}_', dir=root))
    marker = {'app':'noteagent-workspace-v1', 'pid':os.getpid(), 'created':time.time(),
              'uid':os.getuid() if hasattr(os, 'getuid') else None}
    (path / WORKSPACE_MARKER).write_text(json.dumps(marker), encoding='utf-8')
    (path / WORKSPACE_MARKER).chmod(0o600)
    return str(path)


def clean_stale_workspaces(root=None, min_age=86400, now=None, cancel_callback=None):
    """Clean only old, marked workspaces whose owning process has exited."""
    now = time.time() if now is None else now
    removed = 0
    root = Path(root or tempfile.gettempdir())
    if root.is_symlink() or not root.is_dir():
        return 0
    for path in root.iterdir():
        if cancel_callback and cancel_callback():
            break
        if not path.name.startswith(('noteagent_pdf_', 'noteagent_pptx_')) or path.is_symlink() or not path.is_dir():
            continue
        marker_path = path / WORKSPACE_MARKER
        if marker_path.is_symlink():
            continue
        try:
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
            pid, created = marker['pid'], marker['created']
            if (marker.get('app') != 'noteagent-workspace-v1' or type(pid) is not int or pid <= 0 or
                    not isinstance(created, (int,float)) or not math.isfinite(created) or now-created < min_age):
                continue
            # Use POSIX's non-signalling process check. Unknown ownership/platforms
            # are deliberately retained rather than guessing that a workspace is idle.
            if os.name != 'posix' or marker.get('uid') != os.getuid():
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                continue
            shutil.rmtree(path)
            removed += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return removed


def storage_info(store_path, protect_task_id=None):
    if not Path(store_path).exists():
        return {'cache_bytes':0, 'reclaimable_bytes':0, 'database_bytes':0,
                'expired_groups':0, 'duplicate_bodies':0, 'retention_days':30}
    return TaskStore(store_path).cache_info(protect_task_id=protect_task_id)


def clean_storage(store_path, protect_task_id=None, cancel_callback=None):
    if Path(store_path).exists():
        result = TaskStore(store_path).clean_cache(protect_task_id=protect_task_id, cancel_callback=cancel_callback)
    else:
        result = {**storage_info(store_path), 'removed_bytes':0, 'removed_segments':0, 'compacted_bodies':0}
    result['removed_workspaces'] = clean_stale_workspaces(Path(store_path).parent/'workspaces', cancel_callback=cancel_callback)
    return result


def format_bytes(value):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{value} B'
        value /= 1024
