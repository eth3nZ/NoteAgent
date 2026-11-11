import os
import time
import unittest
from unittest.mock import patch

import httpx

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from app.core.cloud import (BASE_URL, FREE_MODEL, PAID_MODEL, CatalogModel,
                            CloudError, list_note_models)
from app.core.configuration import cloud_defaults, make_client
from app.ui.settings import SettingsDialog


FREE = CatalogModel(FREE_MODEL, "NVIDIA vision", 0, 0, 1000000, True)
PAID = CatalogModel(PAID_MODEL, "Qwen vision", .035, 1.5, 1000000, False)
OTHER = CatalogModel("example/other-vision", "Other vision", .2, .8, 131072, False)


class CatalogTests(unittest.TestCase):
    def row(self, model_id=FREE_MODEL, prompt="0", completion="0", **extra):
        return {"id": model_id, "name": model_id, "context_length": 1000000,
                "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                "supported_parameters": ["response_format", "structured_outputs"],
                "pricing": {"prompt": prompt, "completion": completion, "image": "0", "request": "0"}, **extra}

    def test_catalog_is_metadata_only_and_filters_incompatible_models(self):
        rows = [self.row(), self.row(PAID_MODEL, ".000000035", ".0000015"),
                self.row("google/gemini:free"), self.row("openrouter/free"), self.row("~example/latest:free"),
                self.row("example/vision:batch", ".0000001", ".0000004"),
                self.row("example/text:free", architecture={"input_modalities": ["text"]}),
                self.row("example/unstructured:free", supported_parameters=[]),
                self.row("example/json-object:free", supported_parameters=["response_format"]),
                self.row("example/image-fees:free", pricing={"prompt": "0", "completion": "0", "image": ".001"}),
                self.row("example/bad-price", prompt="NaN"), self.row("example/unknown-price", pricing={})]
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"data": rows})
        models = list_note_models("test-key", transport=httpx.MockTransport(handler))
        self.assertEqual([m.id for m in models], ["google/gemini:free", FREE_MODEL, PAID_MODEL])
        self.assertAlmostEqual(models[2].input_price, .035)
        self.assertEqual(models[2].output_price, 1.5)
        self.assertEqual([r.method for r in requests], ["GET"])
        self.assertEqual(str(requests[0].url), BASE_URL + "/models")
        self.assertNotIn("authorization", requests[0].headers)

    def test_public_catalog_does_not_require_an_inference_key(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"data": [self.row()]})
        models = list_note_models(transport=httpx.MockTransport(handler))
        self.assertEqual([model.id for model in models], [FREE_MODEL])
        self.assertNotIn("authorization", requests[0].headers)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "API key"):
                make_client(cloud_defaults())

    def test_canonical_openrouter_endpoint_is_not_overridden_by_stale_setting(self):
        config = {**cloud_defaults(), "api_key": "test", "base_url": "https://old.example/v1"}
        self.assertEqual(make_client(config).base_url, BASE_URL)
        config.update(provider="custom", vision_model="example/vision")
        self.assertEqual(make_client(config).base_url, "https://old.example/v1")

    def test_paid_image_pricing_is_listed_and_zero_cost_routes_stay_strict(self):
        row=self.row('google/gemini-fee-test',prompt='.0000001',completion='.0000004',
                     pricing={'prompt':'.0000001','completion':'.0000004','image':'.0000001','request':'0'})
        models=list_note_models(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'data':[row]})))
        self.assertEqual(len(models),1)
        self.assertFalse(models[0].free)
        self.assertEqual(models[0].image_price,.0000001)


class SettingsCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            self.spin(lambda: dialog.worker is None)
            dialog.deleteLater()

    def spin(self, predicate):
        QTest.qWait(10)
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Settings operation did not finish")
            QTest.qWait(10)

    def dialog(self, config=None, auto_refresh=False):
        dialog = SettingsDialog(config or cloud_defaults(), auto_refresh=auto_refresh)
        self.dialogs.append(dialog)
        dialog.show()
        return dialog

    def test_key_loads_searchable_free_and_paid_models_without_inference(self):
        config = {**cloud_defaults(), "api_key": "test-key", "paid_enabled": True}
        with patch("app.ui.settings.list_note_models", return_value=[FREE, PAID, OTHER]) as fetch:
            dialog = self.dialog(config, auto_refresh=True)
            self.spin(lambda: dialog.worker is None and fetch.called)
        primary, paid = dialog.fields["vision_model"], dialog.fields["paid_model"]
        self.assertEqual(primary.count(), 1)
        self.assertEqual(paid.count(), 2)
        self.assertTrue(primary.isEditable())
        self.assertIn("Free", primary.currentText())
        self.assertIn("$0.035", paid.currentText())
        self.assertEqual(dialog.values()["paid_model"], PAID_MODEL)
        self.assertEqual(paid.lineEdit().cursorPosition(), 0)
        self.assertEqual(fetch.call_count, 1)

    def test_switching_from_saved_ollama_does_not_send_its_key_to_openrouter(self):
        config = {**cloud_defaults(), "provider": "custom", "base_url": "http://127.0.0.1:11434/v1",
                  "api_key": "old-service-key", "vision_model": "qwen3-vl:4b-instruct"}
        with patch("app.ui.settings.list_note_models", return_value=[FREE, PAID, OTHER]) as fetch:
            dialog = self.dialog(config, auto_refresh=True)
            self.assertIn("saved local-model", dialog.service_help.text())
            self.assertTrue(dialog.primary_advanced_button.isChecked())
            self.assertTrue(dialog.fields["free_providers"].isHidden())
            dialog.provider.setCurrentIndex(0)
            self.assertEqual(dialog.fields["api_key"].text(), "")
            self.assertEqual(dialog.values()["vision_model"], FREE_MODEL)
            self.assertTrue(dialog.fields["base_url"].isHidden())
            dialog.provider.setCurrentIndex(1)
            self.assertEqual(dialog.fields["api_key"].text(), "old-service-key")
            self.assertEqual(dialog.values()["vision_model"], "qwen3-vl:4b-instruct")
            self.spin(lambda: dialog.worker is None and fetch.called)
            self.assertEqual(fetch.call_args.args, ())
            self.assertNotIn("api_key", fetch.call_args.kwargs)

    def test_opening_settings_without_a_key_loads_multiple_models_and_shares_catalog(self):
        other_free = CatalogModel("example/other:free", "Another free vision", 0, 0, 131072, True)
        with patch("app.ui.settings.list_note_models", return_value=[FREE, other_free, PAID, OTHER]) as fetch:
            dialog = self.dialog(auto_refresh=True)
            self.spin(lambda: dialog.worker is None and fetch.called)
            self.assertEqual(dialog.fields["vision_model"].count(), 2)
            dialog.fields["paid_api_key"].setText("separate-backup-key")
            dialog.paid_enabled.setChecked(True)
            self.assertEqual(dialog.fields["paid_model"].count(), 2)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(fetch.call_args.args, ())
            self.assertEqual(dialog.values()["api_key"], "")
            self.assertIn("2 free models", dialog.model_note.text())
            self.assertIn("2 paid models", dialog.paid_model_note.text())

    def test_empty_catalog_explains_the_saved_only_options(self):
        dialog = self.dialog({**cloud_defaults(), "paid_enabled": True})
        dialog._catalog_loaded([])
        self.assertEqual(dialog.values()["vision_model"], FREE_MODEL)
        self.assertEqual(dialog.values()["paid_model"], PAID_MODEL)
        self.assertIn("No compatible free", dialog.model_note.text())
        self.assertIn("No compatible paid", dialog.paid_model_note.text())

    def test_paid_checkbox_disables_fields_without_erasing_them(self):
        dialog = self.dialog()
        self.assertFalse(dialog.fields["paid_model"].isEnabled())
        self.assertFalse(dialog.fields["document_limit"].isEnabled())
        self.assertIn("Off", dialog.paid_help.text())
        dialog.paid_enabled.setChecked(True)
        self.assertTrue(dialog.fields["paid_model"].isEnabled())
        self.assertEqual(dialog.values()["document_limit"], .1)
        dialog.paid_enabled.setChecked(False)
        self.assertEqual(dialog.values()["paid_model"], PAID_MODEL)

    def test_explicit_model_selection_updates_prices_but_refresh_does_not(self):
        config = {**cloud_defaults(), "api_key": "test", "paid_enabled": True, "input_price": .001}
        dialog = self.dialog(config)
        dialog._catalog_loaded([FREE, PAID, OTHER])
        self.assertEqual(dialog.fields["input_price"].value(), .001)
        combo = dialog.fields["paid_model"]
        index = combo.findData(OTHER.id)
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        self.assertEqual(dialog.values()["paid_model"], OTHER.id)
        self.assertEqual(dialog.fields["input_price"].value(), .2)
        self.assertEqual(dialog.fields["output_price"].value(), .8)
        self.assertEqual(dialog.fields["document_limit"].value(), .1)

    def test_catalog_failure_preserves_saved_model_and_manual_input(self):
        dialog = self.dialog({**cloud_defaults(), "api_key": "test"})
        with patch("app.ui.settings.list_note_models", side_effect=CloudError("Offline", "network")):
            dialog._load_catalog(force=True)
            self.spin(lambda: dialog.worker is None)
        self.assertEqual(dialog.values()["vision_model"], FREE_MODEL)
        self.assertIn("unchanged", dialog.model_note.text())
        dialog.fields["vision_model"].setEditText("example/manual:free")
        self.assertEqual(dialog.values()["vision_model"], "example/manual:free")

    def test_public_catalog_keeps_a_model_edited_while_loading(self):
        dialog = self.dialog({**cloud_defaults(), "api_key": "old"})
        def delayed(*args, **kwargs):
            time.sleep(.1)
            return [FREE, PAID]
        with patch("app.ui.settings.list_note_models", side_effect=delayed):
            dialog._load_catalog(force=True)
            dialog.fields["api_key"].setText("new")
            dialog.fields["vision_model"].setEditText("example/manual:free")
            self.spin(lambda: dialog.worker is None)
        self.assertEqual(dialog.fields["vision_model"].model_id(), "example/manual:free")
        self.assertEqual(dialog.values()["api_key"], "new")

    def test_gemini_backup_can_be_selected_and_reuses_the_main_key(self):
        gemini = CatalogModel("google/gemini-test", "Gemini vision", .1, .5, 1000000, False, .0000001)
        dialog = self.dialog({**cloud_defaults(), "api_key": "test", "paid_enabled": True})
        dialog._catalog_loaded([FREE, gemini])
        combo = dialog.fields["paid_model"]
        index = combo.findData(gemini.id)
        self.assertGreaterEqual(index, 0)
        combo.setCurrentIndex(index)
        combo.activated.emit(index)
        settings = dialog.values()
        client = make_client(settings, paid=True)
        self.assertEqual(client.model, gemini.id)
        self.assertEqual(client.config.api_key, "test")
        self.assertEqual(settings["input_price"], .1)
        self.assertEqual(settings["output_price"], .5)
        self.assertEqual(settings["document_limit"], .1)
        self.assertEqual(client.config.image_price,.0000001)
