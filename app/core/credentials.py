"""Small native credential boundary. Secrets never travel through process arguments."""

import ctypes
import sys
from typing import Protocol
from urllib.parse import urlparse


SECRET_FIELDS = ("api_key", "paid_api_key", "notion_token")


class CredentialError(RuntimeError):
    """Safe to display: messages contain neither credentials nor native error text."""


class CredentialStore(Protocol):
    persistent: bool

    def get(self, account: str) -> str | None: ...
    def set(self, account: str, secret: str) -> None: ...
    def delete(self, account: str) -> None: ...


def credential_origin(config, field="api_key"):
    if field == "notion_token":
        return "https://api.notion.com:443"
    if field == "paid_api_key" or config.get("provider") == "openrouter":
        return "https://openrouter.ai:443"
    try:
        url = urlparse(config.get("base_url", ""))
        if not url.hostname or url.username or url.password or url.scheme not in {"http", "https"}:
            return "invalid"
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        hostname = f"[{url.hostname.lower()}]" if ":" in url.hostname else url.hostname.lower()
        return f"{url.scheme}://{hostname}:{port}"
    except ValueError:
        return "invalid"


def bound_secret(config, field):
    binding = config.get("_credential_bindings", {}).get(field)
    if binding is not None and binding != credential_origin(config, field):
        return ""
    return config.get(field, "") or ""


class SessionCredentialStore:
    """Unsupported platforms keep newly entered credentials in process memory only."""

    persistent = False

    def get(self, account):
        raise CredentialError("Secure credential storage is unavailable on this platform.")

    def set(self, account, secret):
        raise CredentialError("Secure credential storage is unavailable on this platform.")

    def delete(self, account):
        raise CredentialError("Secure credential storage is unavailable on this platform.")


class MacOSCredentialStore:
    """Login Keychain generic passwords through Security/CoreFoundation, loaded lazily.

    Interaction is disabled: a locked or inaccessible Keychain fails promptly instead
    of holding a worker behind an authentication dialog. Unlock it and retry Save.
    """

    persistent = True
    SERVICE = "org.noteagent.credentials"

    def _load(self):
        if hasattr(self, "_security"):
            return
        try:
            security = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            pointer = ctypes.c_void_p
            signatures = {
                "CFStringCreateWithCString": ([pointer, ctypes.c_char_p, ctypes.c_uint32], pointer),
                "CFDataCreate": ([pointer, pointer, ctypes.c_long], pointer),
                "CFDataGetLength": ([pointer], ctypes.c_long),
                "CFDataGetBytePtr": ([pointer], pointer),
                "CFDictionaryCreate": ([pointer, ctypes.POINTER(pointer), ctypes.POINTER(pointer),
                                        ctypes.c_long, pointer, pointer], pointer),
                "CFRelease": ([pointer], None),
            }
            for name, (args, result) in signatures.items():
                function = getattr(cf, name)
                function.argtypes, function.restype = args, result
            for name in ("SecItemAdd", "SecItemCopyMatching", "SecItemUpdate"):
                function = getattr(security, name)
                function.argtypes = [pointer, ctypes.POINTER(pointer)] if name != "SecItemUpdate" else [pointer, pointer]
                function.restype = ctypes.c_int32
            security.SecItemDelete.argtypes = [pointer]
            security.SecItemDelete.restype = ctypes.c_int32
            self._security, self._cf = security, cf
        except (OSError, AttributeError):
            raise CredentialError("macOS Keychain could not be opened. Credentials were not changed.") from None

    def _constant(self, name):
        library = self._cf if name.startswith("kCF") else self._security
        return ctypes.c_void_p.in_dll(library, name).value

    def _dictionary(self, account, *, secret=None, read=False, update=False):
        self._load()
        allocated = []
        try:
            def string(value):
                result = self._cf.CFStringCreateWithCString(None, value.encode("utf-8"), 0x08000100)
                if not result:
                    raise CredentialError("Keychain request could not be prepared.")
                allocated.append(result)
                return result
            pairs = {}
            if not update:
                pairs = {"kSecClass": self._constant("kSecClassGenericPassword"),
                         "kSecAttrService": string(self.SERVICE), "kSecAttrAccount": string(account),
                         "kSecUseAuthenticationUI": self._constant("kSecUseAuthenticationUIFail")}
            if secret is not None:
                data = secret.encode("utf-8")
                buffer = ctypes.create_string_buffer(data)
                value = self._cf.CFDataCreate(None, buffer, len(data))
                if not value:
                    raise CredentialError("Keychain request could not be prepared.")
                allocated.append(value)
                pairs["kSecValueData"] = value
            if read:
                pairs.update(kSecReturnData=self._constant("kCFBooleanTrue"),
                             kSecMatchLimit=self._constant("kSecMatchLimitOne"))
            keys = (ctypes.c_void_p * len(pairs))(*(self._constant(key) for key in pairs))
            values = (ctypes.c_void_p * len(pairs))(*pairs.values())
            callbacks = lambda name: ctypes.addressof(ctypes.c_byte.in_dll(self._cf, name))
            result = self._cf.CFDictionaryCreate(None, keys, values, len(pairs),
                callbacks("kCFTypeDictionaryKeyCallBacks"), callbacks("kCFTypeDictionaryValueCallBacks"))
            if not result:
                raise CredentialError("Keychain request could not be prepared.")
            return result
        finally:
            for value in reversed(allocated):
                self._cf.CFRelease(value)

    @staticmethod
    def _check(status):
        if status:
            raise CredentialError(f"Keychain access failed ({status}). Unlock your login Keychain and retry; saved credentials were retained.")

    def get(self, account):
        query = self._dictionary(account, read=True)
        value = ctypes.c_void_p()
        try:
            status = self._security.SecItemCopyMatching(query, ctypes.byref(value))
            if status == -25300:  # errSecItemNotFound
                return None
            self._check(status)
            if not value.value:
                raise CredentialError("Keychain returned no credential data.")
            try:
                return ctypes.string_at(self._cf.CFDataGetBytePtr(value), self._cf.CFDataGetLength(value)).decode("utf-8")
            except UnicodeError:
                raise CredentialError("Keychain returned invalid credential data.") from None
        finally:
            if value.value:
                self._cf.CFRelease(value)
            self._cf.CFRelease(query)

    def set(self, account, secret):
        item = self._dictionary(account, secret=secret)
        try:
            status = self._security.SecItemAdd(item, None)
        finally:
            self._cf.CFRelease(item)
        if status == -25299:  # errSecDuplicateItem
            query = self._dictionary(account)
            try:
                changes = self._dictionary(account, secret=secret, update=True)
                try:
                    status = self._security.SecItemUpdate(query, changes)
                finally:
                    self._cf.CFRelease(changes)
            finally:
                self._cf.CFRelease(query)
        self._check(status)

    def delete(self, account):
        query = self._dictionary(account)
        try:
            status = self._security.SecItemDelete(query)
        finally:
            self._cf.CFRelease(query)
        if status != -25300:
            self._check(status)


def default_credential_store():
    return MacOSCredentialStore() if sys.platform == "darwin" else SessionCredentialStore()
