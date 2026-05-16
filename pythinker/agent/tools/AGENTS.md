# AGENTS.md — `pythinker/agent/tools/`

Scoped rules for built-in tools and MCP integration. Root [`../../../AGENTS.md`](../../../AGENTS.md) applies first.

## Scope

Built-in agent tools (filesystem, search, shell, MCP, messaging, cron, notebook, runtime-introspection, web). Tools register through `registry.py`; schema fragments live in `schema.py`.

## Rules

- Side-effecting tools must respect approval/runtime policy. Do not bypass higher-level approval checks by calling lower-level helpers directly.
- The shell tool uses bubblewrap on Linux. Do not add sandbox bypasses or remove required Docker capabilities just for convenience.
- `GrepTool` has regex and output-size footguns. Preserve binary/file-size/output limits when changing it.
- `file_state.py` keeps module-level dedup state and is **not thread-safe**. Do not share read/write tool instances across loops without explicit synchronization.
- `web_fetch` marks external content as untrusted. Preserve that boundary.
- MCP support lives in `mcp.py`; treat MCP servers as untrusted by default and pass tool results through the same sanitization as built-in tools.

## Adding a tool

1. Implement the tool here with a small public surface.
2. Update `registry.py` and `schema.py` fragments.
3. Make approval, sandboxing, and side effects explicit.
4. Add focused tests for schema, execution, errors, and safety-sensitive behavior in `tests/agent/tools/` or `tests/tools/`.
5. Update `docs/my-tool.md`.

## Verification

```bash
uv run pytest tests/agent/tools/
uv run pytest tests/tools/
uv run ruff check pythinker/agent/tools --select F401,F841
```
