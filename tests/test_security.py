"""Credential encryption, source validation and AI provider configuration."""

from __future__ import annotations

import pytest

from app.ai.provider import AIProvider, AIProviderError, ProviderSettings, parse_json_response
from app.ingest.source import SourceError, validate_local_path, validate_ref, validate_remote
from app.security import decrypt_secret, encrypt_secret, mask_secret


def test_secret_encryption_roundtrip():
    cipher = encrypt_secret("sk-super-secret-value")
    assert cipher and cipher != "sk-super-secret-value"
    assert decrypt_secret(cipher) == "sk-super-secret-value"
    assert decrypt_secret("not-a-valid-token") == ""
    assert "super" not in mask_secret("sk-super-secret-value")


@pytest.mark.parametrize("ref", ["--upload-pack=evil", "../../etc", "-x", "a b;rm -rf /"])
def test_malicious_refs_rejected(ref):
    with pytest.raises(SourceError):
        validate_ref(ref)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "--upload-pack=evil", "javascript:alert(1)"])
def test_unsupported_remotes_rejected(url):
    with pytest.raises(SourceError):
        validate_remote(url)


def test_local_path_must_exist(tmp_path):
    assert validate_local_path(str(tmp_path)) == tmp_path.resolve()
    with pytest.raises(SourceError):
        validate_local_path(str(tmp_path / "nope"))


def test_provider_headers_drop_forbidden_entries():
    config = ProviderSettings(
        base_url="https://api.example.test/v1",
        model="gpt-test",
        api_key="secret-token",
        headers={"Host": "evil.test", "Content-Length": "0", "X-Tenant": "acme"},
    )
    headers = config.sanitized_headers()
    assert "Host" not in headers and "Content-Length" not in headers
    assert headers["X-Tenant"] == "acme"
    assert headers["Authorization"] == "Bearer secret-token"


def test_provider_redaction_hides_the_key():
    config = ProviderSettings(base_url="https://x/v1", model="m", api_key="secret")
    assert config.redacted()["api_key"] == "***"


def test_provider_url_normalisation():
    assert ProviderSettings(base_url="https://x/v1/", model="m").chat_url == "https://x/v1/chat/completions"
    assert ProviderSettings(base_url="https://x/v1/chat/completions", model="m").chat_url == "https://x/v1/chat/completions"
    assert ProviderSettings(base_url="https://x/v1/chat/completions", model="m").models_url == "https://x/v1/models"


def test_provider_requires_url_and_model():
    with pytest.raises(AIProviderError):
        AIProvider(ProviderSettings(base_url="", model="m"))
    with pytest.raises(AIProviderError):
        AIProvider(ProviderSettings(base_url="https://x/v1", model=""))


def test_parse_json_response_handles_fenced_output():
    assert parse_json_response('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_response('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
    assert parse_json_response("not json at all") == {}
