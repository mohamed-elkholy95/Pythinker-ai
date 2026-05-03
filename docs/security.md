# Security model — `pythinker gateway`

Pythinker's gateway speaks two surfaces to the outside world:

- **WebSocket** at `channels.websocket.host:port` (default `127.0.0.1:8765`) —
  the chat protocol the WebUI and any programmatic client use.
- **HTTP** on the same socket — the WebUI HTML/JS bundle, plus a small REST
  surface (`/api/sessions`, `/api/search`, `/api/commands`, `/api/models`,
  signed media fetches, and the `/webui/bootstrap` token issuer).

The gateway also exposes a separate health endpoint on `gateway.host:port`
(default `127.0.0.1:18790`). That one has no auth — never expose it
externally.

This page describes the supported deployment modes and the threat each one
defends against.

## Three supported deployment modes

### 1. Localhost (default — recommended)

```jsonc
"channels": {
  "websocket": {
    "enabled": true,
    "host": "127.0.0.1",
    "port": 8765
  }
}
```

- **Reachable from:** processes on the same machine only.
- **Auth:** every WebSocket handshake still requires a token
  (`websocket_requires_token: true` by default). The browser-embedded WebUI
  fetches a short-lived token from `/webui/bootstrap`, which is itself
  hardcoded localhost-only.
- **TLS:** unnecessary; the connection never leaves the loopback interface.
- **Use this when:** you're running pythinker on your laptop / desktop /
  server and use it from the same machine. **For remote access, prefer an SSH
  tunnel** to keep this mode unchanged:

  ```bash
  # On your laptop — forward 8765 on the laptop to 8765 on the server.
  ssh -L 8765:127.0.0.1:8765 user@server
  # Then open http://127.0.0.1:8765/ on the laptop as if it were local.
  ```

### 2. Localhost with a static token

Same shape as mode 1, plus a static token shared between the WebUI and any
programmatic client:

```jsonc
"channels": {
  "websocket": {
    "host": "127.0.0.1",
    "port": 8765,
    "token": "<32-byte secret from `pythinker token`>"
  }
}
```

- **Reachable from:** loopback only.
- **Auth:** clients must present `?token=<value>` on the WebSocket handshake.
- **Use this when:** other users on the same machine should *not* be able to
  hit your gateway with their own client. (Same-host isolation; loopback bind
  alone won't stop them.)

Generate the token with the bundled helper — never invent your own:

```bash
pythinker token
# → nbwt_NEzRk6JOEThXp3WNLqcqJzN1jvP4RGD5wmnWx4hghRk
```

### 3. Public bind with TLS + token

```jsonc
"channels": {
  "websocket": {
    "host": "0.0.0.0",
    "port": 8765,
    "token": "<token from `pythinker token`>",
    "ssl_certfile": "/etc/letsencrypt/live/example.com/fullchain.pem",
    "ssl_keyfile":  "/etc/letsencrypt/live/example.com/privkey.pem",
    "allowed_origins": ["https://example.com"],
    "allow_from": ["webui-prod"]
  }
}
```

- **Reachable from:** anywhere, but the handshake fails without:
  1. a valid token, AND
  2. an `Origin` header that matches `allowed_origins` (defends against
     browser-driven cross-site WebSocket attacks), AND
  3. a `client_id` query that matches `allow_from`.
- **TLS:** required. wss:// terminated either by Pythinker directly
  (`ssl_certfile`/`ssl_keyfile`) or by an upstream reverse proxy (Caddy,
  nginx, Traefik) — see the next section.
- **Use this when:** you need real remote access and you can't tunnel.

#### Reverse-proxy variant (recommended for public exposure)

Keep Pythinker bound to loopback and let a battle-tested HTTP proxy handle
TLS, certificate renewal, rate-limiting, and IP allowlisting:

```
Internet ── https://your-domain.example ──┐
                                          ▼
                                  Caddy/nginx/Traefik
                                  ─ TLS termination
                                  ─ Origin / Referer rules
                                  ─ Rate limiting
                                          │
                                          ▼
                              http://127.0.0.1:8765   ← pythinker gateway
```

Pythinker's config stays in mode 1 or 2 (loopback bind, optional static
token). The proxy is the only thing the public sees.

## Hard refusals at startup

The gateway refuses to start in two configurations that have historically
been the most common foot-guns:

| Configuration | What happens |
|---|---|
| `host` is non-loopback **and** no TLS **and** `allow_insecure_remote: false` (default) | `RuntimeError`: configure TLS or set `allow_insecure_remote: true` (LAN-dev only) |
| `token_issue_path` set **and** `host` is non-loopback **and** `token_issue_secret` is empty | `RuntimeError`: setting an open token-issue endpoint on a public interface defeats the auth surface |

These checks fire in `pythinker.channels.websocket.WebSocketChannel.start()`
before the socket is bound.

## Defenses in depth

