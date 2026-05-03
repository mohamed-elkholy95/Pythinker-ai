#!/usr/bin/env bash
# Browser sandbox boot: Xvfb → optional VNC → Chromium (foreground).
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-0}"
export DISPLAY=":${DISPLAY_NUM}"

# 1. Xvfb (always)
Xvfb "$DISPLAY" -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!

# 2. Optional VNC stack
if [[ "${BROWSER_ENABLE_VIEWER:-0}" == "1" ]]; then
    x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 &
    websockify --web=/usr/share/novnc 6080 localhost:5900 &
fi

# 3. Chromium in the foreground (so tini can reap it cleanly)
exec chromium \
    --no-first-run \
    --no-default-browser-check \
    --disable-gpu \
    --remote-debugging-address=0.0.0.0 \
    --remote-debugging-port=9222 \
    --user-data-dir=/home/sandbox/.chromium-data \
    "about:blank"
