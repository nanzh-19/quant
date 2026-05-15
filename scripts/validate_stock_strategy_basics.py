#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quant.engine.backtest import run_cross_sectional_backtest_from_panel
from quant.engine.indicators import add_basic_indicators
from quant.engine.strategy import BuyAndHoldStrategy, MomentumStrategy


DEFAULT_SYMBOLS = ["600000", "600519", "601318", "000001", "000333"]


def _load_stock_panel(symbols: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    frames = []
    universe = pd.read_csv(PROJECT_ROOT / "data" / "universe.csv", dtype={"symbol": str})
    names = dict(zip(universe["symbol"].astype(str).str.zfill(6), universe["name"]))
    groups = dict(zip(universe["symbol"].astype(str).str.zfill(6), universe["market_group"]))
    for symbol in symbols:
        symbol = str(symbol).zfill(6)
        path = PROJECT_ROOT / "data" / "daily" / f"{symbol}.csv"
        if not path.exists():
            raise FileNotFoundError(f"missing stock daily csv: {path}")
        df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        df["date"] = pd.to_datetime(df["date"])
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)].sort_values("date").reset_index(drop=True)
        if len(df) < 260:
            raise RuntimeError(f"not enough stock history for {symbol}: {len(df)}")
        enriched = add_basic_indicators(df)
        enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1.0
        enriched["symbol"] = symbol
        enriched["name"] = names.get(symbol, symbol)
        enriched["asset_type"] = "stock"
        enriched["market_group"] = groups.get(symbol, "stock_validation")
        frames.append(enriched)
    panel = pd.concat(frames, ignore_index=True)
    return panel.dropna(
        subset=[
            "ret_20",
            "ret_60",
            "ret_120",
            "ma_20",
            "ma_60",
            "ma_gap_20_60",
            "volatility_20",
            "drawdown_20",
            "avg_amount_20",
            "fwd_ret_1",
        ]
    ).copy()


def validate_buy_and_hold(panel: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        symbol = str(symbol).zfill(6)
        one = panel[panel["symbol"] == symbol].copy()
        strategy = BuyAndHoldStrategy({"symbols": [symbol]})
        cfg = {
            "initial_capital": 1.0,
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "stamp_duty_rate": 0.0,
            "rebalance_frequency": "daily",
        }
        returns_df, _ = run_cross_sectional_backtest_from_panel(one, strategy, cfg)
        expected = one[["date", "fwd_ret_1"]].rename(columns={"fwd_ret_1": "expected_return"})
        merged = returns_df[["date", "return"]].merge(expected, on="date", how="inner")
        max_diff = float((merged["return"] - merged["expected_return"]).abs().max())
        engine_cum = float((1.0 + returns_df["return"]).prod() - 1.0)
        expected_cum = float((1.0 + expected["expected_return"]).prod() - 1.0)
        rows.append(
            {
                "case": "stock_buy_and_hold",
                "symbol": symbol,
                "status": "pass" if max_diff < 1e-12 and abs(engine_cum - expected_cum) < 1e-10 else "fail",
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "engine_cum_return": engine_cum,
                "expected_cum_return": expected_cum,
                "cum_return_diff": abs(engine_cum - expected_cum),
                "max_daily_return_diff": max_diff,
            }
        )
    return pd.DataFrame(rows)


def validate_momentum_strategy(panel: pd.DataFrame, max_positions: int) -> pd.DataFrame:
    cfg = {
        "max_positions": max_positions,
        "eligible_asset_types": ["stock"],
        "min_price_stock": 0.0,
        "min_avg_turnover_million_stock": 0.0,
        "min_ret_20": -999.0,
        "min_ret_60": -999.0,
        "min_ret_120": -999.0,
        "max_volatility_20": 0.0,
        "min_drawdown_20": -999.0,
        "require_ma_trend": False,
        "weight_ret_120": 0.45,
        "weight_ret_60": 0.30,
        "weight_ret_20": 0.10,
        "weight_ma_gap_20_60": 0.10,
        "weight_low_volatility": 0.05,
    }
    strategy = MomentumStrategy(cfg)
    backtest_cfg = {
        "initial_capital": 1.0,
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, _ = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)

    expected_rows = []
    for trade_date, day_df in panel.groupby("date"):
        day = day_df.copy()
        day["score"] = (
            day["ret_120"] * cfg["weight_ret_120"]
            + day["ret_60"] * cfg["weight_ret_60"]
            + day["ret_20"] * cfg["weight_ret_20"]
            + day["ma_gap_20_60"] * cfg["weight_ma_gap_20_60"]
            + (-day["volatility_20"]) * cfg["weight_low_volatility"]
        )
        selected = day.sort_values("score", ascending=False).head(max_positions)
        expected_return = float(selected["fwd_ret_1"].mean()) if not selected.empty else 0.0
        expected_rows.append({"date": trade_date, "expected_return": expected_return})
    expected = pd.DataFrame(expected_rows).sort_values("date")
    merged = returns_df[["date", "return"]].merge(expected, on="date", how="inner")
    max_diff = float((merged["return"] - merged["expected_return"]).abs().max())
    engine_cum = float((1.0 + returns_df["return"]).prod() - 1.0)
    expected_cum = float((1.0 + expected["expected_return"]).prod() - 1.0)
    return pd.DataFrame(
        [
            {
                "case": "stock_momentum_internal",
                "symbol": ",".join(sorted(panel["symbol"].unique())),
                "status": "pass" if max_diff < 1e-12 and abs(engine_cum - expected_cum) < 1e-10 else "fail",
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "engine_cum_return": engine_cum,
                "expected_cum_return": expected_cum,
                "cum_return_diff": abs(engine_cum - expected_cum),
                "max_daily_return_diff": max_diff,
            }
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate stock backtest and strategy basics against independent calculations.")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--end-date", default="2025-12-31")
    parser.add_argument("--max-positions", type=int, default=2)
    parser.add_argument("--output", default=str(PROJECT_ROOT / "outputs" / "validation" / "internal" / "stock_strategy_validation.csv"))
    args = parser.parse_args()

    symbols = [str(symbol).zfill(6) for symbol in args.symbols]
    panel = _load_stock_panel(symbols, args.start_date, args.end_date)
    result = pd.concat(
        [
            validate_buy_and_hold(panel, symbols),
            validate_momentum_strategy(panel, args.max_positions),
        ],
        ignore_index=True,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output, index=False)
    print(result.to_string(index=False))
    print(f"saved={output}")
    if (result["status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