Beyond the bind/TLS/token triad, the channel applies several smaller
mitigations:

- **`/webui/bootstrap` is localhost-only by default.** Remote callers must
  present `token_issue_secret` via `Authorization: Bearer <secret>` or
  `X-Pythinker-Auth: <secret>`. If neither is set the endpoint refuses
  remote requests entirely.
- **The SPA HTML is gated.** When bound to a non-loopback host without a
  configured `token_issue_secret`, the static SPA shell returns 404 to
  remote clients — there's no useful page to load if no remote auth path
  exists, so we don't reveal the service.
- **Issued tokens are one-shot.** A token minted by `/webui/bootstrap` is
  consumed by the first WebSocket handshake that presents it. Re-use is
  rejected.
- **Tokens have a TTL.** Default 5 minutes (`token_ttl_s`); minimum 30 s,
  maximum 24 h. Old tokens get garbage-collected.
- **Cap on outstanding tokens.** The bootstrap and issue endpoints both
  refuse beyond `_MAX_ISSUED_TOKENS` outstanding tokens, so a misbehaving
  client can't exhaust memory.
- **`client_id` allowlist.** `channels.websocket.allow_from` defaults to
  `["*"]` (any). Restrict to known IDs for tighter control.
- **Origin allowlist.** `channels.websocket.allowed_origins` defaults to
  `[]` (no check). Set to your known UI origin(s) to defend against
  cross-site WebSocket attacks when the channel is public.
- **Admin config operations are local/trusted-admin tools.** The Config
  Workbench's backup restore, loopback bind test, channel validation, MCP probe,
  and browser-pool probe are available only through the Admin Dashboard token
  gates. They are intended for local operators; keep the WebUI behind loopback,
  a tunnel, or real reverse-proxy authentication before exposing these controls
  to other users.
- **Admin write endpoints require a custom request header.** The runtime
  controls exposed by the Admin Dashboard — `/api/admin/sessions/{key}/stop`,
  `/api/admin/sessions/{key}/restart`, `/api/admin/subagents/{task_id}/cancel` —
  reject any request that does not present `X-Pythinker-Admin-Action: 1` in
  addition to a valid bearer token. Cross-site browser tabs can fire GET
  requests from `<img>` / `<form>` elements but cannot set custom headers
  without a CORS preflight that the server never answers, so a malicious page
  in another tab cannot trigger a session stop or subagent cancel even on a
  loopback deployment. The websockets HTTP parser is GET-only by design, so
  these mutations use action-in-path URLs (mirroring `/api/sessions/{key}/pin`
  and friends) rather than HTTP verbs.
- **Admin mutations are logged at INFO** with the line
  `admin_mutation route=… key=…`. By default Pythinker only configures a
  loguru stderr sink (`pythinker/utils/log.py`), so audit lines appear on
  the gateway's stderr rather than in the dashboard's Logs feed. To
  surface them in the WebUI, point `runtime.telemetry_jsonl_path` at a
  file or run `pythinker gateway` with stderr captured into
  `~/.pythinker/logs/*.log` — the Logs tab tails both.

## Compatibility table

| Mode | Network reach | Token | TLS | Status |
|---|---|---|---|---|
| `127.0.0.1`, no token | loopback | — | — | safe (default) |
| `127.0.0.1`, token | loopback | static or issued | — | safe |
| `0.0.0.0`, token, **TLS** | public | static or issued | required | safe |
| `0.0.0.0`, no token | public | — | — | warning — chat refused, HTML hidden |
| `0.0.0.0`, token, **no TLS** | public | static | none | **unsafe** — token sniffable |

The "unsafe" row is what `allow_insecure_remote: false` blocks at startup.
It's available behind the flag for trusted private LAN dev only.

## Generating a secret

`pythinker token` prints a fresh 256-bit url-safe random token. Use it for
both `channels.websocket.token` and `channels.websocket.token_issue_secret`
when needed:

```bash
pythinker token                # 32-byte default (256 bits)
pythinker token --bytes 48     # 384 bits, longer URL
```

Never embed memorable phrases as tokens. Static tokens should be at least
128 bits of entropy; the helper's default exceeds this.

## Threat checklist for "I want LAN access without a tunnel"

1. Bind: `host: 0.0.0.0` (or a specific LAN IP) — yes
2. TLS: `ssl_certfile` + `ssl_keyfile` configured (use `mkcert` for a
   trusted local CA on your LAN) — yes
3. Static token: `token: <pythinker token output>` — yes
4. Origin allowlist: `allowed_origins: ["https://your-host.local"]` — yes
5. Client allowlist: `allow_from: ["my-laptop", "my-phone"]` — yes
6. `allow_insecure_remote: false` — leave at default
7. Health port `:18790` not exposed externally (firewall it) — yes

That's the supported public-bind shape. Anything less and you're in the
"unsafe" row of the table.
