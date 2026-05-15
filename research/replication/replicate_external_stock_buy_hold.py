#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.engine.backtest import run_cross_sectional_backtest_from_panel
from quant.engine.strategy import BuyAndHoldStrategy


SOURCE_REPO = "https://github.com/kernc/backtesting.py"
SOURCE_COMMIT = "6e9016c7b30d985137cde3fe24e1d39785c5e3a7"
SOURCE_FILE = "backtesting/test/GOOG.csv"
SOURCE_URL = f"https://raw.githubusercontent.com/kernc/backtesting.py/{SOURCE_COMMIT}/{SOURCE_FILE}"
PUBLIC_BUY_HOLD_RETURN_PCT = 703.46


def _download_source(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=30) as response:
            cache_path.write_bytes(response.read())
    return cache_path


def _load_prices(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index.name = "date"
    return df.sort_index()


def _build_panel(prices: pd.DataFrame) -> pd.DataFrame:
    out = prices.reset_index().rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    out["date"] = pd.to_datetime(out["date"])
    out["amount"] = out["close"] * out["volume"]
    out["ret_20"] = 0.0
    out["ret_60"] = 0.0
    out["avg_amount_20"] = 1.0
    out["fwd_ret_1"] = out["close"].pct_change().fillna(0.0)
    out["symbol"] = "GOOG"
    out["name"] = "GOOG"
    out["asset_type"] = "external_stock"
    out["market_group"] = "external_stock_validation"
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate public GOOG buy-and-hold result from backtesting.py sample data.")
    parser.add_argument(
        "--cache-path",
        default=str(PROJECT_ROOT / "outputs" / "validation" / "external" / "sources" / SOURCE_COMMIT / "GOOG.csv"),
    )
    args = parser.parse_args()

    source_path = _download_source(Path(args.cache_path))
    prices = _load_prices(source_path)
    panel = _build_panel(prices)
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(
        panel,
        BuyAndHoldStrategy({"symbols": ["GOOG"]}),
        {
            "initial_capital": 1.0,
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "stamp_duty_rate": 0.0,
            "rebalance_frequency": "daily",
        },
    )
    engine_return_pct = float((returns_df["equity"].iloc[-1] - 1.0) * 100.0)
    external_return_pct = float((prices["Close"].iloc[-1] / prices["Close"].iloc[0] - 1.0) * 100.0)
    max_daily_return_diff = float(
        (
            returns_df["return"].reset_index(drop=True)
            - prices["Close"].pct_change().fillna(0.0).reset_index(drop=True)
        ).abs().max()
    )
    result = pd.DataFrame(
        [
            {
                "source": SOURCE_REPO,
                "commit": SOURCE_COMMIT,
                "strategy": "GOOG buy and hold",
                "public_buy_hold_return_pct": PUBLIC_BUY_HOLD_RETURN_PCT,
                "external_buy_hold_return_pct": external_return_pct,
                "engine_buy_hold_return_pct": engine_return_pct,
                "public_status": "pass" if abs(external_return_pct - PUBLIC_BUY_HOLD_RETURN_PCT) <= 0.01 else "fail",
                "engine_status": "pass" if max_daily_return_diff <= 1e-12 and abs(engine_return_pct - external_return_pct) <= 1e-10 else "fail",
                "max_daily_return_diff": max_daily_return_diff,
                "start_date": str(returns_df["date"].iloc[0].date()),
                "end_date": str(returns_df["date"].iloc[-1].date()),
                "days": len(returns_df),
            }
        ]
    )
    out_dir = PROJECT_ROOT / "outputs" / "validation" / "external" / "stock_buy_hold"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "replication_external_stock_buy_hold_summary.csv"
    returns_path = out_dir / "replication_external_stock_buy_hold_returns.csv"
    picks_path = out_dir / "replication_external_stock_buy_hold_picks.csv"
    result.to_csv(summary_path, index=False)
    returns_df.to_csv(returns_path, index=False)
    picks_df.to_csv(picks_path, index=False)

    print(result.to_string(index=False))
    print(f"saved={summary_path}")
    if (result["public_status"] != "pass").any() or (result["engine_status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
