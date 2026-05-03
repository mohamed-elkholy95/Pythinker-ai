# Configuration

Config file: `~/.pythinker/config.json`

> [!NOTE]
> If your config file is older than the current schema, you can refresh it without overwriting your existing values:
> run `pythinker onboard`, then answer `N` when asked whether to overwrite the config.
> pythinker will merge in missing default fields and keep your current settings.

## Environment Variables for Secrets

Instead of storing secrets directly in `config.json`, you can use `${VAR_NAME}` references that are resolved from environment variables at startup:

```json
{
  "channels": {
    "telegram": { "token": "${TELEGRAM_TOKEN}" },
    "email": {
      "imapPassword": "${IMAP_PASSWORD}",
      "smtpPassword": "${SMTP_PASSWORD}"
    }
  },
  "providers": {
    "groq": { "apiKey": "${GROQ_API_KEY}" }
  }
}
```

For **systemd** deployments, use `EnvironmentFile=` in the service unit to load variables from a file that only the deploying user can read:

```ini
# /etc/systemd/system/pythinker.service (excerpt)
[Service]
EnvironmentFile=/home/youruser/pythinker_secrets.env
User=pythinker
ExecStart=...
```

```bash
# /home/youruser/pythinker_secrets.env (mode 600, owned by youruser)
TELEGRAM_TOKEN=your-token-here
IMAP_PASSWORD=your-password-here
```

## WebUI Admin Dashboard

When the WebSocket channel serves the embedded WebUI, the sidebar includes an
Admin view for local operators. It uses the same short-lived WebUI bootstrap
token as chat, so treat it as a trusted local dashboard rather than an
internet-facing admin console. If you expose the WebUI over a network, put it
behind real authentication such as a reverse proxy, VPN, or SSO gateway.

The WebUI channel is enabled by default on `127.0.0.1:8765`, so
`pythinker gateway` serves the browser UI for the local machine without opening
an external network listener. Keep `channels.websocket.host` on loopback for
local-only admin access. Binding it to a non-loopback address requires TLS or
the explicit `channels.websocket.allowInsecureRemote=true` development opt-in.

The dashboard shows:

- Runtime state: version, workspace path, config path, gateway/API/WebSocket
  listeners, enabled channel adapters, active provider, and active model.
- Sessions: all channel sessions in the workspace, including non-WebSocket
  sessions that the normal chat sidebar intentionally hides.
- Usage: last-turn provider token counts and a workspace-scoped usage ledger at
  `<workspace>/admin/usage.jsonl`. Pythinker records normalized token counts
  after each completed turn. Monetary cost is not calculated unless a future
  pricing table is configured.
- Models: configured model, alternate models, and provider recommendations.
- Config: a redacted config tree plus write controls for full workspace config.

### Admin Dashboard Config Workbench

The Admin Dashboard Config tab renders a service-aware Config Workbench backed
by the same runtime config stored in `~/.pythinker/config.json`. Fields modeled
by Pythinker's Pydantic schema render as typed controls, dynamic/plugin-backed
maps render as dynamic key/value editors, and secret-backed paths use a
write-only replacement modal. Applying changes uses the existing admin config
RPCs and reports `restart_required=true` for persisted edits.

Secret values are never sent back to the browser. Fields whose path includes
`apiKey`, `token`, `secret`, `password`, `credential`, `oauth`, or
`extraHeaders` are shown as `********`. To change a secret, replace it with a
new value; leaving a secret blank means “leave unchanged” in the UI.

Config writes are schema-validated before saving. Pythinker creates a
timestamped `config.json.bak.<timestamp>` backup next to the config file before
each successful dashboard or CLI `pythinker config set/unset` write. Most
changes require a gateway/API restart; model/provider edits use the existing
runtime hot-swap path where available, but the dashboard still labels config
edits as restart-required unless explicitly proven hot-reloadable.

The Workbench also includes operational checks for local administrators:

- **Backup restore:** the Backups panel lists recent `config.json.bak.*`
  versions. Restoring a backup requires confirmation, first writes a safety
  backup of the current config, then swaps the selected backup into place. The
  result is always shown as restart-required because restored config may affect
  listeners, channels, providers, or tool startup state.
- **Test bind:** the gateway/API network panel can attempt a loopback bind for
  the configured host and port. It reports coarse OS errors such as
  `EADDRINUSE` so you can detect busy local ports without starting a second
  gateway. It is intended for loopback/local diagnostics, not remote port
  scanning.
- **Channel validation:** channel cards can run config-only checks such as
  known channel key, config shape, enabled flag, required-secret presence,
  `allow_from` posture, and local dependency presence. These checks do not log
  into chat platforms or send test messages.
- **MCP and browser probes:** the Tools panel can ask a configured MCP server
  for its tool list and can inspect browser-pool status such as active context
  count. Probe output is display-safe: secret environment values and headers are
  not rendered in the browser.
- **Env/default badges:** fields backed by `${VAR}` references show only the
  variable name, and default hints render as `default: <value>` only when the
  current value differs from the schema default.
- **Channel uptime:** channel cards include a compact 60-tick recent uptime
  sparkline when runtime samples are available.

## Providers

