#!/usr/bin/env bash
set -euo pipefail

CRON_MARK_BEGIN="# >>> quant daily job >>>"
CRON_MARK_END="# <<< quant daily job <<<"
CURRENT_CRON="$(crontab -l 2>/dev/null || true)"

printf '%s\n' "$CURRENT_CRON" | awk -v begin="$CRON_MARK_BEGIN" -v end="$CRON_MARK_END" '
  $0 == begin { skip=1; next }
  $0 == end { skip=0; next }
  skip != 1 { print }
' | sed '/^[[:space:]]*$/d' | crontab -

echo "Removed quant daily cron job."
