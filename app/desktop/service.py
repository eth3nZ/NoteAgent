"""Desktop commands: one serial mutation worker, interruptible work, safe view models.

No GUI imports here. Both UI tests and live acceptance can inject isolated profiles.
"""

import base64
import copy
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

from app.core.cloud import list_note_models
from app.core.configuration import cloud_defaults, make_client, make_model_config
from app.core.credentials import SECRET_FIELDS
from app.core.generator.markdown import render_source_pages
from app.core.maintenance import clean_selected_storage, clean_storage, storage_info
from app.core.markdown_text import is_non_content_page
from app.core.notion import NotionClient, NotionPublishResult
from app.core.pipeline import Pipeline, PipelineResult
from app.core.preflight import PreflightService
from app.core.settings_store import SettingsStore
from app.core.source_review import SourceRenderer
from app.core.task_store import TaskStore


PUBLIC_SETTINGS = ('provider', 'base_url', 'vision_model', 'gemini_model', 'gemini_free_project',
                   'primary_paid', 'free_providers', 'paid_enabled', 'paid_model', 'paid_providers',
                   'input_price', 'output_price', 'paid_image_price', 'paid_request_price',
                   'document_limit', 'monthly_limit', 'notion_page_id', 'notion_mode',
                   'output_dir', 'output_format', 'language', 'include_source_pages', 'publish_notion')


