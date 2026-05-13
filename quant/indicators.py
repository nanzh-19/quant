from __future__ import annotations

import pandas as pd


def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.sort_values("date").copy()
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_20"] = out["close"].pct_change(20)
    out["ret_60"] = out["close"].pct_change(60)
    out["ret_120"] = out["close"].pct_change(120)
    out["ma_20"] = out["close"].rolling(20).mean()
    out["ma_60"] = out["close"].rolling(60).mean()
    out["ma_120"] = out["close"].rolling(120).mean()
    out["ma_gap_20_60"] = out["ma_20"] / out["ma_60"] - 1.0
    out["avg_amount_20"] = out["amount"].rolling(20).mean()
    out["volatility_20"] = out["ret_1"].rolling(20).std(ddof=0) * (20**0.5)
    out["drawdown_20"] = out["close"] / out["close"].rolling(20).max() - 1.0
    return out
