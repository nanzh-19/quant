from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant.data.data_quality import audit_daily_frame, normalize_daily_frame


def infer_market(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(
        (
            "600",
            "601",
            "603",
            "605",
            "688",
            "510",
            "511",
            "512",
            "513",
            "515",
            "516",
            "517",
            "518",
            "519",
            "520",
            "560",
            "561",
            "562",
            "563",
            "588",
        )
    ):
        return "SH"
    return "SZ"


def audit_or_repair_daily_file(path: Path, repair: bool = False) -> dict:
    symbol = path.stem.zfill(6)
    market = infer_market(symbol)
    try:
        df = pd.read_csv(path, dtype={"symbol": str})
    except Exception as exc:
        return {
            "symbol": symbol,
            "status": "read_error",
            "error": str(exc),
        }

    stats = audit_daily_frame(df, symbol=symbol, market=market)
    status = "ok"
    if stats.changed:
        status = "fixed" if repair else "needs_repair"
        if repair:
            fixed = normalize_daily_frame(df, symbol=symbol, market=market)
            if fixed.empty:
                return {
                    "symbol": symbol,
                    "status": "empty_after_repair",
                    "rows_before": stats.rows_before,
                    "rows_after": 0,
                    "duplicate_dates": stats.duplicate_dates,
                    "invalid_ohlc_rows": stats.invalid_ohlc_rows,
                    "lot_volume_rows": stats.lot_volume_rows,
                    "pct_chg_mismatch_rows": stats.pct_chg_mismatch_rows,
                }
            tmp_path = path.with_suffix(f"{path.suffix}.tmp")
            fixed.to_csv(tmp_path, index=False)
            os.replace(tmp_path, path)

    return {
        "symbol": symbol,
        "status": status,
        "rows_before": stats.rows_before,
        "rows_after": stats.rows_after,
        "duplicate_dates": stats.duplicate_dates,
        "invalid_ohlc_rows": stats.invalid_ohlc_rows,
        "lot_volume_rows": stats.lot_volume_rows,
        "pct_chg_mismatch_rows": stats.pct_chg_mismatch_rows,
    }


def run_daily_quality_check(
    daily_dir: Path,
    outputs_dir: Path,
    repair: bool = False,
    max_symbols: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(daily_dir.glob("*.csv"))
    if max_symbols > 0:
        paths = paths[:max_symbols]

    rows = []
    for index, path in enumerate(paths, start=1):
        rows.append(audit_or_repair_daily_file(path, repair=repair))
        if index % 200 == 0 or index == len(paths):
            problem_count = sum(1 for row in rows if row.get("status") not in {"ok", "fixed"})
            changed_count = sum(1 for row in rows if row.get("status") in {"fixed", "needs_repair"})
            print(
                f"[quality] {index}/{len(paths)} checked; changed={changed_count}; problems={problem_count}",
                flush=True,
            )

    report_df = pd.DataFrame(rows)
    if report_df.empty:
        summary_df = pd.DataFrame(
            [
                {
                    "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "files": 0,
                    "status": "no_files",
                    "problem_files": 0,
                    "duplicate_dates": 0,
                    "invalid_ohlc_rows": 0,
                    "lot_volume_rows": 0,
                    "pct_chg_mismatch_rows": 0,
                }
            ]
        )
    else:
        issue_columns = ["duplicate_dates", "invalid_ohlc_rows", "lot_volume_rows", "pct_chg_mismatch_rows"]
        for column in issue_columns:
            if column not in report_df.columns:
                report_df[column] = 0
            report_df[column] = pd.to_numeric(report_df[column], errors="coerce").fillna(0).astype(int)
        problem_files = int((~report_df["status"].isin(["ok", "fixed"])).sum())
        issue_rows = int(report_df[issue_columns].sum().sum())
        summary_df = pd.DataFrame(
            [
                {
                    "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "files": len(report_df),
                    "status": "pass" if problem_files == 0 and (repair or issue_rows == 0) else "fail",
                    "problem_files": problem_files,
                    "duplicate_dates": int(report_df["duplicate_dates"].sum()),
                    "invalid_ohlc_rows": int(report_df["invalid_ohlc_rows"].sum()),
                    "lot_volume_rows": int(report_df["lot_volume_rows"].sum()),
                    "pct_chg_mismatch_rows": int(report_df["pct_chg_mismatch_rows"].sum()),
                }
            ]
        )

    report_path = outputs_dir / "data_quality_report.csv"
    summary_path = outputs_dir / "data_quality_summary.csv"
    report_df.to_csv(report_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    return report_df, summary_df