class DesktopService:
    def __init__(self, config_path, *, settings_store=None, owns_profile=True):
        self.config_path = Path(config_path).expanduser().resolve()
        self.store_path = self.config_path.parent / '.noteagent' / 'tasks.sqlite3'
        self._settings = settings_store or SettingsStore(self.config_path)
        self._owns_profile = owns_profile
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._shutdown = threading.Event()
        self._initialization_done = threading.Event()
        self._initialization_done.set()
        self._thread = None
        self._ready = False
        self._busy = False
        self._operation_id = 0
        self._operation_error = None
        self._modal = False
        self._message = 'Opening your workspace…'
        self._events = []
        self._file = ''
        self._config = {**cloud_defaults(), 'language':'en', 'output_format':'md',
                        'output_dir':str(Path.home()/'Desktop'/'NoteAgent_Output'),
                        'include_source_pages':False,'notion_mode':'create_child','publish_notion':False}
        self._result = None
        self._markdown = ''
        self._context = None
        self._estimate = None
        self._preflight = PreflightService()
        self._metadata_lock = threading.Lock()
        self._source_lock = threading.Lock()
        self._source_cancel = threading.Event()
        self._renderer = None
        self._renderer_task = None

    def initialize(self):
        with self._lock:
            if self._shutdown.is_set():
                return
            self._initialization_done.clear()
        try:
            self._initialize()
        finally:
            self._initialization_done.set()

    def _initialize(self):
        if not self._owns_profile:
            self._message = 'Another NoteAgent window owns this profile. Close it first.'
            return
        loaded = self._settings.load(migrate=True)
        with self._lock:
            self._config.update(loaded.config)
        store = TaskStore(self.store_path)
        store.recover_interrupted()
        try:
            clean_storage(self.store_path, cancel_callback=self._shutdown.is_set)
        except (OSError, RuntimeError, sqlite3.OperationalError):
            pass
        task = store.latest()
        if task:
            self._restore(store, task)
        with self._lock:
            self._ready = True
            self._message = loaded.notice or ('Your notes are ready.' if task else 'Choose a PDF or PowerPoint to begin.')
        threading.Thread(target=self._retry_loop, daemon=True, name='noteagent-retry').start()

    def _generation_context(self):
        return self._file, self._config['language'], make_model_config(self._config).fingerprint()

    def _event(self, message):
        with self._lock:
            self._message = message
            self._events.append({'time':time.time(), 'message':message})
            self._events = self._events[-50:]

    def _restore(self, store, task):
        options = task['options']
        notes = {page:note for segment in store.segments(task['cache_key']) for page,note in segment['notes'].items()}
        revision = store.latest_revision(task['id'])
        with self._lock:
            # A saved language preference always wins for completed documents.
            if task['status'] != 'completed':
                self._config['language'] = options['language']
            self._file = options['input_path']
            self._context = (self._file, options['language'], options.get('primary_profile', make_model_config(self._config).fingerprint()))
            notion = options.get('notion_result')
            self._result = PipelineResult(task['output_path'], options['title'], task['page_count'], 0, 1,
                notion=NotionPublishResult(**notion) if notion else None, task_id=task['id'], status=task['status'],
                completed_pages=sorted(notes), cost_usd=store.cost(task['id']),
                reason='uncertain_cost' if store.uncertain(task['id']) else task['reason'], retry_at=task['retry_at'],
                paid_eligible=bool(task['paid_eligible']), revision_id=revision['id'] if revision else 0,
                exported_revision_id=options.get('exported_revision_id',0), published_revision_id=options.get('published_revision_id',0))
            self._markdown = revision['markdown'] if revision else task['markdown'] or '\n\n'.join(
                text for page,text in sorted(notes.items()) if not is_non_content_page(text))

    def snapshot(self):
        with self._lock:
            config = {key:self._config.get(key) for key in PUBLIC_SETTINGS}
            config['gemini_free_project'] = bool(config.get('gemini_free_project') or self._config.get('_gemini_free_session'))
            status = {}
            for field in SECRET_FIELDS:
                env = {'api_key':'OPENROUTER_API_KEY','gemini_api_key':'GEMINI_API_KEY','notion_token':'NOTION_TOKEN'}.get(field)
                status[field] = {'saved':bool(self._config.get(field)), 'environment':bool(env and os.environ.get(env)),
                                 'unavailable':field in self._config.get('_unavailable_credentials', ())}
            result = asdict(self._result) if self._result else None
            mode = 'create'
            if self._result and self._context == self._generation_context():
                item = self._result
                if item.status == 'completed':
                    mode = ('export' if item.revision_id != item.exported_revision_id else
                            'open' if item.output_path and Path(item.output_path).is_file() else
                            'export' if item.completed_pages or item.revision_id else 'create')
                else:
                    mode = 'reconcile' if item.reason == 'uncertain_cost' else 'resume'
            return copy.deepcopy({'ready':self._ready,'busy':self._busy,'closing':self._shutdown.is_set(),
                'message':self._message,'events':self._events,'file':self._file,'config':config,
                'credentials':status,'result':result,'mode':mode,
                'markdown':render_source_pages(self._markdown, bool(self._config.get('include_source_pages'))),
                'estimate':self._estimate,'operation':{'id':self._operation_id,'error':self._operation_error}})

    def _run(self, operation):
        with self._lock:
            if not self._ready or not self._owns_profile or self._busy or self._shutdown.is_set():
                raise ValueError('Wait for the current operation to finish.')
            self._busy = True
            self._operation_id += 1
            operation_id = self._operation_id
            self._operation_error = None
            self._cancel.clear()
        def execute():
            try:
                operation()
            except Exception as exc:
                # Known core exceptions are designed for display. Never include request headers/config.
                from app.core.cloud import CloudError
                from app.core.credentials import CredentialError
                from app.core.task_store import BudgetExceeded, TaskBusy
                message = str(exc) if isinstance(exc, (ValueError, CloudError, CredentialError, BudgetExceeded, TaskBusy)) else 'The operation could not finish. Saved progress is kept.'
                self._operation_error = message
                self._event(message)
            finally:
                with self._lock:
                    self._busy = False
        self._thread = threading.Thread(target=execute, daemon=False, name='noteagent-operation')
        self._thread.start()
        return {'started':True,'operation_id':operation_id}

    def cancel(self):
        self._cancel.set()
        self._source_cancel.set()
        self._event('Stopping… Accepted pages are saved; the in-flight request is bounded by its timeout.')
        return {'cancelling':True}

    def choose_document(self, path):
        path = Path(path).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in {'.pdf','.pptx'}:
            raise ValueError('Choose an existing PDF or PPTX.')
        with self._lock:
            if self._busy:
                raise ValueError('Wait for the current operation first.')
            self._file = str(path)
            self._estimate = None
        return {'file':str(path)}

    def preferences(self, values):
        allowed = {'language','output_format','output_dir','include_source_pages','publish_notion','notion_mode'}
        if set(values)-allowed or values.get('language','en') not in {'en','zh'} or values.get('output_format','md') not in {'md','ipynb'}:
            raise ValueError('Invalid note options.')
        with self._lock:
            if self._busy:
                raise ValueError('Wait for the current operation first.')
            self._config.update(values)
            if self._owns_profile:
                self._settings.save_preferences(self._config)
        return {'saved':True}

    def save_settings(self, values, secrets, confirm_gemini_free=False):
        if set(values)-set(PUBLIC_SETTINGS) or set(secrets)-set(SECRET_FIELDS):
            raise ValueError('Unknown settings field.')
        candidate = {**self._config, **values}
        if candidate.get('provider') not in {'openrouter','gemini','custom'}:
            raise ValueError('Choose a supported service.')
        if candidate.get('notion_mode') not in {'none','append','create_child'}:
            raise ValueError('Choose a Notion destination mode.')
        for field in ('document_limit','monthly_limit','input_price','output_price','paid_image_price','paid_request_price'):
            import math
            if not math.isfinite(float(candidate.get(field,0))) or float(candidate.get(field,0)) < 0:
                raise ValueError('Budgets and price ceilings must be non-negative finite numbers.')
            candidate[field] = float(candidate.get(field,0))
        # The browser sends only explicit credential actions; blank untouched inputs never clear keys.
        changed = set()
        for field, update in secrets.items():
            if update.get('action') not in {'replace','clear'}:
                raise ValueError('Invalid credential action.')
            candidate[field] = str(update.get('value','')).strip() if update['action'] == 'replace' else ''
            changed.add(field)
        def save():
            result = self._settings.save(candidate, changed_secrets=changed, confirm_gemini_free=confirm_gemini_free)
            with self._lock:
                self._config = result.config
                self._estimate = None
            self._event(result.notice or 'Settings saved securely.')
        return self._run(save)

    def models(self, service):
        if service == 'gemini':
            config = {**self._config,'provider':'gemini'}
            # Listing is metadata, but still requires the Google project confirmation.
            return make_client(config).list_models(self._shutdown.is_set)
        return [asdict(model) for model in list_note_models(cancel_callback=self._shutdown.is_set)]

    def test_connection(self):
        config = dict(self._config)
        if config.get('primary_paid'):
            from app.core.cloud import FREE_MODEL
            config.update(primary_paid=False,vision_model=FREE_MODEL,free_providers='')
        return self._run(lambda:self._event(make_client(config).test_connection(self._cancel.is_set)))

    def estimate(self, refresh=False):
        with self._metadata_lock:
            with self._lock:
                path, config = self._file, dict(self._config)
            if not path or not self._ready:
                return None
            estimate = self._preflight.estimate(path, config, self.store_path, self._shutdown.is_set, refresh)
            data = asdict(estimate)
            data['document'] = {'page_count':estimate.document.page_count}
            data['reservation'] = estimate.reservation_usd
            with self._lock:
                if self._file == path and self._config == config:
                    self._estimate = data
            return data

    def start(self, *, resume=False, allow_paid=False, allow_paid_primary=False):
        with self._lock:
            config = dict(self._config)
            args = {'input_path':self._file,'output_dir':config['output_dir'],'output_format':config['output_format'],
                    'language':config['language'],'include_source_pages':bool(config.get('include_source_pages')),
                    'notion_page_id':config.get('notion_page_id',''),
                    'notion_mode':config.get('notion_mode','create_child') if config.get('publish_notion') else 'none',
                    'allow_paid':allow_paid,'allow_paid_primary':allow_paid_primary}
            if resume:
                if not self._result or self._context != self._generation_context():
                    raise ValueError('The file, language or model changed. Start a new task.')
                args['task_id'] = self._result.task_id
        def generate():
            primary = make_client(config)
            backup = make_client(config, paid=True) if allow_paid and config.get('paid_enabled') else None
            with self._lock:
                self._context = self._generation_context()
                if not resume:
                    self._markdown = ''
                    self._result = None
            self._pipeline(config, primary, backup).run(**args, progress_callback=self._event,
                stream_callback=self._stream, state_callback=self._state, cancel_callback=self._cancel.is_set,
                notion_client=self._notion(config))
            self._load_result_notes()
            self._event('Notes saved.' if self._result and self._result.status == 'completed' else 'Progress saved. Continue when ready.')
        return self._run(generate)

    def _pipeline(self, config, primary=None, backup=None):
        return Pipeline(primary, store=TaskStore(self.store_path), paid_client=backup,
                        document_limit=float(config['document_limit']), monthly_limit=float(config['monthly_limit']))

    @staticmethod
    def _notion(config):
        token = '' if 'notion_token' in config.get('_unavailable_credentials', ()) else config.get('notion_token') or os.environ.get('NOTION_TOKEN','')
        return NotionClient(token) if token else None

    def _stream(self, fragment):
        with self._lock:
            self._markdown += fragment

    def _state(self, result):
        with self._lock:
            self._result = result

    def _load_result_notes(self):
        if self._result:
            store = TaskStore(self.store_path)
            task = store.get(self._result.task_id)
            revision = store.latest_revision(task['id'])
            with self._lock:
                self._markdown = revision['markdown'] if revision else task['markdown']

    def saved_action(self, action, *, markdown=None, confirm_repeat=False):
        if action not in {'export','publish','revise'} or not self._result:
            raise ValueError('Choose saved notes first.')
        result, config = self._result, dict(self._config)
        if action == 'publish' and (result.reason == 'notion_failed' or (result.notion and result.revision_id != result.published_revision_id)) and not confirm_repeat:
            raise ValueError('Confirm the new Notion write after checking the destination page.')
        args = {'task_id':result.task_id,'input_path':'','output_dir':config['output_dir'],
                'output_format':config['output_format'],'action':action,'revision_markdown':markdown,
                'include_source_pages':bool(config.get('include_source_pages')),
                'notion_page_id':config.get('notion_page_id',''),'notion_mode':config.get('notion_mode','create_child')}
        def perform():
            self._event({'export':'Exporting notes…','publish':'Sending notes to Notion…','revise':'Saving revision…'}[action])
            value = self._pipeline(config).run(**args, notion_client=self._notion(config), cancel_callback=self._cancel.is_set)
            self._state(value)
            self._load_result_notes()
            self._event('Saved revision. Export or publish it when ready.' if action == 'revise' else
                        'Notion needs attention. Check its page before retrying.' if value.reason else 'Done.')
        return self._run(perform)

    def storage(self):
        return storage_info(self.store_path)

    def clear_cache(self, task_ids):
        def clear():
            info = clean_selected_storage(self.store_path, task_ids, cancel_callback=self._cancel.is_set)
            with self._lock:
                if self._result and self._result.task_id in info['cleared_task_ids']:
                    self._result.completed_pages = []
                    self._markdown = ''
                self._estimate = None
            self._event('Local cache cleared. Exported files and Notion pages are kept.')
        return self._run(clear)

    def uncertain_requests(self):
        if not self._result:
            return []
        return [{'id':row['id'],'model':row['model'],'reserved_usd':row['reserved']/1_000_000}
                for row in TaskStore(self.store_path).uncertain(self._result.task_id)]

    def reconcile(self, request_id, amount):
        if not self._result or request_id not in {row['id'] for row in self.uncertain_requests()}:
            raise ValueError('Choose an unresolved request for this document.')
        def settle():
            TaskStore(self.store_path).reconcile(request_id, amount)
            self._restore(TaskStore(self.store_path), TaskStore(self.store_path).get(self._result.task_id))
            self._event('Charge reconciled. Resume when ready.')
        return self._run(settle)

    def source(self, page, replacement=None):
        if not self._result:
            raise ValueError('Choose saved notes first.')
        with self._lock:
            self._source_cancel.set()
            cancel = self._source_cancel = threading.Event()
            task_id = self._result.task_id
        with self._source_lock:
            if cancel.is_set():
                raise ValueError('Source request superseded.')
            store = TaskStore(self.store_path)
            task = store.get(task_id)
            path = replacement or task['options']['input_path']
            identity = (task_id,path)
            if self._renderer_task != identity:
                if self._renderer:
                    self._renderer.close()
                self._renderer = SourceRenderer(path, task['doc_hash'])
                self._renderer_task = identity
            png = self._renderer.render(int(page), lambda:cancel.is_set() or self._shutdown.is_set())
            if replacement:
                store.update(task_id,options={**task['options'],'input_path':str(Path(replacement).resolve())})
            note = next((segment['notes'][int(page)] for segment in store.segments(task['cache_key']) if int(page) in segment['notes']), '')
            if is_non_content_page(note):
                note = 'This page was reviewed and contains no study content. Check the source if unexpected.'
            return {'page':int(page),'image':'data:image/png;base64,'+base64.b64encode(png).decode(),
                    'note':note or 'No cached note remains for this page.'}

    def close_source(self):
        self._source_cancel.set()
        with self._source_lock:
            if self._renderer:
                self._renderer.close()
            self._renderer = self._renderer_task = None

    def request_close(self):
        with self._lock:
            self._shutdown.set()
        self._source_cancel.set()
        self._cancel.set()
        if self._busy:
            self._event('Finishing the current operation before closing…')
        return not self._busy

    def close(self):
        self.request_close()
        # Keep the profile lock until a background Keychain migration finishes.
        self._initialization_done.wait()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join()
        self.close_source()

    def _retry_loop(self):
        while not self._shutdown.wait(30):
            with self._lock:
                item = self._result
                due = (not self._busy and not self._modal and item and item.status == 'waiting_quota' and
                       item.retry_at and time.time() >= item.retry_at and self._context == self._generation_context())
            if due:
                try:
                    self.start(resume=True)
                except (ValueError, RuntimeError):
                    pass

    def interaction(self, active):
        with self._lock:
            self._modal = bool(active)
        return {'active':self._modal}
