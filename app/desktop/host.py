"""macOS WKWebView host. Only Bridge.call/get_state cross the JS boundary."""

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

from app.core.profile_lock import ProfileLock
from app.core.resources import resource_path
from app.desktop.service import DesktopService


class Bridge:
    def __init__(self, service, window=None):
        self._service, self._window = service, window

    def get_state(self):
        state = self._service.snapshot()
        diagnostic = os.environ.get('NOTEAGENT_DIAGNOSTICS')
        if diagnostic and not getattr(self, '_reported_startup', False):
            self._reported_startup = True
            try:
                elapsed = time.time()-float(os.environ.get('NOTEAGENT_PROCESS_STARTED',str(time.time())))
                Path(diagnostic).write_text(json.dumps({'ui':'webview','first_ui_state_seconds':round(elapsed,3),
                    'backend_ready_at_first_state':state['ready'],'frozen':bool(getattr(sys,'frozen',False))}))
            except (OSError, ValueError):
                pass
        return state

    def call(self, command, args=None):
        args = args or {}
        if not isinstance(args, dict):
            return {'error':'Invalid command arguments.'}
        service = self._service
        handlers = {
            'preferences':lambda:service.preferences(args),
            'save_settings':lambda:service.save_settings(args.get('values',{}),args.get('secrets',{}),args.get('confirm_gemini_free') is True),
            'models':lambda:service.models(args.get('service','openrouter')),
            'test_connection':service.test_connection,
            'estimate':lambda:service.estimate(args.get('refresh') is True),
            'start':lambda:service.start(resume=args.get('resume') is True,allow_paid=args.get('allow_paid') is True,
                                         allow_paid_primary=args.get('allow_paid_primary') is True),
            'cancel':service.cancel,
            'export':lambda:service.saved_action('export'),
            'publish':lambda:service.saved_action('publish',confirm_repeat=args.get('confirm_repeat') is True),
            'revise':lambda:service.saved_action('revise',markdown=args.get('markdown')),
            'storage':service.storage,
            'clear_cache':lambda:service.clear_cache(args.get('task_ids',[])),
            'source':lambda:service.source(args.get('page',1)),
            'source_close':service.close_source,
            'interaction':lambda:service.interaction(args.get('active') is True),
            'uncertain_requests':service.uncertain_requests,
            'reconcile':lambda:service.reconcile(args.get('request_id'),args.get('amount')),
            'pick_file':lambda:self._pick_file(),
            'pick_folder':self._pick_folder,
            'locate_source':lambda:self._pick_file(source_page=args.get('page',1)),
            'open_output':self._open_output,
            'open_notion':self._open_notion,
            'open_link':lambda:self._open_link(args.get('url','')),
        }
        if command not in handlers:
            return {'error':'Unknown desktop command.'}
        try:
            return handlers[command]()
        except (ValueError, RuntimeError) as exc:
            return {'error':str(exc)}
        except Exception:
            return {'error':'The operation could not finish. Your saved notes are kept.'}

    def _pick_file(self, source_page=None):
        import webview
        if self._window is None:
            raise ValueError('The desktop window is not ready.')
        paths = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                file_types=('Course documents (*.pdf;*.pptx)',))
        if not paths:
            return None
        return (self._service.choose_document(paths[0]) if source_page is None else
                self._service.source(source_page, paths[0]))

    def _pick_folder(self):
        import webview
        if self._window is None:
            raise ValueError('The desktop window is not ready.')
        paths = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return self._service.preferences({'output_dir':paths[0]}) if paths else None

    def _open_output(self):
        result = self._service.snapshot()['result']
        if not result or not result['output_path']:
            raise ValueError('No saved output is available.')
        path = Path(result['output_path']).resolve()
        if not path.is_file() or path.suffix.lower() not in {'.md','.ipynb'}:
            raise ValueError('The exported file was moved or deleted. Export cached notes or generate again.')
        if sys.platform == 'darwin':
            subprocess.Popen(['open',str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(path.as_uri())
        return {'opened':True}

    def _open_notion(self):
        result = self._service.snapshot()['result']
        notion = result.get('notion') if result else None
        if not notion:
            raise ValueError('No published Notion page is available.')
        url = urlparse(notion['url'])
        if not (url.hostname and (url.hostname == 'notion.so' or url.hostname.endswith('.notion.so') or
                                 url.hostname == 'notion.com' or url.hostname.endswith('.notion.com'))):
            raise ValueError('Invalid Notion page address.')
        return self._open_link(notion['url'])

    @staticmethod
    def _open_link(value):
        url = urlparse(value)
        if url.scheme not in {'https','http'} or not url.hostname or url.username or url.password:
            raise ValueError('Only explicit HTTP/HTTPS links can be opened.')
        webbrowser.open(value)
        return {'opened':True}


def _menu_bar(window):
    """Reuse the NA template artwork with Cocoa, without importing Qt."""
    from AppKit import NSStatusBar, NSMenu, NSMenuItem, NSImage, NSVariableStatusItem
    from Foundation import NSObject
    import objc
    class Actions(NSObject):
        @objc.IBAction
        def show_(self, sender):
            window.show()
            window.restore()

        @objc.IBAction
        def quit_(self, sender):
            window.destroy()
    actions = Actions.alloc().init()
    item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItem)
    artwork = resource_path('app/assets/noteagent-menubar.svg')
    icon = NSImage.alloc().initWithContentsOfFile_(str(artwork))
    if icon:
        icon.setSize_((24,16)); icon.setTemplate_(True); item.button().setImage_(icon)
    else:
        item.button().setTitle_('NA')
    menu = NSMenu.alloc().init()
    for title, action in [('Open NoteAgent','show:'),('Quit NoteAgent','quit:')]:
        entry = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title,action,'')
        entry.setTarget_(actions); menu.addItem_(entry)
    item.setMenu_(menu)
    return item, actions


