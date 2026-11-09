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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from app.core.cloud import (AccountQuota, CatalogModel, CloudClient, CloudError, FREE_MODEL,
                            ModelConfig, PAID_MODEL, SegmentResponse, fetch_account_quota)
from app.core.configuration import cloud_defaults, make_model_config
from app.core.jobs import cache_key_for
from app.core.preflight import DocumentSummary, PreflightService, build_estimate, inspect_document
from app.core.pipeline import Pipeline
from app.core.prompt_loader import render_prompt
from app.core.task_store import TaskStore
from app.ui.main_window import MainWindow
from app.ui.settings import SettingsDialog


class AllowanceTests(unittest.TestCase):
    def test_account_metadata_is_authenticated_and_missing_counts_are_unknown(self):
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, json={"data": {"limit_remaining": .8,
                "effective_budget": {"remaining_usd": .3}, "free_model_daily_requests": {"remaining": 7, "limit": 50}}})
        quota = fetch_account_quota("test-key", transport=httpx.MockTransport(handler))
        self.assertEqual(quota.remaining_requests, 7)
        self.assertEqual(quota.remaining_budget, .3)
        self.assertEqual(seen[0].headers["authorization"], "Bearer test-key")
        self.assertEqual(seen[0].method, "GET")
        missing = fetch_account_quota("key", transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"data":{}})))
        self.assertIsNone(missing.remaining_requests)
        self.assertIsNone(missing.remaining_budget)

    def test_invalid_allowance_is_not_treated_as_available(self):
        for data in ({"free_model_daily_requests":{"remaining":-1}},
                     {"free_model_daily_requests":{"remaining":True}}, {"limit_remaining":"NaN"}):
            with self.assertRaises(CloudError):
                fetch_account_quota("key", transport=httpx.MockTransport(lambda _:httpx.Response(200,json={"data":data})))

    def test_known_image_and_request_fees_are_included_in_paid_reservation(self):
        client = CloudClient(ModelConfig(api_key="test", model=PAID_MODEL, paid=True,
                                         input_price=.1, output_price=.4, image_price=.002, request_price=.001))
        client.context_length = 1000000
        self.assertAlmostEqual(client.reservation_usd(), .1 + 8192*.4/1000000 + .016 + .001)

    def test_zero_allowance_blocks_before_a_completion_request(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            return httpx.Response(200,json={"data":{"free_model_daily_requests":{"remaining":0}}})
        client = CloudClient(ModelConfig(api_key="test"), httpx.MockTransport(handler))
        with self.assertRaises(CloudError) as error:
            client.check_allowance()
        self.assertEqual(error.exception.kind,"daily_quota")
        self.assertEqual(calls,["GET"])

    def test_local_exhaustion_is_reconciled_before_waiting_a_whole_day(self):
        client=CloudClient(ModelConfig(api_key='test'),httpx.MockTransport(lambda _:httpx.Response(200,
            json={'data':{'free_model_daily_requests':{'remaining':2}}})))
        client.account_quota=AccountQuota(0,50,None,time.time())
        client.check_allowance()
        self.assertEqual(client.account_quota.remaining_requests,2)

    def test_reported_paid_cost_reduces_the_cached_server_budget(self):
        def handler(request):
            return httpx.Response(200,json={'model':PAID_MODEL,'usage':{'cost':.02},'choices':[
                {'finish_reason':'stop','message':{'content':json.dumps({'notes':[{'page':1,'markdown':'accepted'}]})}}]})
        client=CloudClient(ModelConfig(api_key='test',model=PAID_MODEL,paid=True),httpx.MockTransport(handler))
        client.account_quota=AccountQuota(None,None,.10,time.time())
        client._send({},[1],None)
        self.assertAlmostEqual(client.account_quota.remaining_budget,.08)

    def test_connection_probe_uses_short_prompt_and_tracks_one_free_request(self):
        payloads = []
        def handler(request):
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={"data":[{"id":FREE_MODEL,"context_length":1000000,
                    "architecture":{"input_modalities":["image","text"]}}]})
            if request.url.path.endswith('/key'):
                return httpx.Response(200,json={"data":{"free_model_daily_requests":{"remaining":10}}})
            payload = json.loads(request.content)
            payloads.append(payload)
            return httpx.Response(200,json={"model":FREE_MODEL,"choices":[{"finish_reason":"stop", "message":{
                "content":json.dumps({"notes":[{"page":1,"markdown":"NOTEAGENT 4827"}]})}}]})
        client = CloudClient(ModelConfig(api_key="test"),httpx.MockTransport(handler))
        self.assertIn("passed",client.test_connection())
        self.assertEqual(client.account_quota.remaining_requests,9)
        self.assertEqual(len(payloads),1)
        self.assertLessEqual(payloads[0]["max_tokens"],1024)
        self.assertNotIn("derivations",payloads[0]["messages"][0]["content"])
        self.assertEqual(payloads[0]["provider"]["max_price"]["prompt"],0)


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.pdf=self.root/'lecture.pdf'
        with pymupdf.open() as document:
            for number in range(10):
                page=document.new_page()
                page.insert_text((30,40),f"Formula and source details {number}")
            document.save(self.pdf)

    def test_local_scan_and_cost_are_reference_estimates_without_inference(self):
        document=inspect_document(self.pdf)
        model=CatalogModel(PAID_MODEL,"reference",.1,.4,1000000,False)
        config={**cloud_defaults(),"paid_enabled":True}
        estimate=build_estimate(document,config,[model],AccountQuota(1,50,None,time.time()))
        self.assertEqual(document.page_count,10)
        self.assertEqual(estimate.planned_requests,2)
        self.assertTrue(estimate.backup_enabled)
        self.assertGreater(estimate.cost_low,0)
        self.assertGreaterEqual(estimate.cost_high,estimate.cost_low)
        self.assertIn("Not a quote",estimate.price_note)

    def test_cache_and_learned_segment_size_are_used_without_invalidating_content(self):
        document=inspect_document(self.pdf)
        config=cloud_defaults()
        fingerprint=make_model_config(config).fingerprint()
        prompt=hashlib.sha256(render_prompt("segment_system",language="English").encode()).hexdigest()
        key=cache_key_for(document.digest,fingerprint,prompt,"zh")
        store=TaskStore(self.root/'task.sqlite3')
        task=store.create(key,document.digest,{"segment_pages":4})
        request=store.reserve(task,FREE_MODEL,False,0)
        store.commit_response(request,key,SegmentResponse({1:"accepted",2:"accepted"},"stop",FREE_MODEL),True)
        estimate=build_estimate(document,config,store=store)
        self.assertEqual(estimate.cached_pages,2)
        self.assertEqual(estimate.pending_pages,8)
        self.assertEqual(estimate.segment_pages,4)
        self.assertEqual(estimate.planned_requests,2)
        changed=build_estimate(document,{**config,"language":"en"},store=store)
        self.assertEqual(changed.cached_pages,0)

    def test_unknown_cloud_allowance_is_not_zero_and_metadata_is_cached(self):
        service=PreflightService()
        config={**cloud_defaults(),"api_key":"test"}
        with patch('app.core.preflight.list_note_models',return_value=[]) as catalog, \
             patch('app.core.preflight.fetch_account_quota',return_value=AccountQuota(None,None,None,time.time())) as account:
            one=service.estimate(self.pdf,config)
            two=service.estimate(self.pdf,config)
            self.assertIsNone(one.quota.remaining_requests)
            self.assertEqual(one.document,two.document)
            self.assertEqual(catalog.call_count,1)
            self.assertEqual(account.call_count,1)
            service.estimate(self.pdf,config,refresh=True)
            self.assertEqual(account.call_count,2)

    def test_custom_endpoint_does_not_fetch_openrouter_or_claim_zero_cost(self):
        config={**cloud_defaults(),"provider":"custom","base_url":"https://example.com/v1"}
        with patch('app.core.preflight.list_note_models') as catalog, patch('app.core.preflight.fetch_account_quota') as account:
            estimate=PreflightService().estimate(self.pdf,config)
        catalog.assert_not_called();account.assert_not_called()
        self.assertIsNone(estimate.quota)
        self.assertIsNone(estimate.cost_low)
        self.assertIn("billed",estimate.price_note)

    def test_pipeline_with_exhausted_real_client_quota_sends_no_inference(self):
        seen=[]
        def handler(request):
            seen.append(request.method)
            if request.url.path.endswith('/models'):
                return httpx.Response(200,json={'data':[{'id':FREE_MODEL,'context_length':1000000,
                    'architecture':{'input_modalities':['image','text']}}]})
            return httpx.Response(200,json={'data':{'free_model_daily_requests':{'remaining':0}}})
        client=CloudClient(ModelConfig(api_key='test'),httpx.MockTransport(handler))
        result=Pipeline(client,store=TaskStore(self.root/'quota.sqlite3')).run(str(self.pdf),str(self.root))
        self.assertEqual(result.status,'waiting_quota')
        self.assertEqual(result.free_requests_remaining,0)
        self.assertNotIn('POST',seen)

    def test_stale_paid_price_caps_are_explained_instead_of_raised(self):
        config={**cloud_defaults(),"paid_enabled":True}
        model=CatalogModel(PAID_MODEL,"reference",.15,1.875,1000000,False)
        estimate=build_estimate(inspect_document(self.pdf),config,[model])
        self.assertIn("below",estimate.backup_warning)
        self.assertEqual(config['input_price'],.035)

    def test_backup_off_has_no_default_paid_model_reference(self):
        model=CatalogModel(PAID_MODEL,"unused",.1,.4,1000000,False)
        estimate=build_estimate(inspect_document(self.pdf),cloud_defaults(),[model])
        self.assertFalse(estimate.backup_enabled)
        self.assertEqual(estimate.cost_model,"")
        self.assertIsNone(estimate.cost_low)

    def test_paid_estimate_tracks_the_selected_model_and_refreshed_prices(self):
        models=[CatalogModel("test/small","Small",.1,.4,10000,False),
                CatalogModel("test/large","Large",.5,2,10000,False)]
        config={**cloud_defaults(),"api_key":"test","paid_enabled":True,"paid_model":"test/small"}
        service=PreflightService()
        with patch('app.core.preflight.list_note_models',return_value=models) as catalog, \
             patch('app.core.preflight.fetch_account_quota',return_value=AccountQuota(10,50,None,time.time())) as account:
            first=service.estimate(self.pdf,config)
            second=service.estimate(self.pdf,{**config,"paid_model":"test/large"},refresh=True)
        self.assertEqual(first.cost_model,"test/small")
        self.assertEqual(second.cost_model,"test/large")
        self.assertGreater(second.cost_low,first.cost_low)
        self.assertEqual((catalog.call_count,account.call_count),(2,2))

    def test_failed_price_refresh_marks_cached_estimate_as_stale(self):
        config={**cloud_defaults(),"api_key":"test","paid_enabled":True}
        model=CatalogModel(PAID_MODEL,"Backup",.1,.4,10000,False)
        service=PreflightService()
        with patch('app.core.preflight.list_note_models',side_effect=[[model],RuntimeError('offline')]), \
             patch('app.core.preflight.fetch_account_quota',return_value=AccountQuota(10,50,None,time.time())):
            service.estimate(self.pdf,config)
            estimate=service.estimate(self.pdf,config,refresh=True)
        self.assertIsNotNone(estimate.cost_low)
        self.assertIn("outdated",estimate.backup_warning)


class PreflightUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_custom_model_is_a_text_field_and_switching_restores_both_values(self):
        dialog=SettingsDialog({**cloud_defaults(),"provider":"custom","vision_model":"my-model"},auto_refresh=False)
        self.assertIsInstance(dialog.fields['vision_model'],QLineEdit)
        dialog.fields['vision_model'].setText('edited-model')
        dialog.provider.setCurrentIndex(0)
        self.assertEqual(dialog.values()['vision_model'],FREE_MODEL)
        dialog.provider.setCurrentIndex(1)
        self.assertEqual(dialog.values()['vision_model'],'edited-model')
        dialog.reject()

    def test_slow_estimate_does_not_block_file_change_and_close_waits_safely(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(MainWindow,'CONFIG_FILE',str(Path(temporary)/'config.json')):
            window=MainWindow();window.show()
            QTest.qWait(30)
            for _ in range(100):
                if window.worker is None: break
                QTest.qWait(10)
            source=Path(temporary)/'course.pdf';source.write_bytes(b'placeholder')
            def delayed(*args):
                cancel=args[3]
                for _ in range(50):
                    if cancel(): raise RuntimeError('cancelled')
                    time.sleep(.01)
                raise RuntimeError('offline')
            with patch.object(window._preflight,'estimate',side_effect=delayed):
                window._set_input_file(str(source));window._estimate_timer.stop();window._begin_estimate()
                self.assertTrue(window.drop_zone.isEnabled())
                self.assertTrue(window.settings_button.isEnabled())
                before=time.monotonic();QTest.qWait(30)
                self.assertLess(time.monotonic()-before,.2)
                window.close()
                self.assertFalse(window._closed)
                for _ in range(100):
                    if window._closed: break
                    QTest.qWait(10)
                self.assertTrue(window._closed)
                self.assertIsNone(window._estimate_worker)

    def test_switching_files_discards_a_late_previous_estimate(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(MainWindow,'CONFIG_FILE',str(Path(temporary)/'config.json')):
            window=MainWindow();window.show();QTest.qWait(30)
            for _ in range(100):
                if window.worker is None: break
                QTest.qWait(10)
            paths=[Path(temporary)/'first.pdf',Path(temporary)/'second.pdf']
            for path in paths: path.write_bytes(b'placeholder')
            entered,release=threading.Event(),threading.Event()
            def estimate(path,config,*args):
                if path==str(paths[0]):
                    entered.set();release.wait(1)
                count=2 if path==str(paths[0]) else 7
                return build_estimate(DocumentSummary(path,path,tuple([100]*count),tuple([1024]*count)),config)
            try:
                with patch.object(window._preflight,'estimate',side_effect=estimate):
                    window._set_input_file(str(paths[0]));window._estimate_timer.stop();window._begin_estimate()
                    for _ in range(100):
                        if entered.is_set(): break
                        QTest.qWait(10)
                    self.assertTrue(entered.is_set())
                    window._set_input_file(str(paths[1]));window._estimate_timer.stop();window._begin_estimate()
                    release.set()
                    for _ in range(100):
                        if window._estimate is not None and window._estimate_worker is None: break
                        QTest.qWait(10)
                    self.assertEqual(window._estimate.document.path,str(paths[1]))
                    self.assertEqual(window.document_metric.value.text(),'7 pages')
            finally:
                release.set()
                if window._estimate_worker: window._estimate_worker.requestInterruption()
                for _ in range(100):
                    if window._estimate_worker is None: break
                    QTest.qWait(10)
                window.close();QTest.qWait(10)
