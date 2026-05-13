#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quant.data_quality import audit_daily_frame, normalize_daily_frame


def _infer_market(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("600", "601", "603", "605", "688", "510", "511", "512", "513", "515", "516", "517", "518", "519", "520", "560", "561", "562", "563", "588")):
        return "SH"
    return "SZ"


def repair_file(path: Path, dry_run: bool) -> dict:
    symbol = path.stem.zfill(6)
    market = _infer_market(symbol)
    try:
        df = pd.read_csv(path, dtype={"symbol": str})
    except Exception as exc:
        return {
            "symbol": symbol,
            "status": "read_error",
            "error": str(exc),
        }

    stats = audit_daily_frame(df, symbol=symbol, market=market)
    if not stats.changed:
        return {
            "symbol": symbol,
            "status": "ok",
            "rows_before": stats.rows_before,
            "rows_after": stats.rows_after,
            "duplicate_dates": stats.duplicate_dates,
            "invalid_ohlc_rows": stats.invalid_ohlc_rows,
            "lot_volume_rows": stats.lot_volume_rows,
            "pct_chg_mismatch_rows": stats.pct_chg_mismatch_rows,
        }

    fixed = normalize_daily_frame(df, symbol=symbol, market=market)
    if fixed.empty:
        return {
            "symbol": symbol,
            "status": "would_empty" if dry_run else "empty_after_repair",
            "rows_before": stats.rows_before,
            "rows_after": 0,
            "duplicate_dates": stats.duplicate_dates,
            "invalid_ohlc_rows": stats.invalid_ohlc_rows,
            "lot_volume_rows": stats.lot_volume_rows,
            "pct_chg_mismatch_rows": stats.pct_chg_mismatch_rows,
        }

    if not dry_run:
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        fixed.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)

    return {
        "symbol": symbol,
        "status": "would_fix" if dry_run else "fixed",
        "rows_before": stats.rows_before,
        "rows_after": len(fixed),
        "duplicate_dates": stats.duplicate_dates,
        "invalid_ohlc_rows": stats.invalid_ohlc_rows,
        "lot_volume_rows": stats.lot_volume_rows,
        "pct_chg_mismatch_rows": stats.pct_chg_mismatch_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and repair local daily OHLCV CSV files.")
    parser.add_argument("--daily-dir", default=str(PROJECT_ROOT / "data" / "daily"))
    parser.add_argument("--outputs-dir", default=str(PROJECT_ROOT / "outputs"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-symbols", type=int, default=0)
    args = parser.parse_args()

    daily_dir = Path(args.daily_dir)
    outputs_dir = Path(args.outputs_dir)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    paths = sorted(daily_dir.glob("*.csv"))
    if args.max_symbols > 0:
        paths = paths[: args.max_symbols]

    rows = []
    for index, path in enumerate(paths, start=1):
        row = repair_file(path, dry_run=args.dry_run)
        rows.append(row)
        if index % 200 == 0 or index == len(paths):
            fixed = sum(1 for item in rows if item.get("status") in {"fixed", "would_fix"})
            errors = sum(1 for item in rows if str(item.get("status", "")).endswith("error"))
            print(f"[quality] {index}/{len(paths)} checked; fixed={fixed}; errors={errors}", flush=True)

    report = pd.DataFrame(rows)
    report_path = outputs_dir / ("data_quality_dry_run_report.csv" if args.dry_run else "data_quality_repair_report.csv")
    report.insert(0, "run_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    report.to_csv(report_path, index=False)

    status_counts = report["status"].value_counts().to_dict() if not report.empty else {}
    print(f"saved={report_path}")
    print(f"status_counts={status_counts}")
    if not report.empty:
        numeric_cols = [
            "duplicate_dates",
            "invalid_ohlc_rows",
            "lot_volume_rows",
            "pct_chg_mismatch_rows",
        ]
        totals = {col: int(pd.to_numeric(report.get(col, 0), errors="coerce").fillna(0).sum()) for col in numeric_cols}
        print(f"issue_totals={totals}")


if __name__ == "__main__":
    main()
