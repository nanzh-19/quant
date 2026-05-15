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
from quant.strategy import ETFDualMomentumRotationStrategy


SOURCE_URL = "https://juejin.cn/post/7620758996278575167"
PUBLIC_ANNUAL_RETURN = 0.2715
PUBLIC_MAX_DRAWDOWN = -0.11
PUBLIC_SHARPE = 1.41
PUBLIC_TOLERANCE = 0.02
DEFAULT_SYMBOLS = ["510300", "510500", "518880", "159934", "513100", "513500", "511380", "511010"]


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
        enriched["market_group"] = "public_replication"
        frames.append(enriched)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True)
    required = ["ret_60", "ma_200", "fwd_ret_1", "avg_amount_20"]
    return panel.dropna(subset=required).reset_index(drop=True)


def _independent_returns(panel: pd.DataFrame, max_positions: int, fallback_symbol: str, rebalance_frequency: str) -> pd.DataFrame:
    rows = []
    current_weights: dict[str, float] = {}
    prev_rebalance_date = None
    daily_groups = list(panel.groupby("date"))

    for index, (trade_date, day_df) in enumerate(daily_groups):
        next_date = daily_groups[index + 1][0] if index + 1 < len(daily_groups) else None
        if rebalance_frequency == "monthly":
            rebalance = (
                prev_rebalance_date is None
                or trade_date.month != prev_rebalance_date.month
                or trade_date.year != prev_rebalance_date.year
            )
        elif rebalance_frequency == "month_end":
            rebalance = prev_rebalance_date is None or next_date is None or trade_date.month != next_date.month or trade_date.year != next_date.year
        else:
            raise ValueError(f"unsupported rebalance_frequency: {rebalance_frequency}")
        if rebalance:
            day = day_df.copy()
            candidates = day[(day["close"] > day["ma_200"]) & (day["ret_60"] > 0)].copy()
            if candidates.empty and fallback_symbol:
                fallback = day[day["symbol"].astype(str).str.zfill(6) == fallback_symbol]
                current_weights = {fallback_symbol: 1.0} if not fallback.empty else {}
            elif candidates.empty:
                current_weights = {}
            else:
                selected = candidates.sort_values("ret_60", ascending=False).head(max_positions)
                current_symbols = set(selected["symbol"].astype(str).str.zfill(6))
                equal_weight = 1.0 / len(current_symbols) if current_symbols else 0.0
                current_weights = {symbol: equal_weight for symbol in current_symbols}
            prev_rebalance_date = trade_date

        if not current_weights:
            expected_return = 0.0
        else:
            held = day_df.copy()
            held["symbol"] = held["symbol"].astype(str).str.zfill(6)
            returns_by_symbol = {
                str(row.symbol): float(row.fwd_ret_1)
                for row in held[held["symbol"].isin(current_weights)].itertuples(index=False)
                if pd.notna(row.fwd_ret_1)
            }
            expected_return = sum(weight * returns_by_symbol.get(symbol, 0.0) for symbol, weight in current_weights.items())
            if expected_return > -1.0:
                current_weights = {
                    symbol: weight * (1.0 + returns_by_symbol.get(symbol, 0.0)) / (1.0 + expected_return)
                    for symbol, weight in current_weights.items()
                }
        rows.append({"date": trade_date, "expected_return": expected_return, "expected_symbols": ",".join(sorted(current_weights))})

    out = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    out["equity"] = 1_000_000 * (1.0 + out["expected_return"]).cumprod()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate a public ETF dual momentum rotation sample.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default="2020-01-01")
    parser.add_argument("--end-date", default="2024-12-31")
    parser.add_argument("--max-positions", type=int, default=2)
    parser.add_argument("--fallback-symbol", default="")
    parser.add_argument("--rebalance-frequency", choices=["monthly", "month_end"], default="month_end")
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols]
    fallback_symbol = str(args.fallback_symbol).zfill(6) if args.fallback_symbol else ""
    panel = _load_panel(symbols=symbols, start_date=args.start_date, end_date=args.end_date)
    if panel.empty:
        raise RuntimeError("no local ETF data available for replication")

    strategy = ETFDualMomentumRotationStrategy(
        {
            "symbols": symbols,
            "max_positions": args.max_positions,
            "fallback_symbol": fallback_symbol,
        }
    )
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": args.rebalance_frequency,
    }
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)
    expected = _independent_returns(
        panel,
        max_positions=args.max_positions,
        fallback_symbol=fallback_symbol,
        rebalance_frequency=args.rebalance_frequency,
    )
    merged = returns_df[["date", "return"]].merge(expected[["date", "expected_return"]], on="date", how="inner")
    merged["abs_diff"] = (merged["return"] - merged["expected_return"]).abs()

    summary = summarize_backtest(returns_df, initial_capital=1_000_000).iloc[0].to_dict()
    engine_cum = float(summary["cum_return"])
    expected_cum = float(expected["equity"].iloc[-1] / 1_000_000 - 1.0)
    max_daily_diff = float(merged["abs_diff"].max()) if not merged.empty else float("nan")
    cum_diff = abs(engine_cum - expected_cum)

    annual_return_diff = float(summary["annual_return"]) - PUBLIC_ANNUAL_RETURN
    max_drawdown_diff = float(summary["max_drawdown"]) - PUBLIC_MAX_DRAWDOWN
    sharpe_diff = float(summary["sharpe"]) - PUBLIC_SHARPE
    internal_status = "pass" if max_daily_diff < 1e-12 and cum_diff < 1e-10 else "fail"
    public_status = "pass" if abs(annual_return_diff) <= PUBLIC_TOLERANCE and abs(max_drawdown_diff) <= PUBLIC_TOLERANCE else "mismatch"

    result = pd.DataFrame(
        [
            {
                "source": SOURCE_URL,
                "public_annual_return": PUBLIC_ANNUAL_RETURN,
                "public_max_drawdown": PUBLIC_MAX_DRAWDOWN,
                "public_sharpe": PUBLIC_SHARPE,
                "symbols": ",".join(symbols),
                "fallback_symbol": fallback_symbol,
                "rebalance_frequency": args.rebalance_frequency,
                "internal_status": internal_status,
                "public_status": public_status,
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "engine_cum_return": engine_cum,
                "expected_cum_return": expected_cum,
                "cum_return_diff": cum_diff,
                "max_daily_return_diff": max_daily_diff,
                "annual_return": float(summary["annual_return"]),
                "annual_return_diff_vs_public": annual_return_diff,
                "annual_volatility": float(summary["annual_volatility"]),
                "sharpe": float(summary["sharpe"]),
                "sharpe_diff_vs_public": sharpe_diff,
                "max_drawdown": float(summary["max_drawdown"]),
                "max_drawdown_diff_vs_public": max_drawdown_diff,
                "win_rate": float(summary["win_rate"]),
                "avg_turnover": float(summary["avg_turnover"]),
                "avg_positions": float(summary["avg_positions"]),
            }
        ]
    )

    out_dir = PROJECT_ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = args.rebalance_frequency
    result.to_csv(out_dir / f"replication_juejin_etf_dual_momentum_{suffix}_summary.csv", index=False)
    returns_df.to_csv(out_dir / f"replication_juejin_etf_dual_momentum_{suffix}_returns.csv", index=False)
    picks_df.to_csv(out_dir / f"replication_juejin_etf_dual_momentum_{suffix}_picks.csv", index=False)
    expected.to_csv(out_dir / f"replication_juejin_etf_dual_momentum_{suffix}_expected.csv", index=False)

    print(result.to_string(index=False))
    print(f"saved={out_dir / f'replication_juejin_etf_dual_momentum_{suffix}_summary.csv'}")
    if not result.empty and result.iloc[0]["internal_status"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
