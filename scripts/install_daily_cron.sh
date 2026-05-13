#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRON_SCHEDULE="${1:-0 19 * * 1-5}"
CRON_MARK_BEGIN="# >>> quant daily job >>>"
CRON_MARK_END="# <<< quant daily job <<<"
CRON_JOB="$CRON_SCHEDULE cd $ROOT_DIR && /bin/bash $ROOT_DIR/scripts/daily_run.sh"

CURRENT_CRON="$(crontab -l 2>/dev/null || true)"
FILTERED_CRON="$(printf '%s\n' "$CURRENT_CRON" | awk -v begin="$CRON_MARK_BEGIN" -v end="$CRON_MARK_END" '
  $0 == begin { skip=1; next }
  $0 == end { skip=0; next }
  skip != 1 { print }
')"

{
  printf '%s\n' "$FILTERED_CRON" | sed '/^[[:space:]]*$/d'
  printf '%s\n' "$CRON_MARK_BEGIN"
  printf '%s\n' "CRON_TZ=Asia/Shanghai"
  printf '%s\n' "$CRON_JOB"
  printf '%s\n' "$CRON_MARK_END"
} | crontab -

echo "Installed quant daily cron job:"
echo "  $CRON_JOB"
