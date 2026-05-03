---
name: pythinker-provider-test
description: Add or modify a Pythinker LLM provider — covers OpenAI-compatible, Anthropic, Azure OpenAI, OpenAI Codex, and GitHub Copilot. Includes per-model overrides and the Responses-API circuit breaker.
metadata:
  pythinker:
    emoji: "🤖"
    requires:
      bins: ["uv"]
---

# Pythinker Provider Test Companion

Use when adding a new provider, modifying an existing one, or debugging
provider behavior.

## Provider Layout

```
pythinker/providers/
├── base.py                       # LLMProvider ABC — retry/backoff, role alternation, image stripping
├── factory.py                    # build a provider from config
├── registry.py                   # 47 ProviderSpec entries
├── openai_compat_provider.py     # ~1131 LOC; OpenAI-compatible + per-model overrides
├── openai_responses/             # Responses-API helper module
├── anthropic_provider.py         # AnthropicProvider — cache_control markers
├── azure_openai_provider.py      # AzureOpenAIProvider — API version + endpoint routing
├── openai_codex_provider.py      # OpenAICodexProvider — OAuth device flow
├── github_copilot_provider.py    # GitHubCopilotProvider (extends OpenAICompatProvider)
├── local_models.py               # local provider helpers
└── transcription.py              # audio transcription helpers
```

`openai_compat_provider.OpenAICompatProvider` is the workhorse — most
OpenAI-compatible vendors (DashScope, MiniMax, VolcEngine, Moonshot, …)
share it via per-model override maps rather than dedicated subclasses.

## Adding a New Provider

### 1. Implement the `LLMProvider` interface

```python
# pythinker/providers/base.py — LLMProvider ABC
class LLMProvider(ABC):
    async def arun(self, messages, ..., **kwargs) -> LLMResponse: ...
    async def astream(self, messages, ..., **kwargs) -> AsyncIterator[LLMResponse]: ...
    # Retry/backoff, role alternation, image stripping live in the base.
```

If the vendor is OpenAI-compatible, **subclass `OpenAICompatProvider`**
and add per-model overrides instead of building a fresh class.

### 2. Register in `registry.py`

```python
# pythinker/providers/registry.py
ProviderSpec(
    id="myprovider",
    name="My Provider",
    base_url="https://api.example.com/v1",
    ...
)
```

### 3. Onboarding flow (only if user-facing)

- `pythinker/cli/onboard.py` and the `onboard_*.py` flow modules
- `pythinker/config/schema.py` if a new config field is needed

### 4. Document + test

- `docs/configuration.md` — provider config section
- `tests/providers/test_<myprovider>.py`

## Provider Behaviors To Cover

### OpenAI-Compatible Per-Model Overrides
Live in the `OpenAICompatProvider` override map. Don't branch in the
core loop — extend the map.

| Vendor | Override |
|--------|----------|
| DashScope | `enable_thinking` |
| MiniMax | `reasoning_split` |
| VolcEngine | `thinking.type` |
| Moonshot | `temperature=1.0` |

### Responses-API Circuit Breaker
File: `pythinker/providers/openai_compat_provider.py:149-150`

- `_RESPONSES_FAILURE_THRESHOLD = 3`
- `_RESPONSES_PROBE_INTERVAL_S = 300` (5 minutes)
- 3 failures → 5-minute timeout, then a single probe before reopening
- Flaky live tests can trip the breaker; log `_responses_state` before
  blaming the provider

### Auth Flows

| Provider | Mechanism |
|----------|-----------|
| Anthropic | API key via `ANTHROPIC_API_KEY` env or config |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` + endpoint + API version |
| OpenAI Codex | OAuth device flow via `oauth-cli-kit` |
| GitHub Copilot | Device flow → token exchange (extends `OpenAICompatProvider`) |
| Most others | API key via `${VAR}` expansion in `~/.pythinker/config.json` |

## Test Execution

```bash
# Full provider suite
uv run pytest tests/providers/ -v

# One provider
uv run pytest tests/providers/test_openai_compat_provider.py -v

# Cross-cutting (provider + channel)
uv run pytest tests/providers/ tests/channels/

# Lint gate (CI strictest)
uv run ruff check pythinker --select F401,F841
```

## Boundaries

- Provider quirks live in the provider module — core loop stays generic
- Per-model override map: extend, do **not** branch in the core loop
- If a bug names a provider, start in that provider's module; add a
  generic core seam only when multiple providers need it
- Retry/backoff (1, 2, 4 s exponential), role alternation, and image
  stripping live in `LLMProvider` base — don't reimplement per provider
