# Runtime layer (governed execution)

Pythinker has a small "governed-execution" layer that adds tracing, policy,
budgets, and audit on top of the existing message-bus runtime. It is **off
by default** — every existing deployment behaves identically until you set
something in the new `runtime` config block.

## Quick start

Add this to `~/.pythinker/config.json`:

```json
{
  "runtime": {
    "policyEnabled": false,
    "telemetrySink": "jsonl",
    "telemetryJsonlPath": "/home/you/.pythinker/events.jsonl",
    "sessionCacheMax": 512,
    "maxToolCallsPerTurn": 50,
    "maxWallClockS": 120.0,
    "maxSubagentRecursionDepth": 3,
    "manifestsDir": null
  }
}
```

This installs the JSONL telemetry sink — every turn now writes one line per
event to `events.jsonl` — and stamps every inbound message with a per-turn
budget of 50 tool calls and 120 seconds of wall-clock.

## What gets emitted

Each event is one JSON object on its own line:

| Event             | When                              | Key attributes                          |
|-------------------|-----------------------------------|-----------------------------------------|
| `turn_started`    | Loop dispatches a message         | `lock_wait_s`, `concurrency_wait_s`, `inbound_queue_depth` |
| `tool_call`       | Egress gateway authorises a tool  | `tool`, `allowed`, `reason` (on deny)   |
| `tool_result`     | Tool execution finishes           | `tool`, `duration_s`, `error`           |
| `policy_decision` | PolicyService evaluates a request | `phase`, `tool`, `allowed`, `reason`    |
| `turn_finished`   | Loop releases the per-session lock| `duration_s`                            |

Every event carries `trace_id`, `span_id`, `parent_span_id`, `session_key_hash`
(a 12-char sha256 digest — never the raw key, since `session_key` can carry
user-attributable identifiers like Slack user ids), `channel`, `agent_id`,
and `policy_version`. A subagent shares `trace_id` with its parent and bumps
`parent_span_id` so the call tree reconstructs from any consumer (jq one-liners,
Loki, OpenTelemetry forwarder).

## Turning on policy

`policyEnabled: true` is **deny-by-default** when no allow-list is configured. Setting it without `manifestsDir` and without a migration mode results in every tool call being rejected. This is intentional — silent allow-all on a "policy enabled" flag is the wrong default. Pick one of three valid configurations:

**1. Manifest-driven (recommended for production):**

```json
{
  "runtime": {
    "policyEnabled": true,
    "manifestsDir": "/home/you/.pythinker/agents"
  }
}
```

Drop JSON files into that directory:

```json
{
  "id": "research",
  "name": "Research Agent",
  "version": "0.1.0",
  "model": "openai-codex/gpt-5.5",
  "owner": "you@example.com",
  "lifecycle": "active",
  "allowedTools": ["read_file", "grep", "web_search", "web_fetch"],
  "memoryScope": "session",
  "enabledSkills": ["weather"]
}
```

Only manifests with `lifecycle: "active"` populate the allow-list. `draft`/`deprecated`/`retired` manifests are loaded but ignored by the policy — useful for staging or graceful retirement without deleting the file.

**2. Migration mode (every existing tool keeps working while you write manifests):**

```json
{
  "runtime": {
    "policyEnabled": true,
    "policyMigrationMode": "allow-all"
  }
}
```

Logs a warning at startup so you don't forget the policy is effectively a no-op. Remove this flag once your manifests cover every workflow.

**3. Policy off (default — pre-runtime behaviour):**

Don't set `policyEnabled` at all (or set it to `false`). The runtime layer still emits telemetry if you've configured a sink, but every tool call is allowed.

## Scheduled paths and exemptions

Two scheduled subsystems run **without** a user-originated message and therefore without an originating channel:

- **Cron-triggered jobs** (`pythinker/cron/service.py`) — `_normalize_context_for_cron` synthesizes a context with `channel="cron"`, `sender_id="system"`, `chat_id=<job_id>`. These flow through the policy + egress like any other turn.
- **Heartbeat jobs** (`pythinker/heartbeat/service.py`) — same shape with `channel="heartbeat"`.
- **Dream consolidation** (`pythinker/agent/memory.py`) — runs as the system-bound agent `system_dream`. Its tool calls (`read_file`, `edit_file`, `write_file` only) flow through the **same** egress gateway as everything else and emit normal `tool_call` / `tool_result` telemetry. The `system_dream` allow-list is a named entry in `PolicyService.builtin_exemptions` — tightening the `default` agent's allow-list does not affect Dream, and tightening Dream beyond its three tools requires editing the exemptions map (intentional, code-reviewable).

If you add a new scheduled subsystem, follow Dream's pattern: pick a `system_*` agent_id, add a narrow entry to `_BUILTIN_EXEMPTIONS`, route all tool calls through `egress.execute(ctx, name, params)`. **Do not** add a code path that bypasses the gateway — the gateway is the only doorway, and a bypass anywhere is a hole everywhere.

## Trade-offs

The runtime layer is **measurement-first** — turn the JSONL sink on, observe what the loop is actually doing under your real traffic, then decide which policies to tighten. Don't add rules without telemetry to back them; you'll either over-block legitimate traffic or under-protect against the actual abuse surface.
