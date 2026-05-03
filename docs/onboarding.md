# Onboarding

`pythinker onboard` initializes `~/.pythinker/config.json`. It has three flows.

## Quickstart (default on first run)

```bash
pythinker onboard
# or:
pythinker onboard --flow quickstart
```

Walks through: auth choice → credentials → preflight → done.

## Manual (default when a config exists)

```bash
pythinker onboard --flow manual
```

Full questionary menu: provider, channel, agent settings, gateway, tools.

## Non-interactive (scriptable)

```bash
# Plaintext key
pythinker onboard --non-interactive \
  --auth-choice openai-api-key \
  --openai-api-key "$OPENAI_API_KEY"

# Env-var reference — writes "${OPENAI_API_KEY}" into config.json
export OPENAI_API_KEY="sk-..."
pythinker onboard --non-interactive \
  --auth-choice openai-api-key \
  --secret-input-mode ref

# Custom endpoint
pythinker onboard --non-interactive \
  --auth-choice custom-api-key \
  --custom-base-url "https://llm.example.com/v1" \
  --custom-model-id "foo-large" \
  --custom-api-key "$CUSTOM_API_KEY"

# OAuth
pythinker onboard --non-interactive --auth-choice openai-codex
```

## Flags

- `--flow {quickstart,manual}` — override flow selection.
- `--non-interactive` — no prompts; requires `--auth-choice`.
- `--auth-choice <name>` — one of `openai-codex`, `github-copilot`, `openai-api-key`, `openrouter-api-key`, `anthropic-api-key`, `deepseek-api-key`, `gemini-api-key`, `dashscope-api-key`, `mistral-api-key`, `custom-api-key`, `ollama`, `lmstudio`, `skip`.
- `--secret-input-mode {plaintext,ref}` — write key literally or as `${VAR}`.
- `--skip-preflight` — do not ping the model after saving.
- `--accept-risk` — save config even when preflight fails.

See `pythinker onboard --help` for per-provider key flags.
