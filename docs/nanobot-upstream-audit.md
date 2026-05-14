# Nanobot upstream audit — 2026-05-14

Source fetched from `https://github.com/HKUDS/nanobot.git` into local `nanobot/*` remote refs. There is no shared git merge base with this repository, so updates must be ported surgically rather than merged or cherry-picked wholesale.

## Applied in this pass

- `6a4ed255 fix(mcp): probe HTTP port before connecting to prevent event-loop crash`
  - Added HTTP TCP preflight for SSE/streamable HTTP MCP servers.
  - Added regression tests for unreachable HTTP MCP servers.
- `4fad19dc fix: use sequential MCP server connections to prevent CPU spin`
  - Switched MCP startup from parallel task fan-out to sequential connection.
- `6eb17811 fix(mcp): sanitize MCP tool names for provider compatibility`
  - Sanitized MCP tool/resource/prompt names to provider-safe `[a-zA-Z0-9_-]`.
  - Preserved raw and legacy wrapped `enabled_tools` matching.
- `aea5948b` / `5dc96505` web fetch URL cleanup
  - Strips markdown/backtick/quote wrappers before URL validation/fetch.
- Provider bug fixes from recent upstream:
  - Treat `reasoning_effort="none"` as thinking disabled for DashScope, MiniMax, VolcEngine/BytePlus, Xiaomi MiMo, and Kimi thinking models.
  - Add Xiaomi MiMo to provider-specific `thinking.type` handling.
  - Mark VolcEngine providers as using `max_completion_tokens`.
- Channel/media fixes from recent upstream:
  - Add early allowlist gates in Telegram, Email, and WhatsApp before media downloads, attachment extraction, reactions, or transcription.
  - Add WhatsApp bridge `audioMessage` download so Python-side voice transcription can receive audio files.
  - Add transient retry and malformed-response handling for OpenAI/Groq transcription.
  - Add Matrix pre-startup event filtering, fatal auth sync shutdown, and empty-message send guard.

## Already present or equivalent

- LLM timeout and timeout error kind.
- Runtime checkpoint and pending-user-turn recovery.
- Provider hot-reload cascade.
- Session cache cap / robust session manager behavior.
- NVIDIA NIM provider support, with Pythinker-specific env naming.
- Several WebUI features beyond upstream: modular WebSocket channel, admin surfaces, pin/archive/search, per-chat model override, voice, rAF stream coalescing, and slash command palette.

## Missing upstream fixes/enhancements to evaluate next

### High priority

1. Cross-channel `message` tool persistence
   - Upstream branches: `nanobot/fix/cross-channel-session-persist`.
   - Pythinker sends cross-channel messages but does not persist them into the target session history.
2. Matrix backoff refinements
   - Basic fatal-auth shutdown, pre-startup replay filter, and empty-send guard are applied. Remaining upstream parity is finer-grained backoff behavior.
3. Multimodal input limits
   - Upstream adds image count/byte limits; Pythinker currently encodes images without config-level caps.
4. SSRF non-retryable tool hint
   - Pythinker blocks SSRF, but runner still appends a generic “try a different approach” hint that can encourage bypass attempts.

### Medium priority / feature work

1. Model presets and fallback model failover
   - Upstream adds `model_presets` and `fallback_models`.
   - Large surface: config, runtime model switching, provider factory, CLI/WebUI, docs.
2. First-class reasoning stream events
   - Upstream adds `reasoning_delta` / `reasoning_end`, history hydration, and CLI trace polish.
   - Pythinker currently strips `<think>` for streaming and has different WebUI architecture.
3. `turn_end` / `session_updated` live WebUI protocol
   - Useful for title refresh and canonical history replay, but must fit Pythinker’s modular WebSocket/WebUI state.
4. `command_wrapper`
   - Upstream adds generic exec wrapper config.
   - Pythinker already has a stronger `sandbox="bwrap"` path; arbitrary wrapper config should be added only if needed.
5. WhatsApp bridge audio messages
   - Applied: `bridge/src/whatsapp.ts` now downloads `audioMessage` media for Python-side voice transcription.

### Not applicable unless adding those platforms

- Feishu/Lark bot member events and threaded post-media fixes.
- Weixin/WeCom/DingTalk/QQ adapter fixes.
- HKUDS hosted `nanobot auth` provider.
- Bedrock-specific tool-config history fix.

## Verification run

```bash
uv run pytest tests/tools/test_mcp_tool.py tests/tools/test_web_fetch_security.py tests/providers/test_litellm_kwargs.py -q
uv run pytest tests/providers/test_transcription.py tests/channels/test_whatsapp_channel.py tests/channels/test_email_channel.py tests/channels/test_telegram_channel.py tests/channels/test_matrix_channel.py -q
cd bridge && npm run build
uv run ruff check pythinker tests/tools/test_mcp_tool.py tests/tools/test_web_fetch_security.py tests/providers/test_litellm_kwargs.py tests/providers/test_transcription.py tests/channels/test_whatsapp_channel.py tests/channels/test_email_channel.py tests/channels/test_telegram_channel.py tests/channels/test_matrix_channel.py --select F401,F841
```

Result: `99 passed` for the first targeted suite; `178 passed` for provider/channel follow-up; bridge TypeScript build passed; ruff reported `All checks passed!`.
