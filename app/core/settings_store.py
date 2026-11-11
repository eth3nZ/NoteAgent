"""Atomic settings references with independently verified, versioned credentials."""

import hashlib
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.configuration import migrate_config
from app.core.credentials import SECRET_FIELDS, CredentialError, bound_secret, credential_origin, default_credential_store
from app.utils.helpers import load_config, save_config


@dataclass
class SettingsResult:
    config: dict = field(repr=False)
    notice: str = ""


class SettingsStore:
    def __init__(self, path, credentials=None):
        self.path = Path(path).expanduser().resolve()
        self.credentials = credentials if credentials is not None else default_credential_store()
        self._profile = hashlib.sha256(str(self.path).encode()).hexdigest()
        self._lock = threading.RLock()

    def _prefix(self, config, key):
        origin = hashlib.sha256(credential_origin(config, key).encode()).hexdigest()
        return f"{self._profile}:{key}:{origin}:"

    def _valid_ref(self, config, key, reference):
        return (isinstance(reference, str) and reference.startswith(self._prefix(config, key)) and
                len(reference) == len(self._prefix(config, key)) + 32)

    @staticmethod
    def _public(config):
        return {key: value for key, value in config.items() if key not in SECRET_FIELDS and not key.startswith("_")}

    def load(self, *, migrate=False, fields=SECRET_FIELDS):
        with self._lock:
            raw = load_config(str(self.path))
            config = migrate_config(raw)
            refs = raw.get("credential_refs", {})
            refs = refs if isinstance(refs, dict) else {}
            bindings, unavailable = {}, []
            notice = ""
            for key in fields:
                value = raw.get(key, "")
                config[key] = value if isinstance(value, str) else ""
                if config[key]:
                    bindings[key] = credential_origin(config, key)
                    continue
                reference = refs.get(key)
                if not reference:
                    continue
                if not self._valid_ref(config, key, reference):
                    unavailable.append(key)
                    notice = "A saved credential does not match this profile or service. Enter its key in Settings."
                    continue
                try:
                    value = self.credentials.get(reference)
                    if value is None:
                        raise CredentialError("A saved credential is missing from Keychain. Enter it again in Settings.")
                    config[key] = value
                    bindings[key] = credential_origin(config, key)
                except CredentialError as exc:
                    unavailable.append(key)
                    notice = str(exc)
            config["_credential_bindings"] = bindings
            config["_unavailable_credentials"] = tuple(unavailable) if self.credentials.persistent else ()
            legacy = any(isinstance(raw.get(key), str) and raw[key] for key in SECRET_FIELDS)
            if legacy:
                notice = notice or "Legacy keys remain in the local settings file; save Settings to retry secure migration."
                if migrate and not unavailable and set(fields) == set(SECRET_FIELDS) and self.credentials.persistent:
                    try:
                        return self.save(config)
                    except (CredentialError, OSError):
                        notice = "Could not migrate credentials to Keychain. Original settings were retained; unlock Keychain and retry Save."
            return SettingsResult(config, notice)

    def save_preferences(self, config):
        """Never touch Keychain or discard a failed migration's original legacy keys."""
        with self._lock:
            raw = load_config(str(self.path))
            public = self._public(config)
            public["credential_refs"] = raw.get("credential_refs", {})
            for key in SECRET_FIELDS:
                if key in raw:
                    public[key] = raw[key]
            save_config(public, str(self.path))

    def save(self, config, *, changed_secrets=None):
        """Commit new records before references; a failed commit leaves old records intact."""
        with self._lock:
            raw = load_config(str(self.path))
            old = migrate_config(raw)
            old_refs = raw.get("credential_refs", {})
            old_refs = old_refs if isinstance(old_refs, dict) else {}
            refs, created = {}, []
            changed = set(SECRET_FIELDS if changed_secrets is None else changed_secrets)
            result = dict(config)
            bindings = dict(config.get("_credential_bindings", {}))
            persistent = self.credentials.persistent
            try:
                for key in SECRET_FIELDS:
                    same_origin = credential_origin(old, key) == credential_origin(config, key)
                    legacy = bool(raw.get(key))
                    reference = old_refs.get(key)
                    if key not in changed and same_origin and not legacy:
                        if self._valid_ref(config, key, reference):
                            refs[key] = reference
                            if persistent and not result.get(key):
                                value = self.credentials.get(reference)
                                if value is None:
                                    raise CredentialError("A saved credential is missing from Keychain. Enter it again in Settings.")
                                result[key] = value
                                bindings[key] = credential_origin(config, key)
                        continue
                    value = (config.get(key, "") if key in changed else bound_secret(config, key)).strip()
                    if key not in changed and not same_origin:
                        value = ""  # Editing a host cannot silently carry its old key along.
                    result[key] = value
                    bindings.pop(key, None)
                    if value:
                        bindings[key] = credential_origin(config, key)
                    if value and persistent:
                        account = self._prefix(config, key) + uuid.uuid4().hex
                        created.append(account)
                        self.credentials.set(account, value)
                        if self.credentials.get(account) != value:
                            raise CredentialError("Keychain verification failed. Original settings were retained.")
                        refs[key] = account
                public = self._public(config)
                public["credential_refs"] = refs
                save_config(public, str(self.path))
            except Exception:
                for account in created:
                    try:
                        self.credentials.delete(account)
                    except CredentialError:
                        pass
                raise
            notice = ""
            if not persistent and (refs or any(result.get(key) for key in SECRET_FIELDS)):
                notice = "Settings saved. Keys are available for this session only; use environment variables after restarting."
            # Old records are removed only after the JSON commit. Never delete foreign references.
            if persistent:
                for key, account in old_refs.items():
                    if key in SECRET_FIELDS and account != refs.get(key) and self._valid_ref(old, key, account):
                        try:
                            self.credentials.delete(account)
                        except CredentialError:
                            notice = "Settings saved; an unused Keychain record could not be removed."
            result.update(credential_refs=refs, _credential_bindings=bindings)
            result["_unavailable_credentials"] = ()
            return SettingsResult(result, notice)
