# Deployment

## Docker

> [!TIP]
> The `-v ~/.pythinker:/home/pythinker/.pythinker` flag mounts your local config directory into the container, so your config and workspace persist across container restarts.
> The container runs as user `pythinker` (UID 1000). If you get **Permission denied**, fix ownership on the host first: `sudo chown -R 1000:1000 ~/.pythinker`, or pass `--user $(id -u):$(id -g)` to match your host UID. Podman users can use `--userns=keep-id` instead.

### Docker Compose

```bash
docker compose run --rm pythinker-cli onboard   # first-time setup
vim ~/.pythinker/config.json                     # add API keys
docker compose up -d pythinker-gateway           # start gateway
```

```bash
docker compose run --rm pythinker-cli agent -m "Hello!"   # run CLI
docker compose logs -f pythinker-gateway                   # view logs
docker compose down                                      # stop
```

### Docker

```bash
# Build the image
docker build -t pythinker .

# Initialize config (first time only)
docker run -v ~/.pythinker:/home/pythinker/.pythinker --rm pythinker onboard

# Edit config on host to add API keys
vim ~/.pythinker/config.json

# Run gateway (connects to enabled channels, e.g. Telegram/Discord/Slack)
docker run -v ~/.pythinker:/home/pythinker/.pythinker -p 18790:18790 pythinker gateway

# Or run a single command
docker run -v ~/.pythinker:/home/pythinker/.pythinker --rm pythinker agent -m "Hello!"
docker run -v ~/.pythinker:/home/pythinker/.pythinker --rm pythinker status
```

## Linux Service

Run the gateway as a systemd user service so it starts automatically and restarts on failure.

**1. Find the pythinker binary path:**

```bash
which pythinker   # e.g. /home/user/.local/bin/pythinker
```

**2. Create the service file** at `~/.config/systemd/user/pythinker-gateway.service` (replace `ExecStart` path if needed):

```ini
[Unit]
Description=Pythinker Gateway
After=network.target

[Service]
Type=simple
ExecStart=%h/.local/bin/pythinker gateway
Restart=always
RestartSec=10
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=%h

[Install]
WantedBy=default.target
```

**3. Enable and start:**

```bash
systemctl --user daemon-reload
systemctl --user enable --now pythinker-gateway
```

**Common operations:**

```bash
systemctl --user status pythinker-gateway        # check status
systemctl --user restart pythinker-gateway       # restart after config changes
journalctl --user -u pythinker-gateway -f        # follow logs
```

If you edit the `.service` file itself, run `systemctl --user daemon-reload` before restarting.

> **Note:** User services only run while you are logged in. To keep the gateway running after logout, enable lingering:
>
> ```bash
> loginctl enable-linger $USER
> ```

## Browser automation

The `browser` tool is opt-in at config time. Pythinker's default Python package
includes Playwright, and `tools.web.browser.mode="auto"` launches a
Playwright-managed headless Chromium process for normal local, VPS, and
headless-server installs.

### Package-first launch mode

Enable the tool in `~/.pythinker/config.json`:

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

On first browser use, Pythinker checks for the Playwright Chromium binary. If it
is missing and `autoProvision=true` (the default), Pythinker runs a bounded
installer equivalent to:

```bash
python -m playwright install chromium
```

Use `pythinker doctor` to check whether browser tooling is enabled, whether CDP
is reachable when configured, and whether managed Chromium is already present.

For restricted networks, corporate proxies, or regions where Playwright's
default CDN is unreliable, pre-provision Chromium during image/host setup and
use Playwright's standard environment controls such as `PLAYWRIGHT_DOWNLOAD_HOST`
and proxy variables before running the install command.

### CDP / Docker service mode

CDP mode keeps browser lifecycle outside the Python process and remains the
recommended production path for hardened Docker deployments, noVNC
observability, or operators who want to isolate Chromium separately.

```bash
docker compose --profile browser up -d pythinker-browser
```

Then set:

```json
{
  "tools": {
    "web": {
      "browser": {
        "enable": true,
        "mode": "cdp",
        "cdpUrl": "http://127.0.0.1:9222"
      }
    }
  }
}
```

The service binds Chromium's DevTools port to `127.0.0.1:9222`. To watch the
agent browse via VNC:

1. Set `BROWSER_ENABLE_VIEWER=1` on the service.
2. Uncomment the `127.0.0.1:6080:6080` line in `docker-compose.yml`.
3. Open `http://127.0.0.1:6080/vnc.html` in your browser.

### Container sandbox notes

Launch mode first tries Chromium's normal sandbox. Pythinker does not add
`--no-sandbox` automatically. If launch fails because the Chromium sandbox
cannot initialize inside a container, prefer `mode="cdp"` with the
`pythinker-browser` service. For local debugging only, setting
`PYTHINKER_BROWSER_NO_SANDBOX=1` adds `--no-sandbox`; this is an explicit
escape hatch, not the recommended hardened deployment.

### Network policy

The browser tool uses Pythinker's existing SSRF policy
(`pythinker/security/network.py`) on every navigation **and every sub-request**.
Private/loopback/link-local addresses are blocked by default. To allow specific
internal CIDRs (e.g. Tailscale, Docker bridge), set `tools.ssrf_whitelist`.

### `pythinker-gateway` integration

When the `browser` profile is active, `pythinker-gateway` can discover the
service via the `PYTHINKER_TOOLS__WEB__BROWSER__CDP_URL` environment variable
and `PYTHINKER_TOOLS__WEB__BROWSER__MODE=cdp`. Without those settings, enabled
browser tooling uses package-first launch mode.
