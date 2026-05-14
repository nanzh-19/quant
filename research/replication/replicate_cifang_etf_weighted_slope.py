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
from quant.strategy import ETFWeightedSlopeRotationStrategy


SOURCE_URL = "https://blog.cifangquant.com/post/112.html"
PUBLIC_START_DATE = "2020-01-01"
DEFAULT_HISTORY_START = "2018-01-01"
PUBLIC_TOTAL_RETURN = 16.4743
PUBLIC_ANNUAL_RETURN = 0.5994
PUBLIC_MAX_DRAWDOWN = -0.1492
PUBLIC_SHARPE = 2.20
DEFAULT_SYMBOLS = ["159915", "159941", "513030", "513520", "159985", "518880"]


def _load_panel(symbols: list[str], history_start_date: str, end_date: str) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= history_start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
        if df.empty:
            continue
        enriched = add_basic_indicators(df)
        enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
        enriched["symbol"] = symbol
        enriched["name"] = symbol
        enriched["asset_type"] = "etf"
        enriched["market_group"] = "cifang_replication"
        frames.append(enriched)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    required = ["weighted_slope_25", "fwd_ret_1", "avg_amount_20"]
    return panel.dropna(subset=required).reset_index(drop=True)


def _independent_returns(panel: pd.DataFrame, strategy_cfg: dict, report_start_date: str) -> pd.DataFrame:
    rows = []
    active_symbol = ""
    active_high = 0.0
    cooldown_remaining = 0
    score_col = strategy_cfg["score_column"]
    min_score = strategy_cfg["min_score"]
    max_score = strategy_cfg["max_score"]
    stop_profit_drawdown = strategy_cfg["stop_profit_drawdown"]
    cooldown_days = strategy_cfg["cooldown_days"]

    for trade_date, day_df in panel.groupby("date"):
        day = day_df.copy()
        if cooldown_remaining > 0:
            cooldown_remaining -= 1
            if cooldown_remaining == 0:
                active_symbol = ""
                active_high = 0.0
            rows.append({"date": trade_date, "expected_return": 0.0, "expected_symbol": ""})
            continue

        if active_symbol:
            active_row = day[day["symbol"].astype(str).str.zfill(6) == active_symbol]
            if not active_row.empty:
                close = float(active_row["close"].iloc[0])
                active_high = max(active_high, close)
                if active_high > 0 and close <= active_high * (1.0 - stop_profit_drawdown):
                    active_symbol = ""
                    active_high = 0.0
                    cooldown_remaining = cooldown_days
                    rows.append({"date": trade_date, "expected_return": 0.0, "expected_symbol": ""})
                    continue

        day[score_col] = pd.to_numeric(day[score_col], errors="coerce")
        candidates = day[(day[score_col] >= min_score) & (day[score_col] <= max_score)].copy()
        candidates = candidates.dropna(subset=[score_col, "close"])
        if candidates.empty:
            active_symbol = ""
            active_high = 0.0
            rows.append({"date": trade_date, "expected_return": 0.0, "expected_symbol": ""})
            continue

        selected = candidates.sort_values(score_col, ascending=False).iloc[0]
        selected_symbol = str(selected["symbol"]).zfill(6)
        selected_close = float(selected["close"])
        if selected_symbol == active_symbol:
            active_high = max(active_high, selected_close)
        else:
            active_symbol = selected_symbol
            active_high = selected_close
        rows.append(
            {
                "date": trade_date,
                "expected_return": float(selected["fwd_ret_1"]),
                "expected_symbol": active_symbol,
            }
        )

    expected = pd.DataFrame(rows)
    expected["date"] = pd.to_datetime(expected["date"])
    expected = expected[expected["date"] >= pd.Timestamp(report_start_date)].sort_values("date").reset_index(drop=True)
    expected["equity"] = 1_000_000 * (1.0 + expected["expected_return"]).cumprod()
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate the Cifang ETF weighted-slope rotation sample.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default=PUBLIC_START_DATE)
    parser.add_argument("--history-start-date", default=DEFAULT_HISTORY_START)
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--score-column", default="weighted_slope_25")
    parser.add_argument("--min-score", type=float, default=0.0)
    parser.add_argument("--max-score", type=float, default=5.0)
    parser.add_argument("--stop-profit-drawdown", type=float, default=0.05)
    parser.add_argument("--cooldown-days", type=int, default=5)
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols]
    panel = _load_panel(symbols=symbols, history_start_date=args.history_start_date, end_date=args.end_date)
    if panel.empty:
        raise RuntimeError("no local ETF data available for replication")

    strategy_cfg = {
        "symbols": symbols,
        "score_column": args.score_column,
        "min_score": args.min_score,
        "max_score": args.max_score,
        "stop_profit_drawdown": args.stop_profit_drawdown,
        "cooldown_days": args.cooldown_days,
    }
    strategy = ETFWeightedSlopeRotationStrategy(strategy_cfg)
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }

    backtest_panel = panel[pd.to_datetime(panel["date"]) >= pd.Timestamp(args.start_date)].reset_index(drop=True)
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(backtest_panel, strategy, backtest_cfg)
    expected = _independent_returns(backtest_panel, strategy_cfg, report_start_date=args.start_date)
    merged = returns_df[["date", "return"]].merge(expected[["date", "expected_return"]], on="date", how="inner")
    merged["abs_diff"] = (merged["return"] - merged["expected_return"]).abs()

    summary = summarize_backtest(returns_df, initial_capital=1_000_000).iloc[0].to_dict()
    engine_total = float(summary["cum_return"])
    expected_total = float(expected["equity"].iloc[-1] / 1_000_000 - 1.0)
    max_daily_diff = float(merged["abs_diff"].max()) if not merged.empty else float("nan")
    total_diff = abs(engine_total - expected_total)

    result = pd.DataFrame(
        [
            {
                "source": SOURCE_URL,
                "public_start_date": PUBLIC_START_DATE,
                "public_total_return": PUBLIC_TOTAL_RETURN,
                "public_annual_return": PUBLIC_ANNUAL_RETURN,
                "public_max_drawdown": PUBLIC_MAX_DRAWDOWN,
                "public_sharpe": PUBLIC_SHARPE,
                "symbols": ",".join(symbols),
                "status": "pass" if max_daily_diff < 1e-12 and total_diff < 1e-10 else "mismatch",
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
    result.to_csv(out_dir / "replication_cifang_etf_weighted_slope_summary.csv", index=False)
    returns_df.to_csv(out_dir / "replication_cifang_etf_weighted_slope_returns.csv", index=False)
    picks_df.to_csv(out_dir / "replication_cifang_etf_weighted_slope_picks.csv", index=False)
    expected.to_csv(out_dir / "replication_cifang_etf_weighted_slope_expected.csv", index=False)

    print(result.to_string(index=False))
    print(f"saved={out_dir / 'replication_cifang_etf_weighted_slope_summary.csv'}")
    if not result.empty and result.iloc[0]["status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
