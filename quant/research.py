from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from quant.engine.indicators import add_basic_indicators


def build_latest_snapshot(
    universe: pd.DataFrame,
    daily_dir: Path,
    min_history_days_stock: int,
    min_history_days_etf: int,
) -> pd.DataFrame:
    rows = []
    for item in universe.itertuples(index=False):
        symbol = str(item.symbol).zfill(6)
        path = daily_dir / f"{symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        except (EmptyDataError, ParserError):
            continue
        asset_type = getattr(item, "asset_type", "stock")
        min_history = min_history_days_etf if asset_type == "etf" else min_history_days_stock
        if len(df) < min_history or "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"])
        enriched = add_basic_indicators(df)
        latest = enriched.iloc[-1]
        rows.append(
            {
                "symbol": symbol,
                "name": getattr(item, "name", ""),
                "asset_type": asset_type,
                "market_group": getattr(item, "market_group", ""),
                "date": latest["date"],
                "close": latest["close"],
                "ret_20": latest["ret_20"],
                "ret_60": latest["ret_60"],
                "ret_120": latest.get("ret_120"),
                "avg_amount_20": latest["avg_amount_20"],
                "ma_20": latest.get("ma_20"),
                "ma_60": latest.get("ma_60"),
                "ma_gap_20_60": latest.get("ma_gap_20_60"),
                "volatility_20": latest.get("volatility_20"),
                "drawdown_20": latest.get("drawdown_20"),
            }
        )
    if not rows:
        return pd.DataFrame()
    snapshot = pd.DataFrame(rows)
    return snapshot.dropna().sort_values("date").reset_index(drop=True)
