import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

from app.core.cloud import CloudError, ModelConfig
from app.core.configuration import cloud_defaults, make_client, make_model_config
from app.core.gemini import GEMINI_BASE, GEMINI_MODEL, GeminiClient
from app.core.credentials import credential_origin
from app.core.settings_store import SettingsStore
from app.core.note_structure import navigation_context


class MemoryCredentials:
    persistent = True
    def __init__(self): self.values = {}
    def get(self,key): return self.values.get(key)
    def set(self,key,value): self.values[key] = value
    def delete(self,key): self.values.pop(key,None)


class GeminiTests(unittest.TestCase):
    def config(self, **kwargs):
        return ModelConfig(api_key='fake-google',base_url=GEMINI_BASE,model=GEMINI_MODEL,**kwargs)

    def client(self, handler):
        return GeminiClient(self.config(),httpx.MockTransport(handler),confirmed_free_project=True)

    def test_only_fixed_google_unbilled_route_is_allowed(self):
        with self.assertRaises(ValueError): GeminiClient(self.config())
        with self.assertRaises(ValueError): GeminiClient(self.config(paid=True),confirmed_free_project=True)
        with self.assertRaises(ValueError): GeminiClient(ModelConfig(api_key='fake',base_url='https://google.example/v1',model=GEMINI_MODEL),confirmed_free_project=True)

    def test_native_payload_uses_same_notes_prompt_schema_images_and_no_query_key(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200,json={'modelVersion':GEMINI_MODEL+'-001','responseId':'fixture',
                'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'notes':[{'page':3,'markdown':'## Topic\n\n$x=2$'}]})}]}}],
                'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':20,'thoughtsTokenCount':5}})
        client = self.client(handler)
        client.set_document_context('Recent accepted prose: keep moving forward.')
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'page.png'; Image.new('RGB',(30,30),'white').save(path)
            response=client.generate_segment([SimpleNamespace(page_num=3,text='x=2',screenshot_path=str(path))],'en')
        self.assertEqual(response.notes,{3:'## Topic\n\n$x=2$'})
        self.assertEqual(response.output_tokens,25)
        self.assertEqual(response.cost,0)
        self.assertNotIn('fake-google',str(seen[0].url))
        self.assertEqual(seen[0].headers['x-goog-api-key'],'fake-google')
        self.assertNotIn('authorization',seen[0].headers)
        payload=json.loads(seen[0].content)
        parts=payload['contents'][0]['parts']
        self.assertTrue(any('inlineData' in part for part in parts))
        self.assertTrue(any('Recent accepted prose' in part.get('text','') for part in parts))
        self.assertNotIn('A genuinely empty page still needs an entry explaining it',json.dumps(payload))
        self.assertEqual(payload['generationConfig']['responseJsonSchema']['properties']['notes']['items']['properties']['page']['enum'],[3])

    def test_limits_blocked_and_duplicate_pages_do_not_become_accepted_notes(self):
        for finish,notes,expected in [('MAX_TOKENS',[],'length'),('SAFETY',[],'blocked'),
                ('STOP',[{'page':1,'markdown':'First'},{'page':1,'markdown':'Duplicate'}],'incomplete'),
                ('STOP',[{'page':1,'markdown':''}],'incomplete')]:
            with self.subTest(finish=finish,notes=notes):
                client=self.client(lambda request:httpx.Response(200,json={'candidates':[{'finishReason':finish,
                    'content':{'parts':[{'text':json.dumps({'notes':notes})}]}}]}))
                value=client._send({'messages':[{'content':'system'},{'content':[]}], 'max_tokens':100,
                                  'response_format':{'json_schema':{'schema':{}}}},[1],None)
                self.assertEqual(value.finish_reason,expected)
                self.assertFalse(value.notes)

    def test_google_quota_retry_and_errors_are_sanitized(self):
        body=json.dumps({'error':{'message':'secret-and-note-content','details':[
            {'quotaMetric':'GenerateContentFreeTierRequestsPerDay'}, {'retryDelay':'9s'}]}}).encode()
        with self.assertRaises(CloudError) as raised: GeminiClient._google_error(429,body,{})
        self.assertEqual(raised.exception.kind,'daily_quota')
        self.assertEqual(raised.exception.retry_after,9)
        self.assertNotIn('secret-and-note-content',str(raised.exception))
        with self.assertRaises(CloudError) as raised: GeminiClient._google_error(403,b'{"error":"location restricted; secret"}',{})
        self.assertEqual(raised.exception.kind,'region')
        self.assertNotIn('secret',str(raised.exception))

    def test_daily_reset_uses_pacific_local_midnight_in_summer_and_winter(self):
        client=self.client(lambda request:None)
        for stamp,expected in [('2026-07-01T22:00:00+00:00','2026-07-02T07:05:00+00:00'),
                               ('2026-12-01T22:00:00+00:00','2026-12-02T08:05:00+00:00')]:
            now=datetime.fromisoformat(stamp).timestamp()
            self.assertEqual(datetime.fromtimestamp(client.next_quota_retry(now),timezone.utc).isoformat(),expected)

    def test_key_confirmation_is_bound_to_reference_and_revoked_on_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            store=SettingsStore(Path(root)/'config.json',MemoryCredentials())
            original=store.save({**cloud_defaults(),'api_key':'router-dummy','gemini_api_key':'google-dummy'},confirm_gemini_free=True).config
            google={**original,'provider':'gemini'}
            self.assertIsInstance(make_client(google),GeminiClient)
            updated=store.save(google,changed_secrets=set()).config
            self.assertEqual(updated['api_key'],'router-dummy')
            self.assertEqual(credential_origin(updated,'api_key'),'https://openrouter.ai:443')
            changed=store.save({**updated,'gemini_api_key':'new-google'},changed_secrets={'gemini_api_key'}).config
            with self.assertRaises(ValueError): make_client(changed)
            self.assertFalse(changed['gemini_free_project'])
            self.assertNotIn('google-dummy',(Path(root)/'config.json').read_text())

    def test_environment_confirmation_never_authorizes_a_different_key(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'environment-one'}):
            config={**cloud_defaults(),'provider':'gemini','_gemini_free_session':True,
                    '_gemini_free_session_key':hashlib.sha256(b'environment-one').hexdigest()}
            self.assertEqual(make_model_config(config).api_key,'environment-one')
            self.assertIsInstance(make_client(config),GeminiClient)
            with patch.dict(os.environ,{'GEMINI_API_KEY':'environment-two'}):
                with self.assertRaises(ValueError):make_client(config)

    def test_bounded_context_contains_recent_prose_and_never_source_code(self):
        prior='## Previous topic\n\nAn accepted definition to continue.\n\n```python\nsecret_literal = "keep code out of the navigation excerpt"\n```\n\nA new result from the previous page.'
        context=navigation_context('Outline',prior)
        self.assertIn('A new result',context)
        self.assertNotIn('secret_literal',context)
        self.assertLessEqual(len(navigation_context('x'*10000,prior*200)),8500)
