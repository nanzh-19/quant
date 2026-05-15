#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel
from quant.strategy import BaseStrategy


SOURCE_REPO = "https://github.com/10mohi6/portfolio-backtest-python"
SOURCE_COMMIT = "065c41df1d5dc02dad7b18af95e37fed0db4c8b7"
SOURCE_FILE = "tests/VTI-AGG-GLD-2011-04-10-2021-04-10.csv"
SOURCE_URL = f"https://raw.githubusercontent.com/10mohi6/portfolio-backtest-python/{SOURCE_COMMIT}/{SOURCE_FILE}"
PUBLIC_CUMULATIVE_RETURN_PCT = 160.9
WEIGHTS = {"VTI": 0.60, "AGG": 0.25, "GLD": 0.15}


class FixedWeightStrategy(BaseStrategy):
    def __init__(self, weights: dict[str, float]) -> None:
        self.weights = weights

    @property
    def name(self) -> str:
        return "external_fixed_weight_portfolio"

    def params(self) -> dict:
        return {"weights": self.weights, "source": SOURCE_REPO, "commit": SOURCE_COMMIT}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot.head(0)
        selected = snapshot[snapshot["symbol"].isin(self.weights)].copy()
        if selected.empty:
            return snapshot.head(0)
        selected["weight"] = selected["symbol"].map(self.weights)
        selected["score"] = selected["weight"]
        selected["reason"] = "external_fixed_weight"
        return selected.sort_values("symbol").reset_index(drop=True)


def _download_source(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=30) as response:
            cache_path.write_bytes(response.read())
    return cache_path


def _load_prices(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, index_col="Date", parse_dates=True).sort_index()


def _external_reference(prices: pd.DataFrame) -> pd.DataFrame:
    weight_vector = pd.Series(WEIGHTS)
    returns = prices.pct_change().mul(weight_vector, axis=1).sum(axis=1).fillna(0.0)
    out = pd.DataFrame({"date": returns.index, "expected_return": returns.values})
    out["expected_equity"] = (1.0 + out["expected_return"]).cumprod()
    out["expected_cumulative_return_pct"] = (out["expected_equity"] - 1.0) * 100.0
    return out


def _build_engine_panel(prices: pd.DataFrame) -> pd.DataFrame:
    rows = []
    returns = prices.pct_change().fillna(0.0)
    for date in prices.index:
        for symbol in prices.columns:
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "symbol": str(symbol),
                    "name": str(symbol),
                    "asset_type": "external_etf",
                    "market_group": "fixed_weight",
                    "close": float(prices.loc[date, symbol]),
                    "ret_20": 0.0,
                    "ret_60": 0.0,
                    "avg_amount_20": 1.0,
                    "fwd_ret_1": float(returns.loc[date, symbol]),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate external fixed-weight ETF portfolio sample.")
    parser.add_argument(
        "--cache-path",
        default=str(
            PROJECT_ROOT
            / "outputs"
            / "validation"
            / "external"
            / "sources"
            / SOURCE_COMMIT
            / "VTI-AGG-GLD-2011-04-10-2021-04-10.csv"
        ),
    )
    args = parser.parse_args()

    source_path = _download_source(Path(args.cache_path))
    prices = _load_prices(source_path)
    expected = _external_reference(prices)
    panel = _build_engine_panel(prices)
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(
        panel,
        FixedWeightStrategy(WEIGHTS),
        {
            "initial_capital": 1.0,
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "stamp_duty_rate": 0.0,
            "rebalance_frequency": "daily",
        },
    )
    merged = returns_df[["date", "return", "equity"]].merge(expected, on="date", how="inner")
    max_daily_return_diff = float((merged["return"] - merged["expected_return"]).abs().max())
    max_equity_diff = float((merged["equity"] - merged["expected_equity"]).abs().max())
    engine_cumulative_return_pct = float((returns_df["equity"].iloc[-1] - 1.0) * 100.0)
    external_cumulative_return_pct = float(expected["expected_cumulative_return_pct"].iloc[-1])

    result = pd.DataFrame(
        [
            {
                "source": SOURCE_REPO,
                "commit": SOURCE_COMMIT,
                "public_cumulative_return_pct": PUBLIC_CUMULATIVE_RETURN_PCT,
                "external_cumulative_return_pct": external_cumulative_return_pct,
                "engine_cumulative_return_pct": engine_cumulative_return_pct,
                "public_status": "pass" if abs(external_cumulative_return_pct - PUBLIC_CUMULATIVE_RETURN_PCT) <= 0.05 else "fail",
                "engine_status": "pass" if max_daily_return_diff <= 1e-12 and max_equity_diff <= 1e-12 else "fail",
                "max_daily_return_diff": max_daily_return_diff,
                "max_equity_diff": max_equity_diff,
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
                "weights": ",".join(f"{symbol}:{weight}" for symbol, weight in WEIGHTS.items()),
            }
        ]
    )
    out_dir = PROJECT_ROOT / "outputs" / "validation" / "external" / "fixed_weight_portfolio"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "replication_external_fixed_weight_portfolio_summary.csv"
    returns_path = out_dir / "replication_external_fixed_weight_portfolio_returns.csv"
    picks_path = out_dir / "replication_external_fixed_weight_portfolio_picks.csv"
    result.to_csv(summary_path, index=False)
    returns_df.to_csv(returns_path, index=False)
    picks_df.to_csv(picks_path, index=False)

    print(result.to_string(index=False))
    print(f"saved={summary_path}")
    if (result["public_status"] != "pass").any() or (result["engine_status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
