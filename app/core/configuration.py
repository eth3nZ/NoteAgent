"""Configuration defaults and compatibility with existing user settings."""

import os
from urllib.parse import urlparse

from app.core.cloud import BASE_URL, FREE_MODEL, PAID_MODEL, CloudClient, ModelConfig
from app.core.credentials import bound_secret
from app.core.gemini import GEMINI_BASE, GEMINI_MODEL, GeminiClient


def cloud_defaults():
    return {"provider": "openrouter", "api_key": "", "base_url": BASE_URL,
            "vision_model": FREE_MODEL, "free_providers": "nvidia",
            "paid_enabled": False, "paid_api_key": "", "paid_model": PAID_MODEL,
            "paid_providers": "", "input_price": .035, "output_price": 1.5,
            "paid_image_price": 0.0, "paid_request_price": 0.0,
            "document_limit": .10, "monthly_limit": 1.0, "primary_paid": False,
            "gemini_api_key": "", "gemini_model": GEMINI_MODEL, "gemini_free_project": False}


def migrate_config(saved):
    config = {**cloud_defaults(), **saved}
    if saved and saved.get("provider") not in {"openrouter", "gemini"}:
        # Preserve existing endpoint/key fields rather than silently redirecting credentials.
        config["provider"] = "custom"
        config["free_providers"] = saved.get("free_providers", "")
    if config.get("vision_model") == "openrouter/free":
        config["vision_model"] = FREE_MODEL
    return config


def make_model_config(config, paid=False):
    if not paid and config.get('provider') == 'gemini':
        blocked = 'gemini_api_key' in config.get('_unavailable_credentials', ())
        key = bound_secret(config, 'gemini_api_key') or ('' if blocked else os.environ.get('GEMINI_API_KEY', ''))
        return ModelConfig(api_key=key, base_url=GEMINI_BASE, model=config.get('gemini_model', GEMINI_MODEL))
    key = bound_secret(config, "paid_api_key" if paid else "api_key")
    unavailable = config.get("_unavailable_credentials", ())
    blocked = ("paid_api_key" if paid else "api_key") in unavailable
    if paid and not key and config.get("provider") in {"openrouter", "gemini"} and not blocked:
        blocked = "api_key" in unavailable
        if not blocked:
            key = bound_secret(config, "api_key")
    if not key and not blocked:
        if paid or config.get("provider") == "openrouter":
            key = os.environ.get("OPENROUTER_API_KEY", "")
        else:
            try:
                url = urlparse(config.get("base_url", ""))
                if (url.scheme == "https" and url.hostname == "api.groq.com" and
                        url.port in {None, 443} and not url.username and not url.password and
                        not url.query and not url.fragment):
                    key = os.environ.get("GROQ_API_KEY", "")
            except ValueError:
                pass
    providers = config.get("paid_providers" if paid else "free_providers", "")
    paid_route = paid or bool(config.get('primary_paid') and config.get('provider') == 'openrouter')
    return ModelConfig(
        api_key=key, base_url=BASE_URL if paid or config.get("provider") == "openrouter" else config.get("base_url", BASE_URL),
        model=config.get("paid_model", PAID_MODEL) if paid else config.get("vision_model", FREE_MODEL),
        providers=tuple(p.strip() for p in providers.split(",") if p.strip()),
        paid=paid_route,
        input_price=float(config.get("input_price", .035)), output_price=float(config.get("output_price", 1.5)),
        image_price=float(config.get("paid_image_price", 0)) if paid_route else 0,
        request_price=float(config.get("paid_request_price", 0)) if paid_route else 0)


def make_client(config, paid=False, transport=None):
    unavailable = config.get("_unavailable_credentials", ())
    field = "paid_api_key" if paid else "gemini_api_key" if config.get('provider') == 'gemini' else "api_key"
    if field in unavailable or (paid and not bound_secret(config, field) and
                               config.get("provider") in {"openrouter", "gemini"} and "api_key" in unavailable):
        raise ValueError("A saved API key is unavailable. Unlock Keychain and choose Settings → Save, or enter its key again.")
    if not paid and config.get('provider') == 'gemini':
        import hashlib
        reference = config.get('credential_refs', {}).get('gemini_api_key')
        model_config = make_model_config(config)
        session = config.get('_gemini_free_session') and config.get('_gemini_free_session_key') == hashlib.sha256(model_config.api_key.encode()).hexdigest()
        confirmed = bool(session or (reference and config.get('gemini_free_project') and
                         config.get('gemini_confirmation_ref') == reference))
        return GeminiClient(model_config, transport=transport, confirmed_free_project=confirmed)
    return CloudClient(make_model_config(config, paid), transport=transport)
