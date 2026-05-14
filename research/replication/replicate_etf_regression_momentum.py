#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel, summarize_backtest
from quant.indicators import add_basic_indicators
from quant.reporting import build_backtest_diagnostics
from quant.strategy import ETFRegressionMomentumStrategy


SOURCE_URL = "https://www.joinquant.com/community/post/detailMobile?postId=50158"
DEFAULT_SYMBOLS = ["518880", "513100", "159915", "510300"]


def _build_panel(symbols: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
        df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
        enriched = add_basic_indicators(df)
        enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
        enriched["symbol"] = symbol
        enriched["name"] = symbol
        enriched["asset_type"] = "etf"
        enriched["market_group"] = "replication"
        frames.append(enriched)
    panel = pd.concat(frames, ignore_index=True)
    return panel.dropna(subset=["avg_amount_20", "reg_momentum_25", "fwd_ret_1"]).reset_index(drop=True)


def _independent_returns(panel: pd.DataFrame, min_score: float) -> pd.DataFrame:
    rows = []
    for trade_date, day_df in panel.groupby("date"):
        candidates = day_df[day_df["reg_momentum_25"] > min_score].copy()
        if candidates.empty:
            rows.append({"date": trade_date, "expected_return": 0.0, "expected_symbol": ""})
            continue
        selected = candidates.sort_values("reg_momentum_25", ascending=False).iloc[0]
        rows.append(
            {
                "date": trade_date,
                "expected_return": float(selected["fwd_ret_1"]),
                "expected_symbol": str(selected["symbol"]).zfill(6),
            }
        )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate a public ETF regression momentum rotation rule.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--min-score", type=float, default=0.0)
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols]
    panel = _build_panel(symbols=symbols, start_date=args.start_date, end_date=args.end_date)
    strategy = ETFRegressionMomentumStrategy(
        {
            "symbols": symbols,
            "max_positions": 1,
            "min_score": args.min_score,
            "score_column": "reg_momentum_25",
        }
    )
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)
    expected = _independent_returns(panel, min_score=args.min_score)
    merged = returns_df[["date", "return"]].merge(expected, on="date", how="inner")
    merged["abs_diff"] = (merged["return"] - merged["expected_return"]).abs()

    summary = summarize_backtest(returns_df, initial_capital=1_000_000)
    row = summary.iloc[0].to_dict()
    engine_cum = float(row["cum_return"])
    expected_cum = float((1.0 + expected["expected_return"]).prod() - 1.0)
    max_daily_diff = float(merged["abs_diff"].max()) if not merged.empty else float("nan")
    cum_diff = abs(engine_cum - expected_cum)

    result = pd.DataFrame(
        [
            {
                "source": SOURCE_URL,
                "symbols": ",".join(symbols),
                "status": "pass" if max_daily_diff < 1e-12 and cum_diff < 1e-10 else "fail",
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "engine_cum_return": engine_cum,
                "expected_cum_return": expected_cum,
                "cum_return_diff": cum_diff,
                "max_daily_return_diff": max_daily_diff,
                "annual_return": float(row["annual_return"]),
                "annual_volatility": float(row["annual_volatility"]),
                "sharpe": float(row["sharpe"]),
                "max_drawdown": float(row["max_drawdown"]),
                "win_rate": float(row["win_rate"]),
            }
        ]
    )

    out_dir = PROJECT_ROOT / "outputs"
    result.to_csv(out_dir / "replication_etf_regression_momentum_summary.csv", index=False)
    returns_df.to_csv(out_dir / "replication_etf_regression_momentum_returns.csv", index=False)
    picks_df.to_csv(out_dir / "replication_etf_regression_momentum_picks.csv", index=False)
    expected.to_csv(out_dir / "replication_etf_regression_momentum_expected.csv", index=False)
    build_backtest_diagnostics(returns_df, picks_df, out_dir / "replication_etf_regression_momentum_diagnostics")

    print(result.to_string(index=False))
    print(f"saved={out_dir / 'replication_etf_regression_momentum_summary.csv'}")
    if not result.empty and result.iloc[0]["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
