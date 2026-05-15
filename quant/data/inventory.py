from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

EXPECTED_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "chg",
    "turnover",
    "symbol",
    "market",
]

CORE_VALUE_COLUMNS = ["open", "close", "high", "low", "volume", "amount"]


def _infer_market(symbol: str) -> str:
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


def _infer_asset_type(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(
        (
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
            "159",
        )
    ):
        return "etf"
    return "stock"


def _safe_read_csv(path: Path, **kwargs) -> pd.DataFrame:
    try:
        return pd.read_csv(path, **kwargs)
    except (ValueError, EmptyDataError, ParserError):
        return pd.DataFrame()


def build_data_inventory(
    daily_dir: Path,
    universe_df: pd.DataFrame,
    metadata_db: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if universe_df.empty:
        universe_df = pd.DataFrame(columns=["symbol", "name", "market", "asset_type", "list_date"])
    else:
        universe_df = universe_df.copy()
    if "symbol" in universe_df.columns:
        universe_df["symbol"] = universe_df["symbol"].astype(str).str.zfill(6)

    symbol_state = pd.DataFrame(columns=["symbol", "last_trade_date", "updated_at"])
    if metadata_db.exists():
        with sqlite3.connect(metadata_db) as conn:
            symbol_state = pd.read_sql_query(
                "select symbol, last_trade_date, updated_at from symbol_state",
                conn,
            )
    if not symbol_state.empty:
        symbol_state["symbol"] = symbol_state["symbol"].astype(str).str.zfill(6)

    detail_rows: list[dict] = []
    for path in sorted(daily_dir.glob("*.csv")):
        symbol = path.stem.zfill(6)
        header_df = _safe_read_csv(path, nrows=0)
        available_columns = list(header_df.columns)
        price_df = _safe_read_csv(path, usecols=lambda c: c in {"date", *CORE_VALUE_COLUMNS})
        if price_df.empty or "date" not in price_df.columns:
            start_date = ""
            end_date = ""
            rows = 0
            span_days = 0
            null_counts = {f"null_{col}": None for col in CORE_VALUE_COLUMNS}
        else:
            price_df["date"] = pd.to_datetime(price_df["date"], errors="coerce")
            price_df = price_df.dropna(subset=["date"]).sort_values("date")
            rows = len(price_df)
            start_date = price_df["date"].min().strftime("%Y-%m-%d") if rows else ""
            end_date = price_df["date"].max().strftime("%Y-%m-%d") if rows else ""
            span_days = int((price_df["date"].max() - price_df["date"].min()).days) if rows else 0
            null_counts = {f"null_{col}": int(price_df[col].isna().sum()) if col in price_df.columns else None for col in CORE_VALUE_COLUMNS}

        detail_rows.append(
            {
                "symbol": symbol,
                "file_name": path.name,
                "rows": rows,
                "start_date": start_date,
                "end_date": end_date,
                "span_days": span_days,
                "column_count": len(available_columns),
                "columns": ",".join(available_columns),
                "missing_expected_columns": ",".join([col for col in EXPECTED_COLUMNS if col not in available_columns]),
                "extra_columns": ",".join([col for col in available_columns if col not in EXPECTED_COLUMNS]),
                **null_counts,
            }
        )

    detail_df = pd.DataFrame(detail_rows)
    if detail_df.empty:
        empty_summary = pd.DataFrame(
            columns=[
                "asset_type",
                "market",
                "symbols",
                "total_rows",
                "min_start_date",
                "max_end_date",
                "avg_rows",
                "avg_span_days",
            ]
        )
        return detail_df, empty_summary

    merged = detail_df.merge(
        universe_df[[col for col in ["symbol", "name", "market", "asset_type", "list_date"] if col in universe_df.columns]],
        on="symbol",
        how="left",
    )
    merged = merged.merge(symbol_state, on="symbol", how="left", suffixes=("", "_state"))
    merged["name"] = merged["name"].fillna("")
    merged["market"] = merged["market"].fillna("")
    merged["asset_type"] = merged["asset_type"].fillna("")
    merged.loc[merged["market"] == "", "market"] = merged.loc[merged["market"] == "", "symbol"].map(_infer_market)
    merged.loc[merged["asset_type"] == "", "asset_type"] = merged.loc[merged["asset_type"] == "", "symbol"].map(
        _infer_asset_type
    )
    merged["list_date"] = merged["list_date"].fillna("")
    merged["last_trade_date"] = merged["last_trade_date"].fillna("")
    merged["updated_at"] = merged["updated_at"].fillna("")
    merged["data_asof_date"] = merged["end_date"]
    merged["coverage_days"] = merged["rows"]

    summary_df = (
        merged.assign(
            asset_type=merged["asset_type"].replace("", "unknown"),
            market=merged["market"].replace("", "unknown"),
        )
        .groupby(["asset_type", "market"], dropna=False)
        .agg(
            symbols=("symbol", "count"),
            total_rows=("rows", "sum"),
            min_start_date=("start_date", "min"),
            max_end_date=("end_date", "max"),
            avg_rows=("rows", "mean"),
            avg_span_days=("span_days", "mean"),
        )
        .reset_index()
    )
    summary_df["avg_rows"] = summary_df["avg_rows"].round(1)
    summary_df["avg_span_days"] = summary_df["avg_span_days"].round(1)

    ordered_columns = [
        "symbol",
        "name",
        "asset_type",
        "market",
        "list_date",
        "file_name",
        "rows",
        "start_date",
        "end_date",
        "span_days",
        "data_asof_date",
        "last_trade_date",
        "updated_at",
        "column_count",
        "columns",
        "missing_expected_columns",
        "extra_columns",
        "null_open",
        "null_close",
        "null_high",
        "null_low",
        "null_volume",
        "null_amount",
    ]
    merged = merged[[col for col in ordered_columns if col in merged.columns]]
    merged = merged.sort_values(["asset_type", "market", "symbol"]).reset_index(drop=True)
    summary_df = summary_df.sort_values(["asset_type", "market"]).reset_index(drop=True)
    return merged, summary_df
