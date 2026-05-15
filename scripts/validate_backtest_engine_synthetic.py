#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel
from quant.strategy import BaseStrategy


class ScheduledSelectionStrategy(BaseStrategy):
    def __init__(self, schedule: dict[str, list[str]]) -> None:
        self.schedule = {pd.Timestamp(date): [str(symbol) for symbol in symbols] for date, symbols in schedule.items()}

    @property
    def name(self) -> str:
        return "scheduled_selection"

    def params(self) -> dict:
        return {"schedule": {str(date.date()): symbols for date, symbols in self.schedule.items()}}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot.head(0)
        trade_date = pd.Timestamp(snapshot["date"].iloc[0])
        symbols = self.schedule.get(trade_date, [])
        if not symbols:
            return snapshot.head(0)
        ranked = snapshot[snapshot["symbol"].astype(str).isin(symbols)].copy()
        if ranked.empty:
            return snapshot.head(0)
        ranked["score"] = ranked["symbol"].map({symbol: len(symbols) - idx for idx, symbol in enumerate(symbols)})
        ranked["reason"] = "synthetic_schedule"
        return ranked.reset_index(drop=True)


def _synthetic_panel() -> pd.DataFrame:
    dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    returns = {
        "AAA": [0.10, -0.05, 0.02, 0.00],
        "BBB": [0.00, 0.04, -0.02, 0.00],
    }
    rows = []
    for date in dates:
        for symbol in ["AAA", "BBB"]:
            rows.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "name": symbol,
                    "asset_type": "etf",
                    "market_group": "synthetic",
                    "close": 1.0,
                    "ret_20": 0.0,
                    "ret_60": 0.0,
                    "avg_amount_20": 1_000_000.0,
                    "fwd_ret_1": returns[symbol][list(dates).index(date)],
                }
            )
    return pd.DataFrame(rows)


def _assert_close(actual: float, expected: float, name: str, tol: float = 1e-12) -> None:
    if abs(actual - expected) > tol:
        raise AssertionError(f"{name}: actual={actual:.16f}, expected={expected:.16f}, diff={abs(actual - expected):.16f}")


def validate_daily_rebalance_costs() -> pd.DataFrame:
    panel = _synthetic_panel()
    schedule = {
        "2024-01-02": ["AAA"],
        "2024-01-03": ["AAA", "BBB"],
        "2024-01-04": ["BBB"],
        "2024-01-05": [],
    }
    strategy = ScheduledSelectionStrategy(schedule)
    cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.001,
        "slippage_rate": 0.002,
        "stamp_duty_rate": 0.003,
        "rebalance_frequency": "daily",
    }
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, cfg)
    expected = pd.DataFrame(
        [
            {
                "date": pd.Timestamp("2024-01-02"),
                "gross_return": 0.10,
                "cost": 0.003,
                "turnover": 1.0,
                "positions": 1,
                "return": 0.097,
            },
            {
                "date": pd.Timestamp("2024-01-03"),
                "gross_return": (-0.05 + 0.04) / 2.0,
                "cost": 0.0045,
                "turnover": 1.0,
                "positions": 2,
                "return": -0.0095,
            },
            {
                "date": pd.Timestamp("2024-01-04"),
                "gross_return": -0.02,
                "cost": 0.004296482412060302,
                "turnover": 0.9547738693467337,
                "positions": 1,
                "return": -0.0242964824120603,
            },
            {
                "date": pd.Timestamp("2024-01-05"),
                "gross_return": 0.0,
                "cost": 0.006,
                "turnover": 1.0,
                "positions": 0,
                "return": -0.006,
            },
        ]
    )

    merged = returns_df.merge(expected, on="date", suffixes=("_actual", "_expected"), how="outer")
    if len(merged) != len(expected):
        raise AssertionError(f"unexpected row count: actual={len(returns_df)}, expected={len(expected)}")

    for row in merged.itertuples(index=False):
        for col in ["gross_return", "cost", "turnover", "return"]:
            _assert_close(getattr(row, f"{col}_actual"), getattr(row, f"{col}_expected"), f"{row.date.date()} {col}")
        if int(row.positions_actual) != int(row.positions_expected):
            raise AssertionError(f"{row.date.date()} positions: actual={row.positions_actual}, expected={row.positions_expected}")

    expected_equity = 1_000_000 * (1 + expected["return"]).cumprod()
    for actual, expected_value in zip(returns_df["equity"], expected_equity):
        _assert_close(float(actual), float(expected_value), "equity", tol=1e-6)

    expected_pick_rows = 4
    if len(picks_df) != expected_pick_rows:
        raise AssertionError(f"pick rows: actual={len(picks_df)}, expected={expected_pick_rows}")

    return returns_df


def validate_weekly_rebalance_state() -> pd.DataFrame:
    panel = _synthetic_panel()
    schedule = {
        "2024-01-02": ["AAA"],
        "2024-01-03": ["BBB"],
        "2024-01-04": [],
        "2024-01-05": ["BBB"],
    }
    strategy = ScheduledSelectionStrategy(schedule)
    cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "slippage_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "rebalance_frequency": "weekly",
    }
    returns_df, _ = run_cross_sectional_backtest_from_panel(panel, strategy, cfg)
    expected_returns = [0.10, -0.05, 0.02, 0.00]
    expected_positions = [1, 1, 1, 1]
    for idx, row in returns_df.reset_index(drop=True).iterrows():
        _assert_close(float(row["return"]), expected_returns[idx], f"weekly return {idx}")
        if int(row["positions"]) != expected_positions[idx]:
            raise AssertionError(f"weekly positions {idx}: actual={row['positions']}, expected={expected_positions[idx]}")
    return returns_df


def main() -> None:
    daily = validate_daily_rebalance_costs()
    weekly = validate_weekly_rebalance_state()
    print("synthetic_daily_rebalance=pass")
    print(daily[["date", "gross_return", "cost", "turnover", "positions", "return", "equity"]].to_string(index=False))
    print("synthetic_weekly_rebalance=pass")
    print(weekly[["date", "gross_return", "cost", "turnover", "positions", "return", "equity"]].to_string(index=False))


if __name__ == "__main__":
    main()
