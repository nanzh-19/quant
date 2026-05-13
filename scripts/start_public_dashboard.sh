#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

mkdir -p outputs/logs tools
PID_FILE="outputs/logs/dashboard_tunnel.pid"
LOG_FILE="outputs/logs/dashboard_tunnel.log"
URL_FILE="outputs/logs/dashboard_public_url.txt"
CLOUDFLARED_BIN="tools/cloudflared"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Public tunnel already running with PID $(cat "$PID_FILE")"
  if [[ -f "$URL_FILE" ]]; then
    cat "$URL_FILE"
  fi
  exit 0
fi

PORT="$(python3 - <<'PY'
from pathlib import Path
import yaml

cfg = yaml.safe_load(Path("config/config.yml").read_text(encoding="utf-8")) or {}
dashboard_cfg = cfg.get("dashboard", {})
print(int(dashboard_cfg.get("port", 8765)))
PY
)"

TOKEN="$(python3 - <<'PY'
from pathlib import Path
import yaml

cfg = yaml.safe_load(Path("config/config.yml").read_text(encoding="utf-8")) or {}
dashboard_cfg = cfg.get("dashboard", {})
print(str(dashboard_cfg.get("access_token", "")))
PY
)"

if [[ ! -x "$CLOUDFLARED_BIN" ]]; then
  echo "Downloading cloudflared..."
  curl -L --fail --retry 3 -o "$CLOUDFLARED_BIN" \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x "$CLOUDFLARED_BIN"
fi

rm -f "$LOG_FILE" "$URL_FILE"

setsid "$CLOUDFLARED_BIN" tunnel --url "http://127.0.0.1:${PORT}" --no-autoupdate >"$LOG_FILE" 2>&1 < /dev/null &
echo $! >"$PID_FILE"

PUBLIC_URL=""
for _ in $(seq 1 30); do
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Public tunnel failed to start. Check $LOG_FILE"
    exit 1
  fi
  PUBLIC_URL="$(python3 - <<'PY'
from pathlib import Path
import re

text = Path("outputs/logs/dashboard_tunnel.log").read_text(encoding="utf-8", errors="ignore") if Path("outputs/logs/dashboard_tunnel.log").exists() else ""
match = re.search(r"https://[A-Za-z0-9.-]+\.trycloudflare\.com", text)
print(match.group(0) if match else "")
PY
)"
  if [[ -n "$PUBLIC_URL" ]]; then
    break
  fi
  sleep 2
done

if [[ -z "$PUBLIC_URL" ]]; then
  echo "Tunnel started but public URL was not detected yet. Check $LOG_FILE"
  exit 1
fi

SUFFIX=""
if [[ -n "$TOKEN" ]]; then
  SUFFIX="?token=$TOKEN"
fi

FULL_URL="${PUBLIC_URL}/index.html${SUFFIX}"
printf '%s\n' "$FULL_URL" >"$URL_FILE"

echo "Public tunnel started with PID $(cat "$PID_FILE")"
echo "$FULL_URL"
