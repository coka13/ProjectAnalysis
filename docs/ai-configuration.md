# AI configuration

AI is **optional**. Every capability has a deterministic static-analysis fallback,
so the application is fully functional - in English and Hebrew - with no provider
configured at all.

## What works without a provider

| Capability | Without AI |
| --- | --- |
| Diagram explanation | purpose, description, key components and risks derived from the graph |
| Architecture review | strengths, risks and improvements from metrics |
| Refactoring suggestions | ranked from the detected findings |
| Natural-language diagram requests | keyword and entity matching against the graph |
| Comparison narrative | computed from the architecture diff |
| Scorecard, improvement plan, hotspots | never used AI in the first place |
| Guided fixes | **has no AI path at all** - every rule is a pure text transform |

A fallback result is labelled as such, so you always know whether you are reading
a model's output or the analyser's.

## Connecting a provider

**Settings → AI provider**. Any OpenAI-compatible chat-completions endpoint works:
Ollama, LM Studio, vLLM, llama.cpp's server, OpenAI itself, or a corporate gateway.

| Field | Example |
| --- | --- |
| Base URL | `http://localhost:11434/v1` (Ollama), `http://localhost:1234/v1` (LM Studio) |
| Model | `qwen2.5-coder:14b`, `gpt-4o-mini`, whatever your endpoint serves |
| API key | required by hosted providers, usually blank for a local one |

*Test connection* performs a real request and reports the actual error - status
code included - rather than a generic failure.

## Defaults via environment

`.env` or `AAI_*` environment variables set the starting values:

```ini
AAI_AI_BASE_URL=http://localhost:11434/v1
AAI_AI_MODEL=qwen2.5-coder:14b
AAI_AI_TEMPERATURE=0.2
AAI_AI_MAX_TOKENS=2048
AAI_AI_TIMEOUT_SECONDS=120
AAI_AI_MAX_RETRIES=3
AAI_AI_STREAMING=true
```

A key saved in Settings takes precedence over the environment.

## How the key is stored

The API key is encrypted with Fernet before it touches the database. The key
material lives in `secret.key` inside the data folder, written with `0600`
permissions. It is never logged, never returned by an endpoint, and shown masked
in the UI. `sanitized_headers()` exists specifically so a failed request can be
logged without leaking the `Authorization` header.

## What gets sent

Prompts contain a **summary** of the knowledge graph - names, kinds, modules,
relationships and metrics - not your source files. The one exception is evidence
excerpts you explicitly ask to have explained.

If that is still too much for your policy, leave the provider unconfigured. The
static path is not a degraded mode; it is the default.

## Failure behaviour

A provider error never replaces a result with an error screen. The static
fallback is computed first, the AI call is attempted second, and if it fails the
fallback is returned with the provider's message attached as a warning. Retries
and timeouts are governed by `ai_max_retries` and `ai_timeout_seconds`.
