"""Configurable OpenAI-compatible AI provider.

Works with any endpoint exposing ``/chat/completions``: OpenAI, Azure OpenAI,
Ollama, LM Studio, vLLM, llama.cpp server, or a self-hosted enterprise gateway.
Nothing about the provider is hardcoded - URL, model, headers, credentials,
sampling parameters, timeouts, retries and streaming are all configuration.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("aai.ai")

_FORBIDDEN_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}


class AIProviderError(RuntimeError):
    """Raised when the configured AI provider cannot fulfil a request."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class ProviderSettings:
    base_url: str
    model: str
    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout_seconds: int = 120
    max_retries: int = 3
    retry_backoff_seconds: float = 1.5
    streaming: bool = True
    name: str = "default"

    @classmethod
    def from_settings(cls) -> "ProviderSettings":
        return cls(
            base_url=settings.ai_base_url,
            model=settings.ai_model,
            api_key=settings.ai_api_key,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_seconds=settings.ai_timeout_seconds,
            max_retries=settings.ai_max_retries,
            retry_backoff_seconds=1.5,
            streaming=settings.ai_streaming,
        )

    @property
    def chat_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    @property
    def models_url(self) -> str:
        base = self.base_url.rstrip("/")
        base = base.removesuffix("/chat/completions")
        return f"{base}/models"

    def sanitized_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        for key, value in (self.headers or {}).items():
            if key.lower() in _FORBIDDEN_HEADERS:
                continue
            headers[str(key)[:100]] = str(value)[:2000]
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
            # Azure OpenAI style deployments use a dedicated header.
            if "azure" in self.base_url.lower():
                headers.setdefault("api-key", self.api_key)
        return headers

    def redacted(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


class AIProvider:
    """Thin, resilient client around an OpenAI-compatible chat completions API."""

    def __init__(self, config: ProviderSettings) -> None:
        if not config.base_url:
            raise AIProviderError("AI provider base URL is not configured")
        if not config.model:
            raise AIProviderError("AI provider model is not configured")
        self.config = config

    # ------------------------------------------------------------------ util
    def _payload(self, messages: list[dict[str, str]], *, stream: bool, json_mode: bool, **overrides: Any) -> dict:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.config.temperature),
            "max_tokens": overrides.get("max_tokens", self.config.max_tokens),
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _extract(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content or choices[0].get("text", "") or ""

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retryable = response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}
        detail = response.text[:400].replace("\n", " ")
        raise AIProviderError(
            f"AI provider returned HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
            retryable=retryable,
        )

    # ------------------------------------------------------------------ sync
    def chat_sync(self, messages: list[dict[str, str]], *, json_mode: bool = False, **overrides: Any) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds) as client:
                    response = client.post(
                        self.config.chat_url,
                        headers=self.config.sanitized_headers(),
                        json=self._payload(messages, stream=False, json_mode=json_mode, **overrides),
                    )
                    self._raise_for_status(response)
                    return self._extract(response.json())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = AIProviderError(f"AI provider is unreachable: {exc}", retryable=True)
            except AIProviderError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            except json.JSONDecodeError as exc:
                raise AIProviderError("AI provider returned a malformed response") from exc
            if attempt < self.config.max_retries:
                time.sleep(self.config.retry_backoff_seconds * (2**attempt))
        raise last_error or AIProviderError("AI request failed")

    def stream_sync(self, messages: list[dict[str, str]], **overrides: Any) -> Iterator[str]:
        with httpx.Client(timeout=self.config.timeout_seconds) as client:
            with client.stream(
                "POST",
                self.config.chat_url,
                headers=self.config.sanitized_headers(),
                json=self._payload(messages, stream=True, json_mode=False, **overrides),
            ) as response:
                self._raise_for_status(response)
                for line in response.iter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk:
                        yield chunk

    # ----------------------------------------------------------------- async
    async def chat(self, messages: list[dict[str, str]], *, json_mode: bool = False, **overrides: Any) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(
                        self.config.chat_url,
                        headers=self.config.sanitized_headers(),
                        json=self._payload(messages, stream=False, json_mode=json_mode, **overrides),
                    )
                    self._raise_for_status(response)
                    return self._extract(response.json())
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = AIProviderError(f"AI provider is unreachable: {exc}", retryable=True)
            except AIProviderError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            except json.JSONDecodeError as exc:
                raise AIProviderError("AI provider returned a malformed response") from exc
            if attempt < self.config.max_retries:
                await asyncio.sleep(self.config.retry_backoff_seconds * (2**attempt))
        raise last_error or AIProviderError("AI request failed")

    async def stream(self, messages: list[dict[str, str]], **overrides: Any) -> AsyncIterator[str]:
        async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
            async with client.stream(
                "POST",
                self.config.chat_url,
                headers=self.config.sanitized_headers(),
                json=self._payload(messages, stream=True, json_mode=False, **overrides),
            ) as response:
                self._raise_for_status(response)
                async for line in response.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk:
                        yield chunk

    # ------------------------------------------------------------------ test
    async def health(self) -> dict[str, Any]:
        started = time.perf_counter()
        sample = await self.chat(
            [
                {"role": "system", "content": "You are a connectivity probe. Reply with the single word: ready."},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=16,
            temperature=0.0,
        )
        return {
            "ok": True,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "model": self.config.model,
            "sample": sample.strip()[:120],
        }


def _parse_sse_line(line: str) -> str:
    if not line or not line.startswith("data:"):
        return ""
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return ""
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return ""
    for choice in parsed.get("choices", []):
        delta = choice.get("delta") or {}
        content = delta.get("content")
        if content:
            return content
        if choice.get("text"):
            return choice["text"]
    return ""


def parse_json_response(raw: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response."""
    if not raw:
        return {}
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
