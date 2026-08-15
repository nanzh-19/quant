#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/quant_mplconfig_${USER:-user}}"
mkdir -p "$MPLCONFIGDIR"
mkdir -p outputs/logs
LOG_FILE="outputs/logs/daily_$(date +%F).log"

{
  echo "[$(date '+%F %T')] daily job started"
  echo "python: $("$PYTHON_BIN" --version 2>&1) ($PYTHON_BIN)"
  "$PYTHON_BIN" run.py fast_daily --workers 8 --lookback-days 7
  echo "[$(date '+%F %T')] daily job finished"
  echo "status: outputs/daily_status.md"
  echo "inventory: outputs/data_inventory_summary.csv"
  echo "quality: outputs/data_quality_summary.csv"
  echo "recommendations: outputs/daily_recommendations.csv"
} 2>&1 | tee -a "$LOG_FILE"