def _strict_csp_bridge(window):
    """WKWebView returns native JS values without pywebview's eval() wrapper.

    pywebview 6.2 uses eval for replies and Function for API creation. Replace
    those two wrappers with native evaluation and a static, two-command bridge.
    This host intentionally pins that version; keep script-src 'self'.
    """
    native_run = window.run_js
    bootstrap = Path(__file__).with_name('bridge.js').read_text(encoding='utf-8')
    def run_script(script):
        if 'window.pywebview = {' in script:
            script += '\n' + bootstrap
        return native_run(script)
    window.run_js = run_script
    def evaluate(script, callback=None):
        if callback is not None:
            raise ValueError('Asynchronous JS evaluation is not exposed by this host.')
        return window.run_js(script)
    window.evaluate_js = evaluate


def run(config_path=None):
    import webview
    from webview.dom import DOMEventHandler
    config = Path(config_path or os.environ.get('NOTEAGENT_CONFIG',str(Path.home()/'.noteagent_config.json')))
    lock = ProfileLock(str(config)+'.lock')
    if not lock.tryLock(0):
        raise SystemExit('This NoteAgent profile is already open. Fully quit its other window first.')
    service = DesktopService(config)
    bridge = Bridge(service)
    entry = resource_path('app/web/index.html')
    if not entry.is_file():
        lock.unlock()
        raise SystemExit('Frontend assets are missing. Run npm ci && npm run build in frontend/.')
    window = webview.create_window('NoteAgent',str(entry),js_api=bridge,width=1160,height=820,min_size=(820,600),
                                   background_color='#F4F2ED',text_select=True)
    if sys.platform == 'darwin':
        _strict_csp_bridge(window)
    bridge._window = window
    closing = threading.Event()
    ready_to_close = threading.Event()
    references = []
    def on_close():
        if ready_to_close.is_set():
            return True
        if not closing.is_set():
            closing.set()
            def finish():
                service.close()
                ready_to_close.set()
                window.destroy()
            threading.Thread(target=finish,daemon=False).start()
        return False
    window.events.closing += on_close
    def dropped(event):
        files = (event.get('dataTransfer') or {}).get('files') or []
        if files and files[0].get('pywebviewFullPath'):
            try:
                service.choose_document(files[0]['pywebviewFullPath'])
                service.estimate()
            except Exception:
                service._event('Could not open that document. Use Choose file to select a PDF or PPTX.')
    def loaded():
        window.dom.document.events.drop += DOMEventHandler(dropped, prevent_default=True)
    window.events.loaded += loaded
    def initialize():
        try:
            service.initialize()
        except Exception:
            service._ready = True
            service._event('The workspace could not be restored. Open Settings to check your connections; existing files are kept.')
    try:
        # The status item must be created on Cocoa's main thread.
        if sys.platform == 'darwin':
            from PyObjCTools import AppHelper
            AppHelper.callAfter(lambda:references.append(_menu_bar(window)))
        webview.start(initialize, debug=False, private_mode=True)
    finally:
        service.close()
        lock.unlock()
