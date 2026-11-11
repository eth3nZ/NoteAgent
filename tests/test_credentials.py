import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.configuration import cloud_defaults, make_client, make_model_config
from app.core.credentials import (SECRET_FIELDS, CredentialError, MacOSCredentialStore,
                                  SessionCredentialStore, credential_origin)
from app.core.settings_store import SettingsStore
from app.utils.helpers import load_config, save_config


class MemoryCredentials:
    persistent = True

    def __init__(self):
        self.values = {}
        self.calls = []
        self.denied = self.bad_read = False

    def get(self, account):
        self.calls.append(("get", account))
        if self.denied:
            raise CredentialError("Keychain access denied.")
        return "wrong-value" if self.bad_read else self.values.get(account)

    def set(self, account, secret):
        self.calls.append(("set", account))
        if self.denied:
            raise CredentialError("Keychain access denied.")
        self.values[account] = secret

    def delete(self, account):
        self.calls.append(("delete", account))
        self.values.pop(account, None)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "config.json"
        self.backend = MemoryCredentials()
        self.store = SettingsStore(self.path, self.backend)
        self.config = {**cloud_defaults(), "api_key": "main-dummy", "paid_api_key": "backup-dummy",
                       "notion_token": "notion-dummy", "language": "en"}

    def test_legacy_migration_verifies_all_keys_before_removing_plaintext(self):
        save_config(self.config, str(self.path))
        result = self.store.load(migrate=True)
        raw = load_config(str(self.path))
        for key in SECRET_FIELDS:
            self.assertNotIn(key, raw)
            self.assertNotIn(self.config[key], self.path.read_text())
            self.assertEqual(self.backend.values[raw["credential_refs"][key]], self.config[key])
            self.assertEqual(result.config[key], self.config[key])
        self.assertEqual(self.store.load().config["api_key"], "main-dummy")
        self.assertEqual(raw["language"], "en")
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("dummy", repr(result))

    def test_denied_migration_retains_original_file(self):
        save_config(self.config, str(self.path))
        before = self.path.read_bytes()
        self.backend.denied = True
        result = self.store.load(migrate=True)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(result.config["api_key"], "main-dummy")
        self.assertIn("Original settings", result.notice)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_verification_failure_does_not_replace_settings(self):
        save_config(self.config, str(self.path))
        before = self.path.read_bytes()
        self.backend.bad_read = True
        with self.assertRaises(CredentialError):
            self.store.save(self.config)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.backend.values, {})

    def test_json_failure_keeps_old_records_and_removes_new_records(self):
        config = self.store.save(self.config).config
        before, records = self.path.read_bytes(), dict(self.backend.values)
        with patch("app.core.settings_store.save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.save({**config, "api_key": "new-dummy"}, changed_secrets={"api_key"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.backend.values, records)

    def test_replacing_and_clearing_keys_deletes_only_committed_old_records(self):
        config = self.store.save(self.config).config
        old = config["credential_refs"]["api_key"]
        updated = self.store.save({**config, "api_key": "new-dummy"}, changed_secrets={"api_key"}).config
        self.assertNotIn(old, self.backend.values)
        self.assertNotEqual(old, updated["credential_refs"]["api_key"])
        cleared = self.store.save({**updated, "api_key": ""}, changed_secrets={"api_key"}).config
        self.assertNotIn("api_key", cleared["credential_refs"])
        self.assertNotIn(updated["credential_refs"]["api_key"], self.backend.values)
        self.assertEqual(len(self.backend.values), 2)

    def test_unchanged_keys_and_preferences_never_call_keychain(self):
        config = self.store.save(self.config).config
        self.backend.calls.clear()
        self.backend.denied = True
        self.store.save_preferences({**config, "language": "zh"})
        self.store.save({**config, "language": "en"}, changed_secrets=set())
        self.assertEqual(self.backend.calls, [])
        self.assertNotIn("api_key", load_config(str(self.path)))

    def test_preferences_keep_failed_legacy_migration_without_saving_new_memory_keys(self):
        save_config(self.config, str(self.path))
        self.store.save_preferences({**self.config, "api_key": "session-only", "language": "zh"})
        self.assertEqual(load_config(str(self.path))["api_key"], "main-dummy")
        self.assertEqual(load_config(str(self.path))["language"], "zh")
        self.assertEqual(self.backend.calls, [])

    def test_foreign_profile_reference_is_never_read(self):
        self.store.save(self.config)
        other = Path(self.temp.name) / "other.json"
        other.write_bytes(self.path.read_bytes())
        self.backend.calls.clear()
        result = SettingsStore(other, self.backend).load()
        self.assertEqual(result.config["api_key"], "")
        self.assertIn("does not match", result.notice)
        self.assertEqual(self.backend.calls, [])

    def test_custom_host_change_drops_old_bound_key_without_sending_it(self):
        config = {**self.config, "provider": "custom", "base_url": "https://first.example/v1"}
        saved = self.store.save(config).config
        changed = {**saved, "base_url": "https://second.example/v1"}
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "OPENROUTER_API_KEY": ""}):
            self.assertEqual(make_model_config(changed).api_key, "")
        result = self.store.save(changed, changed_secrets=set()).config
        self.assertEqual(result["api_key"], "")
        self.assertNotIn("api_key", result["credential_refs"])
        self.assertNotIn(saved["credential_refs"]["api_key"], self.backend.values)

    def test_environment_fallback_is_not_persisted(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "environment-dummy"}):
            config = self.store.load().config
            self.assertEqual(make_model_config(config).api_key, "environment-dummy")
            self.store.save(config)
            self.assertNotIn("environment-dummy", self.path.read_text())
            self.assertEqual(self.backend.values, {})

    def test_unavailable_saved_key_does_not_silently_use_another_account(self):
        self.store.save(self.config)
        self.backend.denied = True
        loaded = self.store.load().config
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "other-account-dummy"}):
            self.assertEqual(make_model_config(loaded).api_key, "")
            self.assertEqual(make_model_config(loaded, paid=True).api_key, "")
            with self.assertRaisesRegex(ValueError, "saved API key"):
                make_client(loaded)
            with self.assertRaisesRegex(ValueError, "saved API key"):
                make_client(loaded, paid=True)
        self.backend.denied = False
        saved = self.store.save(loaded, changed_secrets=set()).config
        self.assertEqual(make_client(saved).config.api_key, "main-dummy")

    def test_groq_environment_key_requires_canonical_https_origin(self):
        config = {**cloud_defaults(), "provider": "custom", "api_key": ""}
        with patch.dict(os.environ, {"GROQ_API_KEY": "environment-dummy"}):
            for address, expected in [
                ("https://api.groq.com/openai/v1", "environment-dummy"),
                ("https://API.GROQ.COM:443/openai/v1", "environment-dummy"),
                ("https://other.example/groq.com", ""), ("https://api.groq.com.other.example/v1", ""),
                ("https://other.example/?host=api.groq.com", ""), ("http://api.groq.com/v1", ""),
                ("https://api.groq.com:8443/v1", ""), ("https://name@api.groq.com/v1", ""),
                ("https://api.groq.com:invalid/v1", ""), ("https://groq.com/v1", "")]:
                with self.subTest(address=address):
                    self.assertEqual(make_model_config({**config, "base_url": address}).api_key, expected)

    def test_unavailable_keychain_keeps_references_when_saving_other_settings(self):
        config = self.store.save(self.config).config
        self.backend.denied = True
        unavailable = self.store.load()
        self.assertEqual(unavailable.config["api_key"], "")
        self.store.save_preferences({**unavailable.config, "language": "zh"})
        with self.assertRaises(CredentialError):
            self.store.save(unavailable.config, changed_secrets=set())
        self.assertEqual(load_config(str(self.path))["credential_refs"], config["credential_refs"])
        self.backend.denied = False
        saved = self.store.save(unavailable.config, changed_secrets=set())
        self.assertEqual(saved.config["api_key"], "main-dummy")
        self.assertEqual(make_model_config(saved.config).api_key, "main-dummy")

    def test_non_macos_legacy_read_and_new_session_only_save(self):
        save_config(self.config, str(self.path))
        store = SettingsStore(self.path, SessionCredentialStore())
        loaded = store.load(migrate=True)
        self.assertIn("Legacy", loaded.notice)
        self.assertEqual(load_config(str(self.path))["api_key"], "main-dummy")
        saved = store.save(loaded.config)
        self.assertIn("session only", saved.notice)
        self.assertEqual(saved.config["api_key"], "main-dummy")
        self.assertNotIn("api_key", load_config(str(self.path)))

    def test_read_only_primary_loading_does_not_migrate_or_read_other_secrets(self):
        config = self.store.save(self.config).config
        before = self.path.read_bytes()
        self.backend.calls.clear()
        self.store.load(fields=("api_key",))
        self.assertEqual(self.backend.calls, [("get", config["credential_refs"]["api_key"])])
        self.assertEqual(self.path.read_bytes(), before)

    def test_native_status_errors_are_safe_and_missing_is_not_silently_success(self):
        with self.assertRaisesRegex(CredentialError, "Unlock"):
            MacOSCredentialStore._check(-25308)
        self.assertEqual(credential_origin({"provider": "custom", "base_url": "https://[::1]:443/v1"}), "https://[::1]:443")
        self.assertNotEqual(credential_origin({"provider": "custom", "base_url": "https://api.groq.com:0/v1"}),
                            credential_origin({"provider": "custom", "base_url": "https://api.groq.com/v1"}))
