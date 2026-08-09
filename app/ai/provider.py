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
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("aai.ai")

_FORBIDDEN_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}

_THINK_BLOCK = re.compile(
    r"<(think|thinking|reasoning|reflection)[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_FENCE_BLOCK = re.compile(r"```(?:json|JSON)?\s*\r?\n?(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")

JSON_REPAIR_USER = (
    "Your previous reply was not valid JSON. Reply again with ONLY one JSON object "
    "matching the requested schema. No markdown fences, no commentary, no thinking tags."
)


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
        # After the first HTTP 400 on response_format, skip it for later calls.
        self._json_mode_ok: bool | None = None

    # ------------------------------------------------------------------ util
    def _payload(self, messages: list[dict[str, str]], *, stream: bool, json_mode: bool, **overrides: Any) -> dict:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.config.temperature),
            "max_tokens": overrides.get("max_tokens", self.config.max_tokens),
            "stream": stream,
        }
        # OpenAI-style JSON mode. Many gateways (OpenRouter for some models,
        # Ollama, older LM Studio) reject this field with HTTP 400.
        if json_mode and self._json_mode_ok is not False:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _json_mode_unsupported(exc: AIProviderError) -> bool:
        """True when the server likely rejected OpenAI-style JSON mode.

        Local stacks (Ollama, LM Studio, llama.cpp) often answer with a bare
        HTTP 400 and no useful body, so any non-retryable 400 while json_mode
        was requested is treated as a signal to retry without response_format.
        """
        return exc.status_code == 400 and not exc.retryable

    @staticmethod
    def _extract(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            parts = []
            for part in content:
                if isinstance(part, dict):
                    parts.append(part.get("text") or part.get("content") or "")
                elif isinstance(part, str):
                    parts.append(part)
            content = "".join(parts)
        if content:
            return str(content)
        # Reasoning models sometimes leave content empty and put the answer elsewhere.
        for key in ("reasoning_content", "reasoning", "text"):
            value = message.get(key) or choice.get(key)
            if value:
                return str(value)
        return ""

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
        return self._chat(messages, json_mode=json_mode, sync=True, **overrides)

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
        return await self._chat(messages, json_mode=json_mode, sync=False, **overrides)

    async def chat_json(self, messages: list[dict[str, str]], **overrides: Any) -> dict[str, Any]:
        """Ask for JSON and recover when the model wraps or mangles the reply.

        Local models and some OpenRouter routes ignore response_format and return
        markdown or prose. A repair turn asks again for JSON only — without
        echoing the bad prose as an assistant message, which tends to continue it.
        """
        raw = await self.chat(messages, json_mode=True, **overrides)
        parsed = parse_json_response(raw)
        if parsed:
            return parsed

        preview = " ".join(str(raw or "").split())[:240]
        log.warning("AI JSON parse failed; requesting a repair turn. preview=%r", preview)

        repair_messages = list(messages) + [
            {
                "role": "user",
                "content": (
                    f"{JSON_REPAIR_USER}\n"
                    "The first character of your reply must be `{`.\n"
                    f"Your previous reply began with: {preview!r}"
                ),
            }
        ]
        repaired = await self.chat(
            repair_messages,
            json_mode=True,
            temperature=0.0,
            max_tokens=overrides.get("max_tokens", max(self.config.max_tokens, 2048)),
        )
        parsed = parse_json_response(repaired)
        if parsed:
            return parsed

        raise AIProviderError(
            "AI returned a response that could not be parsed as JSON"
            + (f" (preview: {preview})" if preview else "")
        )

    def _chat(self, messages: list[dict[str, str]], *, json_mode: bool, sync: bool, **overrides: Any):
        """Post a chat completion, retrying without JSON mode when the server rejects it."""

        async def _async_once(use_json_mode: bool) -> str:
            last_error: Exception | None = None
            for attempt in range(self.config.max_retries + 1):
                try:
                    async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                        response = await client.post(
                            self.config.chat_url,
                            headers=self.config.sanitized_headers(),
                            json=self._payload(messages, stream=False, json_mode=use_json_mode, **overrides),
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

        def _sync_once(use_json_mode: bool) -> str:
            last_error: Exception | None = None
            for attempt in range(self.config.max_retries + 1):
                try:
                    with httpx.Client(timeout=self.config.timeout_seconds) as client:
                        response = client.post(
                            self.config.chat_url,
                            headers=self.config.sanitized_headers(),
                            json=self._payload(messages, stream=False, json_mode=use_json_mode, **overrides),
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

        if sync:
            try:
                result = _sync_once(json_mode and self._json_mode_ok is not False)
                if json_mode and self._json_mode_ok is None:
                    self._json_mode_ok = True
                return result
            except AIProviderError as exc:
                if json_mode and self._json_mode_ok is not False and self._json_mode_unsupported(exc):
                    log.info("provider rejected JSON mode; retrying without response_format")
                    self._json_mode_ok = False
                    return _sync_once(False)
                raise

        async def _async_with_fallback() -> str:
            try:
                result = await _async_once(json_mode and self._json_mode_ok is not False)
                if json_mode and self._json_mode_ok is None:
                    self._json_mode_ok = True
                return result
            except AIProviderError as exc:
                if json_mode and self._json_mode_ok is not False and self._json_mode_unsupported(exc):
                    log.info("provider rejected JSON mode; retrying without response_format")
                    self._json_mode_ok = False
                    return await _async_once(False)
                raise

        return _async_with_fallback()

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


def _strip_noise(text: str) -> str:
    cleaned = _THINK_BLOCK.sub("", text)
    # Unclosed think blocks from truncated replies.
    cleaned = re.sub(r"<(think|thinking|reasoning)[^>]*>.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


def _balanced_json_slice(text: str) -> str:
    """Return the first top-level JSON object or array in ``text``."""
    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj == -1 and start_arr == -1:
        return ""
    if start_obj == -1 or (start_arr != -1 and start_arr < start_obj):
        start, open_ch, close_ch = start_arr, "[", "]"
    else:
        start, open_ch, close_ch = start_obj, "{", "}"

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        ch = text[index]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


def _loads_lenient(candidate: str) -> Any:
    if not candidate or not candidate.strip():
        return None
    text = candidate.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    repaired = (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    repaired = _TRAILING_COMMA.sub(r"\1", repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def parse_json_response(raw: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response.

    Local models frequently wrap JSON in markdown fences, prepend chain-of-thought
    tags, or emit trailing commas. None of those should force a static fallback.
    """
    if not raw:
        return {}
    text = _strip_noise(str(raw))
    candidates: list[str] = []
    for match in _FENCE_BLOCK.finditer(text):
        candidates.append(match.group(1).strip())
    slice_ = _balanced_json_slice(text)
    if slice_:
        candidates.append(slice_)
    candidates.append(text)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        parsed = _loads_lenient(candidate)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            return parsed[0]
    return {}
