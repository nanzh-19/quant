#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel, summarize_backtest
from quant.indicators import add_basic_indicators
from quant.strategy import BuyAndHoldStrategy


DEFAULT_SYMBOLS = ["510300", "510050", "159915"]


def _prepare_single_symbol_panel(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"missing daily csv for {symbol}: {path}")

    df = pd.read_csv(path, dtype={"symbol": str})
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
    if len(df) < 150:
        raise RuntimeError(f"not enough rows for {symbol}: {len(df)}")

    enriched = add_basic_indicators(df)
    enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
    enriched["symbol"] = symbol
    enriched["name"] = symbol
    enriched["asset_type"] = "etf" if symbol.startswith(("159", "510", "511", "512", "513", "515", "516", "517", "518", "519", "520", "560", "561", "562", "563", "588")) else "stock"
    enriched["market_group"] = "validation"
    panel = enriched.dropna(subset=["ret_20", "ret_60", "avg_amount_20", "fwd_ret_1"]).copy()
    if panel.empty:
        raise RuntimeError(f"empty validation panel for {symbol}")
    return panel


def validate_symbol(symbol: str, start_date: str, end_date: str) -> dict:
    panel = _prepare_single_symbol_panel(symbol=symbol, start_date=start_date, end_date=end_date)
    strategy = BuyAndHoldStrategy({"symbols": [symbol]})
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)
    if returns_df.empty:
        raise RuntimeError(f"engine returned empty validation result for {symbol}")

    expected = panel[["date", "fwd_ret_1"]].rename(columns={"fwd_ret_1": "expected_return"})
    merged = returns_df[["date", "return"]].merge(expected, on="date", how="inner")
    merged["abs_return_diff"] = (merged["return"] - merged["expected_return"]).abs()

    engine_summary = summarize_backtest(returns_df, initial_capital=1_000_000).iloc[0].to_dict()
    expected_cum_return = float((1.0 + expected["expected_return"]).prod() - 1.0)
    engine_cum_return = float(engine_summary["cum_return"])
    max_daily_return_diff = float(merged["abs_return_diff"].max())
    cum_return_diff = abs(engine_cum_return - expected_cum_return)

    return {
        "symbol": symbol,
        "status": "pass" if max_daily_return_diff < 1e-12 and cum_return_diff < 1e-10 else "fail",
        "start_date": str(returns_df["date"].iloc[0].date()),
        "end_date": str(returns_df["date"].iloc[-1].date()),
        "engine_days": len(returns_df),
        "pick_rows": len(picks_df),
        "engine_cum_return": engine_cum_return,
        "expected_cum_return": expected_cum_return,
        "cum_return_diff": cum_return_diff,
        "max_daily_return_diff": max_daily_return_diff,
        "engine_annual_return": float(engine_summary["annual_return"]),
        "engine_annual_volatility": float(engine_summary["annual_volatility"]),
        "engine_max_drawdown": float(engine_summary["max_drawdown"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate deterministic backtest behavior with buy-and-hold ETFs.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs" / "validation" / "internal" / "backtest_validation_buy_hold.csv"))
    args = parser.parse_args()

    rows = [validate_symbol(str(symbol).zfill(6), args.start_date, args.end_date) for symbol in args.symbols]
    result = pd.DataFrame(rows)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)

    print(result.to_string(index=False))
    print(f"saved={output_path}")
    if not result.empty and (result["status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
