#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # Prefer the project venv when available so cron and shell runs use the same environment.
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

mkdir -p outputs/logs
LOG_FILE="outputs/logs/daily_$(date +%F).log"

{
  echo "[$(date '+%F %T')] daily job started"
  python3 run.py fast_daily --workers 16 --lookback-days 7
  echo "[$(date '+%F %T')] daily job finished"
  echo "status: outputs/daily_status.md"
  echo "inventory: outputs/data_inventory_summary.csv"
  echo "recommendations: outputs/daily_recommendations.csv"
} | tee -a "$LOG_FILE"
