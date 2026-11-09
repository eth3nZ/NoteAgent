"""Configuration defaults and compatibility with existing user settings."""

import os

from app.core.cloud import BASE_URL, FREE_MODEL, PAID_MODEL, CloudClient, ModelConfig


def cloud_defaults():
    return {"provider": "openrouter", "api_key": "", "base_url": BASE_URL,
            "vision_model": FREE_MODEL, "free_providers": "nvidia",
            "paid_enabled": False, "paid_api_key": "", "paid_model": PAID_MODEL,
            "paid_providers": "", "input_price": .035, "output_price": 1.5,
            "paid_image_price": 0.0, "paid_request_price": 0.0,
            "document_limit": .10, "monthly_limit": 1.0}


def migrate_config(saved):
    config = {**cloud_defaults(), **saved}
    if saved and saved.get("provider") != "openrouter":
        # Preserve existing endpoint/key fields rather than silently redirecting credentials.
        config["provider"] = "custom"
        config["free_providers"] = saved.get("free_providers", "")
    if config.get("vision_model") == "openrouter/free":
        config["vision_model"] = FREE_MODEL
    return config


def make_model_config(config, paid=False):
    key = (config.get("paid_api_key") if paid else config.get("api_key")) or ""
    if paid and not key and config.get("provider") == "openrouter":
        key = config.get("api_key", "")
    if not key:
        if paid or config.get("provider") == "openrouter":
            key = os.environ.get("OPENROUTER_API_KEY", "")
        elif "groq.com" in config.get("base_url", ""):
            key = os.environ.get("GROQ_API_KEY", "")
    providers = config.get("paid_providers" if paid else "free_providers", "")
    return ModelConfig(
        api_key=key, base_url=BASE_URL if paid or config.get("provider") == "openrouter" else config.get("base_url", BASE_URL),
        model=config.get("paid_model", PAID_MODEL) if paid else config.get("vision_model", FREE_MODEL),
        providers=tuple(p.strip() for p in providers.split(",") if p.strip()), paid=paid,
        input_price=float(config.get("input_price", .035)), output_price=float(config.get("output_price", 1.5)),
        image_price=float(config.get("paid_image_price", 0)) if paid else 0,
        request_price=float(config.get("paid_request_price", 0)) if paid else 0)


def make_client(config, paid=False, transport=None):
    return CloudClient(make_model_config(config, paid), transport=transport)
