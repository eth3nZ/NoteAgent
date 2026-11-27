import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import pymupdf

from app.core.cloud import CloudClient, ModelConfig, FREE_MODEL
from app.core.configuration import cloud_defaults
from app.core.credentials import SessionCredentialStore
from app.core.profile_lock import ProfileLock
from app.core.settings_store import SettingsStore
from app.core.task_store import TaskStore
from app.desktop.host import Bridge, _strict_csp_bridge
from app.desktop.service import DesktopService


class DesktopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.settings=SettingsStore(self.root/'config.json',SessionCredentialStore())
        self.service=DesktopService(self.root/'config.json',settings_store=self.settings)
        self.service.initialize()
        self.bridge=Bridge(self.service)
        self.pdf=self.root/'Course.pdf'
        with pymupdf.open() as doc:
            page=doc.new_page();page.insert_text((40,60),'A visible source definition: x = 2.');doc.save(self.pdf)
        self.service.preferences({'output_dir':str(self.root/'outputs'),'language':'en'})

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+5
        while self.service.snapshot()['busy']:
            if time.monotonic()>deadline:self.fail('Background operation did not finish')
            time.sleep(.01)

    def client(self, paid=False, charge=.001):
        model='google/gemini-test' if paid else FREE_MODEL
        def handler(request):
            if request.method=='GET':
                if request.url.path.endswith('/key'):
                    return httpx.Response(200,json={'data':{'limit_remaining':1,'free_model_daily_requests':{'remaining':50,'limit':50}}})
                return httpx.Response(200,json={'data':[{'id':model,'context_length':1000,'architecture':{'input_modalities':['text','image']},'supported_parameters':['structured_outputs']}]})
            numbers=json.loads(request.content)['response_format']['json_schema']['schema']['properties']['notes']['items']['properties']['page']['enum']
            return httpx.Response(200,json={'model':model,'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'notes':[{'page':page,'markdown':'## Topic\n\nA definition with $x=2$.'} for page in numbers]})}}],'usage':{'cost':charge,'prompt_tokens':100,'completion_tokens':30}})
        return CloudClient(ModelConfig(api_key='fixture-only',model=model,paid=paid),httpx.MockTransport(handler))

    def generate(self):
        self.service.choose_document(self.pdf)
        with patch('app.desktop.service.make_client',return_value=self.client()):
            self.service.start();self.wait()
        self.assertIsNone(self.service.snapshot()['operation']['error'])

    def test_generation_revision_export_storage_and_source_use_existing_pipeline(self):
        self.generate()
        state=self.bridge.get_state()
        self.assertEqual(state['result']['status'],'completed')
        self.assertEqual(state['mode'],'open')
        self.assertEqual(state['result']['completed_pages'],[1])
        original=Path(state['result']['output_path']).read_bytes()
        source=self.bridge.call('source',{'page':1})
        self.assertTrue(source['image'].startswith('data:image/png;base64,'))
        self.assertIn('A definition',source['note'])
        self.assertTrue(self.bridge.call('revise',{'markdown':'# Corrected\n\nA human correction.'})['started']);self.wait()
        self.assertEqual(self.service.snapshot()['result']['revision_id'],1)
        self.bridge.call('export');self.wait()
        self.assertIn('A human correction',Path(self.service.snapshot()['result']['output_path']).read_text())
        self.assertEqual(Path(state['result']['output_path']).read_bytes(),original)
        rows=self.bridge.call('storage')['tasks']
        self.assertEqual(rows[0]['protected_reason'],'Saved human revision')

    def test_paid_primary_requires_explicit_action_and_still_settles_budget(self):
        self.service.choose_document(self.pdf)
        self.service._config.update(primary_paid=True,vision_model='google/gemini-test')
        with patch('app.desktop.service.make_client',return_value=self.client(paid=True)):
            self.service.start();self.wait()
            self.assertIn('explicitly approved',self.service.snapshot()['operation']['error'])
            self.service.start(allow_paid_primary=True);self.wait()
        state=self.service.snapshot()
        self.assertIsNone(state['operation']['error'])
        self.assertAlmostEqual(state['result']['cost_usd'],.001)

    def test_repeated_clicks_do_not_create_two_jobs_and_cancel_is_immediate(self):
        def delayed():
            while not self.service._cancel.is_set():time.sleep(.005)
        self.service._run(delayed)
        with self.assertRaises(ValueError):self.service._run(lambda:None)
        started=time.monotonic();self.bridge.call('cancel')
        self.assertLess(time.monotonic()-started,.1)
        self.wait()

    def test_paid_primary_without_budget_sends_no_generation_request(self):
        self.service.choose_document(self.pdf)
        self.service._config.update(primary_paid=True,vision_model='google/gemini-test',document_limit=0)
        client=self.client(paid=True)
        with patch('app.desktop.service.make_client',return_value=client), patch.object(client,'_send',wraps=client._send) as send:
            self.service.start(allow_paid_primary=True);self.wait()
        send.assert_not_called()
        state=self.service.snapshot()
        self.assertEqual(state['result']['status'],'paused')
        self.assertEqual(state['result']['reason'],'budget')

    def test_bridge_whitelist_redaction_and_secret_patch_keep_old_values(self):
        self.service._config.update(api_key='private-router',notion_token='private-notion',gemini_api_key='private-google')
        public=json.dumps(self.bridge.get_state())
        self.assertNotIn('private-router',public);self.assertNotIn('private-notion',public);self.assertNotIn('private-google',public)
        self.assertIn('error',self.bridge.call('__dict__'))
        self.assertIn('error',self.bridge.call('open_link',{'url':'file:///etc/passwd'}))
        self.bridge.call('save_settings',{'values':{'language':'zh'},'secrets':{}});self.wait()
        self.assertEqual(self.service._config['api_key'],'private-router')
        self.assertNotIn('private-router',(self.root/'config.json').read_text())
        self.bridge.call('save_settings',{'values':{},'secrets':{'api_key':{'action':'clear'}}});self.wait()
        self.assertEqual(self.service._config['api_key'],'')

    def test_current_completed_cache_disappears_but_output_and_history_remain(self):
        self.generate()
        state=self.service.snapshot();task=state['result']['task_id'];output=Path(state['result']['output_path'])
        saved=output.read_bytes()
        self.assertFalse(self.service.storage()['tasks'][0]['protected_reason'])
        with patch('httpx.Client.send',side_effect=AssertionError('Cleanup must not send HTTP')):
            self.bridge.call('clear_cache',{'task_ids':[task]});self.wait()
        self.assertEqual(self.service.storage()['tasks'],[])
        self.assertEqual(self.service.snapshot()['markdown'],'')
        self.assertEqual(self.service.snapshot()['mode'],'open')
        self.assertEqual(output.read_bytes(),saved)
        self.assertTrue(TaskStore(self.service.store_path).get(task))

    def test_restored_notes_use_saved_language_preference_and_no_inference(self):
        self.generate();self.service.preferences({'language':'zh'})
        self.service.close()
        restored=DesktopService(self.root/'config.json',settings_store=self.settings)
        with patch('httpx.Client.send',side_effect=AssertionError('Restore must not send HTTP')):
            restored.initialize()
        try:
            self.assertEqual(restored.snapshot()['config']['language'],'zh')
            self.assertIn('A definition',restored.snapshot()['markdown'])
        finally:restored.close()

    def test_profile_lock_excludes_live_legacy_pid_and_recovers_dead_owner(self):
        path=self.root/'profile.lock';first=ProfileLock(path);second=ProfileLock(path)
        self.assertTrue(first.tryLock());self.assertFalse(second.tryLock());first.unlock()
        self.assertTrue(second.tryLock());second.unlock()
        import socket
        path.write_text(f'123456789\nold-app\n{socket.gethostname()}\n')
        self.assertTrue(first.tryLock());first.unlock()
        target=self.root/'unrelated';target.write_text('keep');path.symlink_to(target)
        self.assertFalse(first.tryLock());self.assertEqual(target.read_text(),'keep')

    def test_slow_keychain_save_is_responsive_and_close_waits_for_atomic_commit(self):
        entered, release = threading.Event(), threading.Event()
        original = self.settings.save
        def slow(*args, **kwargs):
            entered.set();release.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.settings, 'save', side_effect=slow):
            self.service.save_settings({'language':'zh'}, {})
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertTrue(self.bridge.get_state()['busy'])
            self.assertIn('error', self.bridge.call('save_settings', {'values':{},'secrets':{}}))
            self.assertLess(time.monotonic()-started, .2)
            closing = threading.Thread(target=self.service.close);closing.start()
            self.assertTrue(closing.is_alive())
            release.set();closing.join(3)
        self.assertFalse(closing.is_alive())
        self.assertEqual(json.loads((self.root/'config.json').read_text())['language'], 'zh')

    def test_close_during_initial_migration_keeps_profile_owned_until_finished(self):
        entered, release = threading.Event(), threading.Event()
        other = DesktopService(self.root/'fresh.json',settings_store=self.settings)
        original = self.settings.load
        def slow(*args, **kwargs):
            entered.set();release.wait(3)
            return original(*args, **kwargs)
        with patch.object(self.settings, 'load', side_effect=slow):
            opening = threading.Thread(target=other.initialize);opening.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(other.snapshot()['ready'])
            closing = threading.Thread(target=other.close);closing.start()
            self.assertTrue(closing.is_alive())
            release.set();opening.join(3);closing.join(3)
        self.assertFalse(closing.is_alive())

    def test_native_bridge_replies_do_not_require_csp_unsafe_eval(self):
        class Window:
            def run_js(self, script):
                self.script = script
                return {'native_value':True}
        window = Window();_strict_csp_bridge(window)
        self.assertEqual(window.evaluate_js('pywebview.reply()'), {'native_value':True})
        self.assertEqual(window.script, 'pywebview.reply()')
        with self.assertRaises(ValueError):window.evaluate_js('x',lambda value:None)
        window.run_js('window.pywebview = {};')
        self.assertIn("name !== 'get_state'",window.script)
        from app.core.configuration import make_model_config
        paid=make_model_config({**cloud_defaults(),'primary_paid':True,'paid_image_price':.002,'paid_request_price':.003})
        self.assertEqual((paid.image_price,paid.request_price),(.002,.003))
