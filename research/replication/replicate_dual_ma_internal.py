#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel
from quant.indicators import add_basic_indicators
from quant.strategy import DualMAStrategy


def _build_panel(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
    enriched = add_basic_indicators(df)
    enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
    enriched["symbol"] = symbol
    enriched["name"] = symbol
    enriched["asset_type"] = "etf"
    enriched["market_group"] = "replication"
    return enriched.dropna(
        subset=[
            "ret_20",
            "ret_60",
            "ret_120",
            "ma_20",
            "ma_60",
            "ma_gap_20_60",
            "avg_amount_20",
            "fwd_ret_1",
        ]
    ).copy()


def validate_dual_ma(symbol: str, start_date: str, end_date: str) -> dict:
    panel = _build_panel(symbol=symbol, start_date=start_date, end_date=end_date)
    strategy = DualMAStrategy(
        {
            "max_positions": 1,
            "min_ret_120": 0.0,
            "min_price_stock": 0.0,
            "min_price_etf": 0.0,
            "min_avg_turnover_million_stock": 0.0,
            "min_avg_turnover_million_etf": 0.0,
        }
    )
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, _ = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)

    expected = panel[["date", "close", "ma_20", "ma_60", "ret_120", "fwd_ret_1"]].copy()
    expected["signal"] = (
        (expected["close"] >= expected["ma_20"])
        & (expected["ma_20"] >= expected["ma_60"])
        & (expected["ret_120"] >= 0.0)
        & (expected["ma_20"] > 0)
        & (expected["ma_60"] > 0)
    ).astype(int)
    expected["expected_return"] = expected["signal"] * expected["fwd_ret_1"]

    merged = returns_df[["date", "return"]].merge(expected[["date", "expected_return"]], on="date", how="inner")
    merged["abs_diff"] = (merged["return"] - merged["expected_return"]).abs()
    engine_cum = float((1.0 + returns_df["return"]).prod() - 1.0)
    expected_cum = float((1.0 + expected["expected_return"]).prod() - 1.0)
    max_daily_diff = float(merged["abs_diff"].max())
    cum_diff = abs(engine_cum - expected_cum)

    return {
        "symbol": symbol,
        "status": "pass" if max_daily_diff < 1e-12 and cum_diff < 1e-10 else "fail",
        "start_date": str(returns_df["date"].iloc[0].date()),
        "end_date": str(returns_df["date"].iloc[-1].date()),
        "days": len(returns_df),
        "signal_days": int(expected["signal"].sum()),
        "engine_cum_return": engine_cum,
        "expected_cum_return": expected_cum,
        "cum_return_diff": cum_diff,
        "max_daily_return_diff": max_daily_diff,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate DualMA strategy against an independent vectorized calculation.")
    parser.add_argument("--symbols", nargs="+", default=["510300", "510050", "159915"])
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    args = parser.parse_args()

    result = pd.DataFrame([validate_dual_ma(str(symbol).zfill(6), args.start_date, args.end_date) for symbol in args.symbols])
    out_path = PROJECT_ROOT / "outputs" / "replication_dual_ma_internal.csv"
    result.to_csv(out_path, index=False)
    print(result.to_string(index=False))
    print(f"saved={out_path}")
    if not result.empty and (result["status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
