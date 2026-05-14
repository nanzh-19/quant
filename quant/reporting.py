from __future__ import annotations

from pathlib import Path

import pandas as pd


def build_backtest_diagnostics(
    returns_df: pd.DataFrame,
    picks_df: pd.DataFrame,
    outputs_dir: Path,
) -> dict[str, pd.DataFrame]:
    outputs_dir.mkdir(parents=True, exist_ok=True)
    if returns_df.empty:
        empty = pd.DataFrame()
        return {
            "monthly_returns": empty,
            "yearly_returns": empty,
            "drawdowns": empty,
            "turnover": empty,
            "holding_counts": empty,
        }

    frame = returns_df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").reset_index(drop=True)
    frame["year"] = frame["date"].dt.year
    frame["month"] = frame["date"].dt.to_period("M").astype(str)
    frame["year_month"] = frame["date"].dt.strftime("%Y-%m")

    monthly_returns = (
        frame.groupby("year_month")
        .agg(
            start_date=("date", "min"),
            end_date=("date", "max"),
            return_=("return", lambda s: float((1.0 + s).prod() - 1.0)),
            gross_return=("gross_return", lambda s: float((1.0 + s).prod() - 1.0)),
            cost=("cost", "sum"),
            avg_turnover=("turnover", "mean"),
            avg_positions=("positions", "mean"),
        )
        .reset_index()
        .rename(columns={"return_": "return"})
    )

    yearly_returns = (
        frame.groupby("year")
        .agg(
            start_date=("date", "min"),
            end_date=("date", "max"),
            return_=("return", lambda s: float((1.0 + s).prod() - 1.0)),
            gross_return=("gross_return", lambda s: float((1.0 + s).prod() - 1.0)),
            cost=("cost", "sum"),
            avg_turnover=("turnover", "mean"),
            avg_positions=("positions", "mean"),
            trading_days=("date", "count"),
        )
        .reset_index()
        .rename(columns={"return_": "return"})
    )

    equity = frame["equity"]
    drawdown = equity / equity.cummax() - 1.0
    drawdowns = frame[["date", "equity"]].copy()
    drawdowns["drawdown"] = drawdown
    drawdowns["cummax_equity"] = equity.cummax()

    turnover = frame[["date", "turnover", "positions", "rebalanced", "cost"]].copy()
    holding_counts = pd.DataFrame()
    if not picks_df.empty and "date" in picks_df.columns:
        picks = picks_df.copy()
        picks["date"] = pd.to_datetime(picks["date"])
        holding_counts = (
            picks.groupby("date")
            .agg(picks=("symbol", "count"), avg_score=("score", "mean"))
            .reset_index()
        )

    outputs = {
        "monthly_returns": monthly_returns,
        "yearly_returns": yearly_returns,
        "drawdowns": drawdowns,
        "turnover": turnover,
        "holding_counts": holding_counts,
    }
    for name, df in outputs.items():
        df.to_csv(outputs_dir / f"backtest_{name}.csv", index=False)
    return outputs
