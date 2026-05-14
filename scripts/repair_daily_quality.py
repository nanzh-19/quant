#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quant.quality import run_daily_quality_check


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and repair local daily OHLCV CSV files.")
    parser.add_argument("--daily-dir", default=str(PROJECT_ROOT / "data" / "daily"))
    parser.add_argument("--outputs-dir", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=0)
    args = parser.parse_args()

    report, summary = run_daily_quality_check(
        daily_dir=Path(args.daily_dir),
        outputs_dir=Path(args.outputs_dir),
        repair=not args.dry_run,
        max_symbols=args.max_symbols,
    )
    status_counts = report["status"].value_counts().to_dict() if not report.empty else {}
    print(f"summary={summary.iloc[0].to_dict() if not summary.empty else {}}")
    print(f"status_counts={status_counts}")


if __name__ == "__main__":
    main()
