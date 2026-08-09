"""Resolution of the effective AI provider configuration."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.provider import AIProvider, ProviderSettings
from app.config import settings
from app.models import ProviderConfig
from app.security import decrypt_secret, encrypt_secret, mask_secret

log = logging.getLogger("aai.provider")


def _to_settings(config: ProviderConfig) -> ProviderSettings:
    return ProviderSettings(
        name=config.name,
        base_url=config.base_url,
        model=config.model,
        api_key=decrypt_secret(config.api_key_encrypted),
        headers=dict(config.headers or {}),
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        retry_backoff_seconds=config.retry_backoff_seconds,
        streaming=config.streaming,
    )


def find_config(session: Session) -> ProviderConfig | None:
    """Return the single stored provider configuration, if any."""
    return session.scalar(select(ProviderConfig).order_by(ProviderConfig.updated_at.desc()))


def resolve_settings(session: Session) -> ProviderSettings | None:
    config = find_config(session)
    if config:
        return _to_settings(config)
    if settings.ai_base_url and settings.ai_model:
        return ProviderSettings.from_settings()
    return None


def build_provider(session: Session) -> AIProvider | None:
    """Return a ready provider, or ``None`` when AI is not configured."""
    provider_settings = resolve_settings(session)
    if not provider_settings:
        return None
    try:
        return AIProvider(provider_settings)
    except Exception as exc:  # noqa: BLE001 - misconfiguration must not break analysis
        log.warning("AI provider unavailable: %s", exc)
        return None


def upsert(session: Session, payload: dict) -> ProviderConfig:
    config = find_config(session)
    if config is None:
        config = ProviderConfig()
        session.add(config)

    config.name = payload.get("name", "default")
    config.base_url = payload["base_url"]
    config.model = payload["model"]
    api_key = payload.get("api_key", "")
    if api_key:
        config.api_key_encrypted = encrypt_secret(api_key)
    elif payload.get("clear_api_key"):
        config.api_key_encrypted = ""
    config.headers = payload.get("headers") or {}
    config.temperature = payload.get("temperature", 0.2)
    config.max_tokens = payload.get("max_tokens", 2048)
    config.timeout_seconds = payload.get("timeout_seconds", 120)
    config.max_retries = payload.get("max_retries", 3)
    config.retry_backoff_seconds = payload.get("retry_backoff_seconds", 1.5)
    config.streaming = bool(payload.get("streaming", True))
    session.commit()
    session.refresh(config)
    return config


def to_public(config: ProviderConfig | None, fallback: ProviderSettings | None) -> dict:
    if config:
        return {
            "id": config.id,
            "name": config.name,
            "source": "saved",
            "base_url": config.base_url,
            "model": config.model,
            "api_key_masked": mask_secret(decrypt_secret(config.api_key_encrypted)),
            "headers": dict(config.headers or {}),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout_seconds": config.timeout_seconds,
            "max_retries": config.max_retries,
            "retry_backoff_seconds": config.retry_backoff_seconds,
            "streaming": config.streaming,
        }
    if fallback:
        return {
            "id": "env",
            "name": "environment",
            "source": "environment",
            "base_url": fallback.base_url,
            "model": fallback.model,
            "api_key_masked": mask_secret(fallback.api_key),
            "headers": fallback.headers,
            "temperature": fallback.temperature,
            "max_tokens": fallback.max_tokens,
            "timeout_seconds": fallback.timeout_seconds,
            "max_retries": fallback.max_retries,
            "retry_backoff_seconds": fallback.retry_backoff_seconds,
            "streaming": fallback.streaming,
        }
    return {}
