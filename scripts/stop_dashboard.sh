#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PID_FILE="outputs/logs/dashboard.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Dashboard is not running."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  echo "Stopped dashboard PID $PID"
else
  echo "Dashboard PID $PID was not running."
fi

rm -f "$PID_FILE"
