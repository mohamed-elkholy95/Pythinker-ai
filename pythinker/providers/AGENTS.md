# AGENTS.md — `pythinker/providers/`

Scoped rules for provider adapters. Root [`../../AGENTS.md`](../../AGENTS.md) applies first.

## Scope

LLM provider adapters, registry, OAuth helpers, and Responses-API support. Provider modules own all per-vendor quirks; runtime/agent code must stay vendor-neutral.

## Rules

- Read upstream docs/source/types before changing provider-backed behavior. Do not assume API defaults, response shapes, retry semantics, or error models.
- Keep provider quirks here or in override maps. Examples: DashScope `enable_thinking`, MiniMax `reasoning_split`, VolcEngine `thinking.type`, Moonshot `temperature=1.0`, Anthropic `cache_control`, Codex OAuth, Copilot OAuth/token exchange.
- `OpenAICompatProvider` owns shared OpenAI-compatible behavior, per-model overrides, retries, role alternation, image stripping, and the Responses-API circuit breaker.
- The Responses circuit breaker has failure/probe timing; flaky live tests may be affected by a provider sitting out after repeated Responses failures.
- Tests should use model strings from `registry.py` unless a test explicitly covers a new registry entry.

## Adding a provider

1. Add provider code or `OpenAICompatProvider` overrides here.
2. Register the provider/model in `registry.py`.
3. Update onboarding/auth/config paths as needed.
4. Add focused provider tests with mocked HTTP boundaries (`tests/providers/`). Never require real secrets.
5. Update `docs/configuration.md` and any onboarding/status docs.

## Verification

```bash
uv run pytest tests/providers/                          # full provider tests
uv run pytest tests/providers/test_openai_responses.py  # focused (Responses API path)
uv run ruff check pythinker/providers --select F401,F841
```
