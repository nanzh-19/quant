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
from quant.strategy import ETFRSRSRotationStrategy


SOURCE_URL = "https://bigquant.com/square/ai/32264c26-c478-65f3-2ddb-5f070dfb2724"
PUBLIC_TOTAL_RETURN = 3.4425
PUBLIC_ANNUAL_RETURN = 0.123
PUBLIC_MAX_DRAWDOWN = -0.1722
PUBLIC_SHARPE = 0.82
DEFAULT_SYMBOLS = ["518880", "513100"]


def _load_panel(symbols: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
        if df.empty:
            continue
        enriched = add_basic_indicators(df)
        enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
        enriched["symbol"] = symbol
        enriched["name"] = symbol
        enriched["asset_type"] = "etf"
        enriched["market_group"] = "bigquant_replication"
        frames.append(enriched)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    return panel.dropna(subset=["rsrs_z_18_600", "fwd_ret_1"]).reset_index(drop=True)


def _independent_returns(panel: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    held_symbols: set[str] = set()
    rows = []
    for trade_date, day_df in panel.groupby("date"):
        day = day_df.copy()
        day["symbol"] = day["symbol"].astype(str).str.zfill(6)
        for row in day.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            if symbol not in symbols:
                continue
            z_score = float(row.rsrs_z_18_600)
            buy_signal = (0.0 < z_score < 2.0) or (z_score < -2.0)
            sell_signal = (-2.0 < z_score < -1.0) or (z_score > 3.0)
            if symbol in held_symbols and sell_signal:
                held_symbols.remove(symbol)
            elif symbol not in held_symbols and buy_signal:
                held_symbols.add(symbol)
        held = day[day["symbol"].isin(held_symbols)]
        expected_return = float(held["fwd_ret_1"].mean()) if not held.empty else 0.0
        rows.append({"date": trade_date, "expected_return": expected_return, "expected_symbols": ",".join(sorted(held_symbols))})
    expected = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    expected["equity"] = 1_000_000 * (1.0 + expected["expected_return"]).cumprod()
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate BigQuant RSRS ETF rotation sample.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default="2013-07-01")
    parser.add_argument("--end-date", default="2026-05-13")
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols]
    panel = _load_panel(symbols=symbols, start_date=args.start_date, end_date=args.end_date)
    if panel.empty:
        raise RuntimeError("no local ETF data available for replication")

    strategy = ETFRSRSRotationStrategy({"symbols": symbols})
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)
    expected = _independent_returns(panel, symbols=symbols)
    merged = returns_df[["date", "return"]].merge(expected[["date", "expected_return"]], on="date", how="inner")
    merged["abs_diff"] = (merged["return"] - merged["expected_return"]).abs()

    summary = summarize_backtest(returns_df, initial_capital=1_000_000).iloc[0].to_dict()
    engine_total = float(summary["cum_return"])
    expected_total = float(expected["equity"].iloc[-1] / 1_000_000 - 1.0)
    total_diff = abs(engine_total - expected_total)
    max_daily_diff = float(merged["abs_diff"].max()) if not merged.empty else float("nan")

    result = pd.DataFrame(
        [
            {
                "source": SOURCE_URL,
                "public_total_return": PUBLIC_TOTAL_RETURN,
                "public_annual_return": PUBLIC_ANNUAL_RETURN,
                "public_max_drawdown": PUBLIC_MAX_DRAWDOWN,
                "public_sharpe": PUBLIC_SHARPE,
                "symbols": ",".join(symbols),
                "status": "pass" if total_diff < 1e-10 and max_daily_diff < 1e-12 else "mismatch",
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "engine_total_return": engine_total,
                "expected_total_return": expected_total,
                "total_return_diff": total_diff,
                "max_daily_return_diff": max_daily_diff,
                "annual_return": float(summary["annual_return"]),
                "annual_return_diff_vs_public": float(summary["annual_return"]) - PUBLIC_ANNUAL_RETURN,
                "annual_volatility": float(summary["annual_volatility"]),
                "sharpe": float(summary["sharpe"]),
                "sharpe_diff_vs_public": float(summary["sharpe"]) - PUBLIC_SHARPE,
                "max_drawdown": float(summary["max_drawdown"]),
                "max_drawdown_diff_vs_public": float(summary["max_drawdown"]) - PUBLIC_MAX_DRAWDOWN,
                "win_rate": float(summary["win_rate"]),
                "avg_turnover": float(summary["avg_turnover"]),
                "avg_positions": float(summary["avg_positions"]),
            }
        ]
    )

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_dir / "replication_bigquant_rsrs_etf_summary.csv", index=False)
    returns_df.to_csv(out_dir / "replication_bigquant_rsrs_etf_returns.csv", index=False)
    picks_df.to_csv(out_dir / "replication_bigquant_rsrs_etf_picks.csv", index=False)
    expected.to_csv(out_dir / "replication_bigquant_rsrs_etf_expected.csv", index=False)

    print(result.to_string(index=False))
    print(f"saved={out_dir / 'replication_bigquant_rsrs_etf_summary.csv'}")
    if not result.empty and result.iloc[0]["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