> [!TIP]
> - **Voice transcription**: Voice messages (Telegram, WhatsApp) are automatically transcribed using Whisper. By default Groq is used (free tier). Set `"transcriptionProvider": "openai"` under `channels` to use OpenAI Whisper instead, and optionally set `"transcriptionLanguage": "en"` (or another ISO-639-1 code) for more accurate transcription. The API key is picked from the matching provider config.
> - **MiniMax Coding Plan**: Exclusive discount links for the pythinker community: [Overseas](https://platform.minimax.io/subscribe/coding-plan?code=9txpdXw04g&source=link) · [Mainland China](https://platform.minimaxi.com/subscribe/token-plan?code=GILTJpMTqZ&source=link)
> - **MiniMax wizard**: Run `pythinker onboard`, pick `[P] LLM Provider` → `MiniMax`. The wizard asks region (Global / Mainland China), opens the [token plan portal](https://platform.minimax.io/user-center/payment/token-plan) in your browser, and lets you pick **endpoint flavor** (`OpenAI-compatible` / `Anthropic-compatible` / **Both — recommended**) and **plan tier** (Standard → `MiniMax-M2.7` / Highspeed → `MiniMax-M2.7-highspeed`). Pick `Both` to wire `providers.minimax` *and* `providers.minimax_anthropic` with the same key.
> - **MiniMax thinking modes**: `providers.minimax` supports thinking via `reasoningEffort` — pythinker injects `extra_body={"reasoning_split": true}` automatically (`pythinker/providers/openai_compat_provider.py:415`). `providers.minimax_anthropic` exposes **native Anthropic thinking blocks** (visible reasoning content in the response). Pick `Both` in the wizard to keep either mode reachable at runtime.
> - **VolcEngine / BytePlus Coding Plan**: Use dedicated providers `volcengineCodingPlan` or `byteplusCodingPlan` instead of the pay-per-use `volcengine` / `byteplus` providers.
> - **Zhipu Coding Plan**: If you're on Zhipu's coding plan, set `"apiBase": "https://open.bigmodel.cn/api/coding/paas/v4"` in your zhipu provider config.
> - **Alibaba Cloud BaiLian**: If you're using Alibaba Cloud BaiLian's OpenAI-compatible endpoint, set `"apiBase": "https://dashscope.aliyuncs.com/compatible-mode/v1"` in your dashscope provider config.
> - **Step Fun (Mainland China)**: If your API key is from Step Fun's mainland China platform (stepfun.com), set `"apiBase": "https://api.stepfun.com/v1"` in your stepfun provider config.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `custom` | Any OpenAI-compatible endpoint | — |
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `volcengine` | LLM (VolcEngine, pay-per-use) | [Coding Plan](https://www.volcengine.com/activity/codingplan?utm_campaign=pythinker&utm_content=pythinker&utm_medium=devrel&utm_source=OWO&utm_term=pythinker) · [volcengine.com](https://www.volcengine.com) |
| `byteplus` | LLM (VolcEngine international, pay-per-use) | [Coding Plan](https://www.byteplus.com/en/activity/codingplan?utm_campaign=pythinker&utm_content=pythinker&utm_medium=devrel&utm_source=OWO&utm_term=pythinker) · [byteplus.com](https://www.byteplus.com) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `azure_openai` | LLM (Azure OpenAI) | [portal.azure.com](https://portal.azure.com) |
| `openai` | LLM + Voice transcription (Whisper) | [platform.openai.com](https://platform.openai.com) |
| `deepseek` | LLM (DeepSeek direct) | [platform.deepseek.com](https://platform.deepseek.com) |
| `groq` | LLM + Voice transcription (Whisper, default) | [console.groq.com](https://console.groq.com) |
| `minimax` | LLM (MiniMax direct) | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `minimax_anthropic` | LLM (MiniMax Anthropic-compatible endpoint, thinking mode) | [platform.minimaxi.com](https://platform.minimaxi.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `aihubmix` | LLM (API gateway, access to all models) | [aihubmix.com](https://aihubmix.com) |
| `siliconflow` | LLM (SiliconFlow) | [siliconflow.cn](https://siliconflow.cn) |
| `dashscope` | LLM (Qwen) | [dashscope.console.aliyun.com](https://dashscope.console.aliyun.com) |
| `moonshot` | LLM (Moonshot/Kimi) | [platform.moonshot.cn](https://platform.moonshot.cn) |
| `zhipu` | LLM (Zhipu GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `mimo` | LLM (MiMo) | [platform.xiaomimimo.com](https://platform.xiaomimimo.com) |
| `ollama` | LLM (local, Ollama) | — |
| `lm_studio` | LLM (local, LM Studio) | — |
| `mistral` | LLM | [docs.mistral.ai](https://docs.mistral.ai/) |
| `stepfun` | LLM (Step Fun) | [platform.stepfun.com](https://platform.stepfun.com) |
| `ovms` | LLM (local, OpenVINO Model Server) | [docs.openvino.ai](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) |
| `vllm` | LLM (local, any OpenAI-compatible server) | — |
| `openai_codex` | LLM (Codex, OAuth) | `pythinker provider login openai-codex` |
| `github_copilot` | LLM (GitHub Copilot, OAuth) | `pythinker provider login github-copilot` |
| `qianfan` | LLM (Baidu Qianfan) | [cloud.baidu.com](https://cloud.baidu.com/doc/qianfan/s/Hmh4suq26) |


<details>
<summary><b>OpenAI Codex (OAuth)</b></summary>

Codex uses OAuth instead of API keys. Requires a ChatGPT Plus or Pro account.
No `providers.openaiCodex` block is needed in `config.json`; `pythinker provider login` stores the OAuth session outside config.

**1. Login:**
```bash
pythinker provider login openai-codex
```

**2. Set model** (merge into `~/.pythinker/config.json`):
```json
{
  "agents": {
    "defaults": {
      "model": "openai-codex/gpt-5.5-mini"
    }
  }
}
```

**3. Chat:**
```bash
pythinker agent -m "Hello!"

# Target a specific workspace/config locally
pythinker agent -c ~/.pythinker-telegram/config.json -m "Hello!"

# One-off workspace override on top of that config
pythinker agent -c ~/.pythinker-telegram/config.json -w /tmp/pythinker-telegram-test -m "Hello!"
```

> Docker users: use `docker run -it` for interactive OAuth login.

</details>


<details>
<summary><b>GitHub Copilot (OAuth)</b></summary>

GitHub Copilot uses OAuth instead of API keys. Requires a [GitHub account with a plan](https://github.com/features/copilot/plans) configured.
No `providers.githubCopilot` block is needed in `config.json`; `pythinker provider login` stores the OAuth session outside config.

**1. Login:**
```bash
pythinker provider login github-copilot
```

**2. Set model** (merge into `~/.pythinker/config.json`):
```json
{
  "agents": {
    "defaults": {
      "model": "github-copilot/gpt-4.1"
    }
  }
}
```

**3. Chat:**
```bash
pythinker agent -m "Hello!"

# Target a specific workspace/config locally
pythinker agent -c ~/.pythinker-telegram/config.json -m "Hello!"

# One-off workspace override on top of that config
pythinker agent -c ~/.pythinker-telegram/config.json -w /tmp/pythinker-telegram-test -m "Hello!"
```

> Docker users: use `docker run -it` for interactive OAuth login.

</details>

<details>
<summary><b>Custom Provider (Any OpenAI-compatible API)</b></summary>

Connects directly to any OpenAI-compatible endpoint — llama.cpp, Together AI, Fireworks, Azure OpenAI, or any self-hosted server. Model name is passed as-is.

```json
{
  "providers": {
    "custom": {
      "apiKey": "your-api-key",
      "apiBase": "https://api.your-provider.com/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "your-model-name"
    }
  }
}
```

> For local servers that don't require authentication, set `apiKey` to `null`.
>
> `custom` is the right choice for providers that expose an OpenAI-compatible **chat completions** API. It does **not** force third-party endpoints onto the OpenAI/Azure **Responses API**.
>
> If your proxy or gateway is specifically Responses-API-compatible, use the `azure_openai` provider shape instead and point `apiBase` at that endpoint:
>
> ```json
> {
>   "providers": {
>     "azure_openai": {
>       "apiKey": "your-api-key",
>       "apiBase": "https://api.your-provider.com",
>       "defaultModel": "your-model-name"
>     }
>   },
>   "agents": {
>     "defaults": {
>       "provider": "azure_openai",
>       "model": "your-model-name"
>     }
>   }
> }
> ```
>
> In short: **chat-completions-compatible endpoint → `custom`**; **Responses-compatible endpoint → `azure_openai`**.

</details>

<details>
<summary><b>Ollama (local)</b></summary>

Run a local model with Ollama, then add to config:

**1. Start Ollama** (example):
```bash
ollama run llama3.2
```

**2. Add to config** (partial — merge into `~/.pythinker/config.json`):
```json
{
  "providers": {
    "ollama": {
      "apiBase": "http://localhost:11434"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "llama3.2"
    }
  }
}
```

> `provider: "auto"` also works when `providers.ollama.apiBase` is configured, but setting `"provider": "ollama"` is the clearest option.

</details>

<details>
<summary><b>LM Studio (local)</b></summary>

[LM Studio](https://lmstudio.ai/) provides a local OpenAI-compatible server for running LLMs. Download models through the LM Studio UI, then start the local server.

**1. Start LM Studio server:**
- Launch LM Studio
- Go to the "Local Server" tab
- Load a model (e.g., Llama, Mistral, Qwen)
- Click "Start Server" (default port: 1234)

**2. Add to config** (partial — merge into `~/.pythinker/config.json`):
```json
{
  "providers": {
    "lm_studio": {
      "apiKey": null,
      "apiBase": "http://localhost:1234/v1"
    }
  },
  "agents": {
    "defaults": {
      "provider": "lm_studio",
      "model": "local-model"
    }
  }
}
```

> **Note:** Set `apiKey` to `null` for LM Studio since it runs locally and doesn't require authentication. The model name should match what's shown in the LM Studio UI.
> `provider: "auto"` also works when `providers.lm_studio.apiBase` is configured, but setting `"provider": "lm_studio"` is the clearest option.

</details>

<details>
<summary><b>OpenVINO Model Server (local / OpenAI-compatible)</b></summary>

Run LLMs locally on Intel GPUs using [OpenVINO Model Server](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html). OVMS exposes an OpenAI-compatible API at `/v3`.

> Requires Docker and an Intel GPU with driver access (`/dev/dri`).

**1. Pull the model** (example):

```bash
mkdir -p ov/models && cd ov

docker run -d \
  --rm \
  --user $(id -u):$(id -g) \
  -v $(pwd)/models:/models \
  openvino/model_server:latest-gpu \
  --pull \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

> This downloads the model weights. Wait for the container to finish before proceeding.

**2. Start the server** (example):

```bash
docker run -d \
  --rm \
  --name ovms \
  --user $(id -u):$(id -g) \
  -p 8000:8000 \
  -v $(pwd)/models:/models \
  --device /dev/dri \
  --group-add=$(stat -c "%g" /dev/dri/render* | head -n 1) \
  openvino/model_server:latest-gpu \
  --rest_port 8000 \
  --model_name openai/gpt-oss-20b \
  --model_repository_path /models \
  --source_model OpenVINO/gpt-oss-20b-int4-ov \
  --task text_generation \
  --tool_parser gptoss \
  --reasoning_parser gptoss \
  --enable_prefix_caching true \
  --target_device GPU
```

**3. Add to config** (partial — merge into `~/.pythinker/config.json`):

```json
{
  "providers": {
    "ovms": {
      "apiBase": "http://localhost:8000/v3"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ovms",
      "model": "openai/gpt-oss-20b"
    }
  }
}
```

> OVMS is a local server — no API key required. Supports tool calling (`--tool_parser gptoss`), reasoning (`--reasoning_parser gptoss`), and streaming.
> See the [official OVMS docs](https://docs.openvino.ai/2026/model-server/ovms_docs_llm_quickstart.html) for more details.
</details>

<details>
<summary><b>vLLM (local / OpenAI-compatible)</b></summary>

Run your own model with vLLM or any OpenAI-compatible server, then add to config:

**1. Start the server** (example):
```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. Add to config** (partial — merge into `~/.pythinker/config.json`):

*Provider (set API key to null for local servers):*
```json
{
  "providers": {
    "vllm": {
      "apiKey": null,
      "apiBase": "http://localhost:8000/v1"
    }
  }
}
```

*Model:*
```json
{
  "agents": {
    "defaults": {
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  }
}
```

</details>

<details>
<summary><b>Adding a New Provider (Developer Guide)</b></summary>

pythinker uses a **Provider Registry** (`pythinker/providers/registry.py`) as the single source of truth.
Adding a new provider only takes **2 steps** — no if-elif chains to touch.

**Step 1.** Add a `ProviderSpec` entry to `PROVIDERS` in `pythinker/providers/registry.py`:

```python
ProviderSpec(
    name="myprovider",                   # config field name
    keywords=("myprovider", "mymodel"),  # model-name keywords for auto-matching
    env_key="MYPROVIDER_API_KEY",        # env var name
    display_name="My Provider",          # shown in `pythinker status`
    default_api_base="https://api.myprovider.com/v1",  # OpenAI-compatible endpoint
)
```

**Step 2.** Add a field to `ProvidersConfig` in `pythinker/config/schema.py`:

```python
class ProvidersConfig(BaseModel):
    ...
    myprovider: ProviderConfig = ProviderConfig()
```

That's it! Environment variables, model routing, config matching, and `pythinker status` display will all work automatically.

**Common `ProviderSpec` options:**

| Field | Description | Example |
|-------|-------------|---------|
| `default_api_base` | OpenAI-compatible base URL | `"https://api.deepseek.com"` |
| `env_extras` | Additional env vars to set | `(("ZHIPUAI_API_KEY", "{api_key}"),)` |
| `model_overrides` | Per-model parameter overrides | `(("kimi-k2.5", {"temperature": 1.0}), ("kimi-k2.6", {"temperature": 1.0}),)` |
| `is_gateway` | Can route any model (like OpenRouter) | `True` |
| `detect_by_key_prefix` | Detect gateway by API key prefix | `"sk-or-"` |
| `detect_by_base_keyword` | Detect gateway by API base URL | `"openrouter"` |
| `strip_model_prefix` | Strip provider prefix before sending to gateway | `True` (for AiHubMix) |
| `supports_max_completion_tokens` | Use `max_completion_tokens` instead of `max_tokens`; required for providers that reject both being set simultaneously (e.g. VolcEngine) | `True` |

</details>

## Channel Settings

Global settings that apply to all channels. Configure under the `channels` section in `~/.pythinker/config.json`:

```json
{
  "channels": {
    "sendProgress": true,
    "sendToolHints": false,
    "sendMaxRetries": 3,
    "transcriptionProvider": "groq",
    "transcriptionLanguage": null,
    "telegram": { ... }
  }
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `sendProgress` | `true` | Stream agent's text progress to the channel |
| `sendToolHints` | `false` | Stream tool-call hints (e.g. `read_file("…")`) |
| `sendMaxRetries` | `3` | Max delivery attempts per outbound message, including the initial send (0-10 configured, minimum 1 actual attempt) |
| `transcriptionProvider` | `"groq"` | Voice transcription backend: `"groq"` (free tier, default) or `"openai"`. API key is auto-resolved from the matching provider config. |
| `transcriptionLanguage` | `null` | Optional ISO-639-1 language hint for audio transcription, e.g. `"en"`, `"ko"`, `"ja"`. |

### Retry Behavior

Retry is intentionally simple.

When a channel `send()` raises, pythinker retries at the channel-manager layer. By default, `channels.sendMaxRetries` is `3`, and that count includes the initial send.

- **Attempt 1**: Send immediately
- **Attempt 2**: Retry after `1s`
- **Attempt 3**: Retry after `2s`
- **Higher retry budgets**: Backoff continues as `1s`, `2s`, `4s`, then stays capped at `4s`
- **Transient failures**: Network hiccups and temporary API limits often recover on the next attempt
- **Permanent failures**: Invalid tokens, revoked access, or banned channels will exhaust the retry budget and fail cleanly

> [!NOTE]
> This design is deliberate: channel implementations should raise on delivery failure, and the channel manager owns the shared retry policy.
>
> Some channels may still apply small API-specific retries internally. For example, Telegram separately retries timeout and flood-control errors before surfacing a final failure to the manager.
>
> If a channel is completely unreachable, pythinker cannot notify the user through that same channel. Watch logs for `Failed to send to {channel} after N attempts` to spot persistent delivery failures.

## Web Search

> [!TIP]
> Use `proxy` in `tools.web` to route all web requests (search + fetch) through a proxy:
> ```json
> { "tools": { "web": { "proxy": "http://127.0.0.1:7890" } } }
> ```

pythinker supports multiple web search providers. Configure in `~/.pythinker/config.json` under `tools.web.search`.

By default, web tools are enabled and web search uses `duckduckgo`, so search works out of the box without an API key.

If you want to disable all built-in web tools entirely, set `tools.web.enable` to `false`. This removes both `web_search` and `web_fetch` from the tool list sent to the LLM.

If you need to allow trusted private ranges such as Tailscale / CGNAT addresses, you can explicitly exempt them from SSRF blocking with `tools.ssrfWhitelist`:

```json
{
  "tools": {
    "ssrfWhitelist": ["100.64.0.0/10"]
  }
}
```

| Provider | Config fields | Env var fallback | Free |
|----------|--------------|------------------|------|
| `brave` | `apiKey` | `BRAVE_API_KEY` | No |
| `tavily` | `apiKey` | `TAVILY_API_KEY` | No |
| `jina` | `apiKey` | `JINA_API_KEY` | Free tier (10M tokens) |
| `kagi` | `apiKey` | `KAGI_API_KEY` | No |
| `searxng` | `baseUrl` | `SEARXNG_BASE_URL` | Yes (self-hosted) |
| `duckduckgo` (default) | — | — | Yes |

**Disable all built-in web tools:**
```json
{
  "tools": {
    "web": {
      "enable": false
    }
  }
}
```

Configure under `tools.web.search`. Each provider has its own credential slot,
so multiple keys can coexist; switching `provider` does not overwrite the
others.

Web search providers are built-in tool backends, not MCP servers. For example,
setting `provider` to `"tavily"` enables the built-in `web_search` tool through
Tavily; it does not create an MCP server and will not list Tavily as a connected
server in `/mcp` unless you also add a Tavily entry under `tools.mcpServers`.

```jsonc
{
  "tools": {
    "web": {
      "search": {
        "provider": "brave",
        "providers": {
          "brave":   { "apiKey": "BSA..." },
          "tavily":  { "apiKey": "tvly-..." },
          "searxng": { "baseUrl": "https://searx.example.com" }
        },
        "maxResults": 5
      }
    }
  }
}
```

> **Legacy shape (deprecated, removed in 0.2.0).** Configs that set
> `tools.web.search.apiKey` or `baseUrl` at the top level are still loaded and
> auto-migrated into the `providers` dict on first load. The migrated config
> is written back to disk eagerly so the upgrade path is transparent. New
> setups should use the nested form above.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enable` | boolean | `true` | Enable or disable all built-in web tools (`web_search` + `web_fetch`) |
| `proxy` | string or null | `null` | Proxy for all web requests, for example `http://127.0.0.1:7890` |

### `tools.web.search`

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `provider` | string | `"duckduckgo"` | Active backend: `brave`, `tavily`, `jina`, `searxng`, `kagi`, `duckduckgo` |
| `providers` | object | `{}` | Per-provider credentials, e.g. `{"brave": {"apiKey": "..."}}`. |
| `maxResults` | integer | `5` | Results per search (1–10) |
| `timeout` | integer | `30` | Wall-clock timeout in seconds |
| `apiKey` *(deprecated)* | string | `""` | Legacy single-slot key; auto-migrated into `providers[<provider>].apiKey`. |
| `baseUrl` *(deprecated)* | string | `""` | Legacy single-slot URL; auto-migrated into `providers[searxng].baseUrl`. |

### `tools.web.browser.*` — sandboxed browser tool

Off by default. The default install includes the Playwright Python package, so
normal local/VPS installs can use a Playwright-managed headless Chromium browser
without a separate Docker service. Set `tools.web.browser.enable=true` to
register the `browser` tool.

`mode="auto"` is the default. It launches Playwright-managed Chromium unless
you explicitly configure a non-default `cdpUrl`, in which case Pythinker tries
that CDP endpoint first and falls back to launch mode if it is unavailable.
Use `mode="launch"` to force the packaged browser path, or `mode="cdp"` to
connect to an externally managed Chromium service such as the Docker
`pythinker-browser` profile.

```json
{
  "tools": {
    "web": {
      "browser": {
        "enable": true,
        "mode": "auto"
      }
    }
  }
}
```

| Key (disk, camelCase) | Python (snake_case) | Default | Description |
|---|---|---|---|
| `enable` | `enable` | `false` | Register the `browser` tool. |
| `mode` | `mode` | `auto` | Browser transport: `auto`, `launch`, or `cdp`. |
| `cdpUrl` | `cdp_url` | `http://127.0.0.1:9222` | Chromium DevTools Protocol endpoint for `cdp` mode or explicit-CDP `auto` mode. |
| `headless` | `headless` | `true` | Launch managed Chromium headlessly. |
| `executablePath` | `executable_path` | `null` | Advanced launch override for a custom Chromium executable. Prefer Playwright-managed Chromium unless you have a deployment-specific reason. |
| `autoProvision` | `auto_provision` | `true` | In launch mode, install Playwright Chromium lazily on first browser use if it is missing. |
| `provisionTimeoutS` | `provision_timeout_s` | `300` | Timeout for the bounded first-use `python -m playwright install chromium` subprocess. |
| `defaultTimeoutMs` | `default_timeout_ms` | `15000` | Per-action timeout for click/type/etc. |
| `navigationTimeoutMs` | `navigation_timeout_ms` | `30000` | Timeout for `page.goto`. |
| `evalTimeoutMs` | `eval_timeout_ms` | `5000` | Timeout for `evaluate`. |
| `snapshotMaxChars` | `snapshot_max_chars` | `20000` | Max characters returned by `snapshot`. |
| `idleTtlSeconds` | `idle_ttl_seconds` | `600` | Close idle per-session browser contexts after this many seconds. Set `0` to disable idle eviction. |
| `disconnectOnIdle` | `disconnect_on_idle` | `false` | Also close the shared browser process/connection when the last context is evicted or closed. |
| `maxPagesPerContext` | `max_pages_per_context` | `5` | Per-session tab/page limit. Extra pages are closed after each browser action. |
| `storageStateDir` | `storage_state_dir` | `null` | Override location of persisted cookies. `null` = `<config_dir>/browser/`. |

Environment variables (Pydantic-Settings, double underscore for nesting):

```bash
PYTHINKER_TOOLS__WEB__BROWSER__ENABLE=true
PYTHINKER_TOOLS__WEB__BROWSER__MODE=launch
PYTHINKER_TOOLS__WEB__BROWSER__CDP_URL=http://pythinker-browser:9222
PYTHINKER_TOOLS__WEB__BROWSER__DEFAULT_TIMEOUT_MS=15000
```

Launch-mode debug/escape-hatch environment variables:

- `PYTHINKER_BROWSER_HEADFUL=1` runs managed Chromium headed for local debugging.
- `PYTHINKER_BROWSER_NO_SANDBOX=1` adds `--no-sandbox` only when you explicitly accept that trade-off. Prefer `mode="cdp"` with an isolated browser service for hardened containers.

Run `pythinker doctor` to verify browser configuration. If Playwright Chromium
is missing and auto-provisioning is disabled or cannot reach the download host,
run:

```bash
python -m playwright install chromium
```

**Known limitation.** `storage_state` persists cookies and `localStorage` only.
IndexedDB (used by Firebase, Supabase, many OAuth flows) is not preserved across
restarts; affected sites will silently re-authenticate after a Pythinker restart
or `/restart`.

## MCP (Model Context Protocol)

> [!TIP]
> The config format is compatible with Claude Desktop / Cursor. You can copy MCP server configs directly from any MCP server's README.

pythinker supports [MCP](https://modelcontextprotocol.io/) — connect external tool servers and use them as native agent tools.

Add MCP servers to your `config.json`:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
      },
      "my-remote-mcp": {
        "url": "https://example.com/mcp/",
        "headers": {
          "Authorization": "Bearer xxxxx"
        }
      }
    }
  }
}
```

Two transport modes are supported:

| Mode | Config | Example |
|------|--------|---------|
| **Stdio** | `command` + `args` | Local process via `npx` / `uvx` |
| **HTTP** | `url` + `headers` (optional) | Remote endpoint (`https://mcp.example.com/sse`) |

Use `toolTimeout` to override the default 30s per-call timeout for slow servers:

```json
{
  "tools": {
    "mcpServers": {
      "my-slow-server": {
        "url": "https://example.com/mcp/",
        "toolTimeout": 120
      }
    }
  }
}
```

Use `enabledTools` to register only a subset of tools from an MCP server:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file", "mcp_filesystem_write_file"]
      }
    }
  }
}
```

`enabledTools` accepts either the raw MCP tool name (for example `read_file`) or the wrapped pythinker tool name (for example `mcp_filesystem_write_file`).

- Omit `enabledTools`, or set it to `["*"]`, to register all tools.
- Set `enabledTools` to `[]` to register no tools from that server.
- Set `enabledTools` to a non-empty list of names to register only that subset.

MCP tools are automatically discovered and registered on startup. The LLM can use them alongside built-in tools — no extra configuration needed.

In `pythinker tui`, `/mcp` refreshes this section from disk before showing the
overlay. `/mcp reconnect` closes existing MCP sessions, unregisters old MCP
capabilities, and reconnects from the current `tools.mcpServers` config so
server removals, credential rotations, and same-name config edits take effect
without restarting the TUI.




## Security

> [!TIP]
> For production deployments, set `"restrictToWorkspace": true` and `"tools.exec.sandbox": "bwrap"` in your config to sandbox the agent.
> In `v0.1.4.post3` and earlier, an empty `allowFrom` allowed all senders. Since `v0.1.4.post4`, empty `allowFrom` denies all access by default. To allow all senders, set `"allowFrom": ["*"]`.

| Option | Default | Description |
|--------|---------|-------------|
| `tools.restrictToWorkspace` | `false` | When `true`, restricts **all** agent tools (shell, file read/write/edit, list) to the workspace directory. Prevents path traversal and out-of-scope access. |
| `tools.exec.sandbox` | `""` | Sandbox backend for shell commands. Set to `"bwrap"` to wrap exec calls in a [bubblewrap](https://github.com/containers/bubblewrap) sandbox — the process can only see the workspace (read-write) and media directory (read-only); config files and API keys are hidden. Automatically enables `restrictToWorkspace` for file tools. **Linux only** — requires `bwrap` installed (`apt install bubblewrap`; pre-installed in the Docker image). Not available on macOS or Windows (bwrap depends on Linux kernel namespaces). |
| `tools.exec.enable` | `true` | When `false`, the shell `exec` tool is not registered at all. Use this to completely disable shell command execution. |
| `tools.exec.pathAppend` | `""` | Extra directories to append to `PATH` when running shell commands (e.g. `/usr/sbin` for `ufw`). |
| `channels.*.allowFrom` | `[]` (deny all) | Whitelist of user IDs. Empty denies all; use `["*"]` to allow everyone. |

**Docker security**: The official Docker image runs as a non-root user (`pythinker`, UID 1000) with bubblewrap pre-installed. When using `docker-compose.yml`, the container drops all Linux capabilities except `SYS_ADMIN` (required for bwrap's namespace isolation).


## Auto Compact

When a user is idle for longer than a configured threshold, pythinker **proactively** compresses the older part of the session context into a summary while keeping a recent legal suffix of live messages. This reduces token cost and first-token latency when the user returns — instead of re-processing a long stale context with an expired KV cache, the model receives a compact summary, the most recent live context, and fresh input.

```json
{
  "agents": {
    "defaults": {
      "idleCompactAfterMinutes": 15
    }
  }
}
```

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.idleCompactAfterMinutes` | `0` (disabled) | Minutes of idle time before auto-compaction starts. Set to `0` to disable. Recommended: `15` — close to a typical LLM KV cache expiry window, so stale sessions get compacted before the user returns. |

`sessionTtlMinutes` remains accepted as a legacy alias for backward compatibility, but `idleCompactAfterMinutes` is the preferred config key going forward.

How it works:
1. **Idle detection**: On each idle tick (~1 s), checks all sessions for expiration.
2. **Background compaction**: Idle sessions summarize the older live prefix via LLM and keep the most recent legal suffix (currently 8 messages).
3. **Summary injection**: When the user returns, the summary is injected as runtime context (one-shot, not persisted) alongside the retained recent suffix.
4. **Restart-safe resume**: The summary is also mirrored into session metadata so it can still be recovered after a process restart.

> [!NOTE]
> Mental model: "summarize older context, keep the freshest live turns, **and overwrite the session file with the compact form.**" It is not a full `session.clear()`, but it is a write — not a soft cursor move.
>
> Concretely, auto compact rewrites `sessions/<key>.jsonl` in place: older messages (including their structured `tool_calls` / `tool_call_id` / `reasoning_content`) are replaced by just the retained recent suffix (currently 8 messages), while the archived prefix is preserved only as a plain-text summary appended to `memory/history.jsonl` (or a `[RAW] ...` flattened dump if LLM summarization fails). The original structured JSON of those turns is no longer recoverable from the session file.
>
> This differs from the **token-driven soft consolidation** that fires when a prompt exceeds the context budget: that path only advances an internal `last_consolidated` cursor and leaves the session file untouched, so the raw tool-call trail stays on disk and can still be replayed or audited. If you rely on that trail for debugging or auditing, leave `idleCompactAfterMinutes` at the default `0` and let only the token-driven path run.

## Timezone

Time is context. Context should be precise.

By default, pythinker uses `UTC` for runtime time context. If you want the agent to think in your local time, set `agents.defaults.timezone` to a valid [IANA timezone name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones):

```json
{
  "agents": {
    "defaults": {
      "timezone": "Asia/Shanghai"
    }
  }
}
```

This affects runtime time strings shown to the model, such as runtime context and heartbeat prompts. It also becomes the default timezone for cron schedules when a cron expression omits `tz`, and for one-shot `at` times when the ISO datetime has no explicit offset.

Common examples: `UTC`, `America/New_York`, `America/Los_Angeles`, `Europe/London`, `Europe/Berlin`, `Asia/Tokyo`, `Asia/Shanghai`, `Asia/Singapore`, `Australia/Sydney`.

> Need another timezone? Browse the full [IANA Time Zone Database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

## Unified Session

By default, each channel × chat ID combination gets its own session. If you use pythinker across multiple channels (e.g. Telegram + Discord + CLI) and want them to share the same conversation, enable `unifiedSession`:

```json
{
  "agents": {
    "defaults": {
      "unifiedSession": true
    }
  }
}
```

When enabled, all incoming messages — regardless of which channel they arrive on — are routed into a single shared session. Switching from Telegram to Discord (or any other channel) continues the same conversation seamlessly.

| Behavior | `false` (default) | `true` |
|----------|-------------------|--------|
| Session key | `channel:chat_id` | `unified:default` |
| Cross-channel continuity | No | Yes |
| `/new` clears | Current channel session | Shared session |
| `/stop` finds tasks | By channel session | By shared session |
| Existing `session_key_override` (e.g. Telegram thread) | Respected | Still respected — not overwritten |

> This is designed for single-user, multi-device setups. It is **off by default** — existing users see zero behavior change.

## Disabled Skills

pythinker ships with built-in skills, and your workspace can also define custom skills under `skills/`. If you want to hide specific skills from the agent, set `agents.defaults.disabledSkills` to a list of skill directory names:

```json
{
  "agents": {
    "defaults": {
      "disabledSkills": ["github", "weather"]
    }
  }
}
```

Disabled skills are excluded from the main agent's skill summary, from always-on skill injection, and from subagent skill summaries. This is useful when some bundled skills are unnecessary for your deployment or should not be exposed to end users.

| Option | Default | Description |
|--------|---------|-------------|
| `agents.defaults.disabledSkills` | `[]` | List of skill directory names to exclude from loading. Applies to both built-in skills and workspace skills. |

## Provider Configuration

Each entry under `providers.<name>` is a `ProviderConfig` with these fields:

| Key (disk, camelCase) | Python (snake_case) | Default | Description |
|---|---|---|---|
| `apiKey` | `api_key` | `null` | Provider API key. May reference an env var via `${VAR}`. Not required for OAuth providers (`openai_codex`, `github_copilot`) or local servers (`ollama`, `lm_studio`, `vllm`, `ovms`). |
| `apiBase` | `api_base` | `null` | Override the provider's base URL (e.g. point `dashscope` at the Mainland China endpoint, or `zhipu` at the coding-plan endpoint). Defaults come from `pythinker/providers/registry.py`. |
| `extraHeaders` | `extra_headers` | `null` | Custom request headers merged into every call (e.g. `APP-Code` for AiHubMix). |
| `extraBody` | `extra_body` | `null` | Extra fields merged into every request body. Used by pythinker internally to inject `{"reasoning_split": true}` for `providers.minimax` thinking mode (`pythinker/providers/openai_compat_provider.py`); also available to users for any OpenAI-compatible request-body extension. |

## Agent Defaults

The `agents.defaults` block governs the agent's runtime parameters. The most common fields:

| Key (disk, camelCase) | Python (snake_case) | Default | Description |
|---|---|---|---|
| `workspace` | `workspace` | `~/.pythinker/workspace` | Filesystem root for tool I/O and session storage. |
| `model` | `model` | `openai-codex/gpt-5.5` | Active model id, prefixed with the provider name. |
| `alternateModels` | `alternate_models` | `[]` | Same-provider model ids surfaced in the WebUI model-switcher dropdown. The active `model` should not appear here — it's added implicitly. |
| `provider` | `provider` | `auto` | Provider key (e.g. `anthropic`, `openrouter`) or `auto` to derive from the `model` prefix. |
| `maxTokens` | `max_tokens` | `8192` | Per-call max output tokens. |
| `contextWindowTokens` | `context_window_tokens` | `65536` | Soft prompt-budget used by the consolidator and the runner's `_snip_history`. |
| `temperature` | `temperature` | `0.1` | Sampling temperature. Some providers override this internally (e.g. Moonshot's reasoning models force `1.0`). |
| `maxToolIterations` | `max_tool_iterations` | `200` | Hard ceiling on per-turn tool loop iterations. |
| `maxToolResultChars` | `max_tool_result_chars` | `16000` | Per-tool-result truncation budget; spillovers go to `.pythinker/tool-results/`. |
| `reasoningEffort` | `reasoning_effort` | `null` | One of `minimal` / `low` / `medium` / `high` / `xhigh` — enables LLM thinking mode where supported. |
| `timezone` | `timezone` | `UTC` | IANA timezone (see [Timezone](#timezone) below). |
| `unifiedSession` | `unified_session` | `false` | Share one session across all channels (see [Unified Session](#unified-session)). |
| `disabledSkills` | `disabled_skills` | `[]` | Skill names to exclude from loading (see [Disabled Skills](#disabled-skills)). |
| `idleCompactAfterMinutes` | `session_ttl_minutes` | `0` | Idle threshold before auto-compaction (see [Auto Compact](#auto-compact)). Legacy alias `sessionTtlMinutes` still accepted. |
| `dream` | `dream` | (defaults) | Memory curator schedule (`intervalH`, `cron`, `modelOverride`, `maxBatchSize`, `maxIterations`, `annotateLineAges`). |

## Logging

```json
{
  "logging": {
    "level": "INFO"
  }
}
```

| Key (disk, camelCase) | Python (snake_case) | Default | Description |
|---|---|---|---|
| `level` | `level` | `INFO` | Persistent default for the [loguru](https://loguru.readthedocs.io/) sink. One of `TRACE`, `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. The CLI's `--verbose` / `--quiet` flags and the `PYTHINKER_LOG_LEVEL` environment variable override this at runtime. Without an explicit value, loguru's default sink fires at DEBUG and floods the gateway's stdout with internal lifecycle events on every channel handshake. |

## Updates

Self-update behavior for `pythinker update` / `pythinker upgrade` and the
periodic update banner shown by the CLI.

```json
{
  "updates": {
    "check": true,
    "notify": true,
    "auto": "off",
    "checkIntervalH": 24,
    "prereleases": false
  }
}
```

| Key (disk, camelCase) | Python (snake_case) | Default | Description |
|---|---|---|---|
| `check` | `check` | `true` | Periodically poll PyPI for a newer release. Set to `false` to disable all update checks (also silences `pythinker update`'s implicit pre-flight). |
| `notify` | `notify` | `true` | When a newer release is found, print a one-line update banner at CLI startup. Independent of `check` only in that `notify=false` keeps the check itself but suppresses the banner. |
| `auto` | `auto` | `"off"` | Auto-upgrade policy. `"off"` = no automatic install (default; only the banner appears). `"patch"` = automatically install patch-level releases (no minor/major bumps). |
| `checkIntervalH` | `check_interval_h` | `24` | Hours between automatic checks. The check is rate-limited via `~/.pythinker/update/` so multiple `pythinker` invocations don't hammer PyPI. |
| `prereleases` | `prereleases` | `false` | Include pre-releases when picking the latest version. The `--prerelease` flag on `pythinker update` / `pythinker upgrade` overrides this for one run. |

## CLI Settings

### `cli.tui.theme`

Default TUI theme. Used when launching `pythinker tui` without
`--theme`.

```json
{
  "cli": {
    "tui": {
      "theme": "default"
    }
  }
}
```

Allowed values: `default`, `monochrome` (more themes may ship in
future releases). Updated automatically when you choose a theme via
the `/theme` picker inside the TUI.
