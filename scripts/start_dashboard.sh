#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

mkdir -p outputs/logs
LOG_FILE="outputs/logs/dashboard.log"
PID_FILE="outputs/logs/dashboard.pid"
URL_FILE="outputs/logs/dashboard_urls.txt"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard already running with PID $(cat "$PID_FILE")"
  exit 0
fi

setsid python3 -u run.py dashboard --serve >>"$LOG_FILE" 2>&1 < /dev/null &
echo $! >"$PID_FILE"
sleep 1

if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "Dashboard failed to start. Check $LOG_FILE"
  exit 1
fi

python3 - <<'PY' >"$URL_FILE"
from pathlib import Path
import yaml
import subprocess

cfg = yaml.safe_load(Path("config/config.yml").read_text(encoding="utf-8")) or {}
dashboard_cfg = cfg.get("dashboard", {})
port = int(dashboard_cfg.get("port", 8765))
token = str(dashboard_cfg.get("access_token", ""))
suffix = f"?token={token}" if token else ""
print(f"http://127.0.0.1:{port}/index.html{suffix}")
try:
    output = subprocess.check_output(["hostname", "-I"], text=True).strip()
    addresses = {item.strip() for item in output.split() if item.strip()}
except Exception:
    addresses = set()
for addr in sorted(addresses):
    if addr.startswith("127."):
        continue
    print(f"http://{addr}:{port}/index.html{suffix}")
PY

echo "Dashboard started with PID $(cat "$PID_FILE")"
echo "Log: $LOG_FILE"
echo "URLs:"
cat "$URL_FILE"
