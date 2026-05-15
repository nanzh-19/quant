#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.engine.strategy import SMACrossStrategy


SOURCE_REPO = "https://github.com/kernc/backtesting.py"
SOURCE_COMMIT = "6e9016c7b30d985137cde3fe24e1d39785c5e3a7"
SOURCE_FILE = "backtesting/test/GOOG.csv"
SOURCE_URL = f"https://raw.githubusercontent.com/kernc/backtesting.py/{SOURCE_COMMIT}/{SOURCE_FILE}"

FAST_WINDOW = 10
SLOW_WINDOW = 20
INITIAL_CASH = 10_000.0
PUBLIC_README_METRICS = {
    "equity_final": 68_935.12,
    "return_pct": 589.35,
    "buy_hold_return_pct": 703.46,
    "max_drawdown_pct": -33.08,
    "trades": 93,
    "win_rate_pct": 53.76,
    "best_trade_pct": 57.12,
    "worst_trade_pct": -16.63,
    "avg_trade_pct": 1.96,
    "profit_factor": 2.13,
}
CURRENT_COMMISSION_METRICS = {
    "equity_final": 56_263.52,
    "return_pct": 462.64,
    "trades": 93,
    "max_drawdown_pct": -33.93,
}


@dataclass
class Trade:
    size: int
    entry_price: float
    entry_bar: int
    entry_commission: float = 0.0
    exit_commission: float = 0.0
    exit_price: float | None = None
    exit_bar: int | None = None

    def gross_pnl(self, current_price: float | None = None) -> float:
        price = self.exit_price if self.exit_price is not None else current_price
        if price is None:
            raise ValueError("current_price is required for open trades")
        return self.size * (price - self.entry_price)

    def pnl(self, current_price: float | None = None) -> float:
        pnl = self.gross_pnl(current_price=current_price)
        if self.exit_price is not None:
            pnl -= self.entry_commission + self.exit_commission
        return pnl

    def return_pct(self) -> float:
        if self.exit_price is None:
            raise ValueError("closed trade is required")
        direction = 1.0 if self.size > 0 else -1.0
        gross_return = direction * (self.exit_price / self.entry_price - 1.0)
        commission_pct = (self.entry_commission + self.exit_commission) / (abs(self.size) * self.entry_price)
        return gross_return - commission_pct


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


def _build_signal_frame(prices: pd.DataFrame) -> pd.DataFrame:
    frame = prices.copy()
    frame[f"ma_{FAST_WINDOW}"] = frame["Close"].rolling(FAST_WINDOW).mean()
    frame[f"ma_{SLOW_WINDOW}"] = frame["Close"].rolling(SLOW_WINDOW).mean()
    return frame


def _is_cross_up(frame: pd.DataFrame, index: int) -> bool:
    strategy = SMACrossStrategy(
        {
            "fast_window": FAST_WINDOW,
            "slow_window": SLOW_WINDOW,
            "fast_column": f"ma_{FAST_WINDOW}",
            "slow_column": f"ma_{SLOW_WINDOW}",
        }
    )
    return strategy.signal(frame.iloc[: index + 1]) == 1


def _is_cross_down(frame: pd.DataFrame, index: int) -> bool:
    strategy = SMACrossStrategy(
        {
            "fast_window": FAST_WINDOW,
            "slow_window": SLOW_WINDOW,
            "fast_column": f"ma_{FAST_WINDOW}",
            "slow_column": f"ma_{SLOW_WINDOW}",
        }
    )
    return strategy.signal(frame.iloc[: index + 1]) == -1


def _process_orders(
    orders: list[dict],
    open_price: float,
    close_price: float,
    cash_state: dict[str, float],
    open_trades: list[Trade],
    closed_trades: list[Trade],
    bar_index: int,
    spread: float,
    commission: float,
) -> None:
    for order in list(orders):
        if order not in orders:
            continue
        if order["type"] == "close":
            trade = order["trade"]
            if trade in open_trades:
                open_trades.remove(trade)
                trade.exit_price = open_price
                trade.exit_bar = bar_index
                trade.exit_commission = abs(trade.size) * open_price * commission
                closed_trades.append(trade)
                cash_state["cash"] += trade.gross_pnl() - trade.exit_commission
            orders.remove(order)
            continue

        requested_size = float(order["size"])
        is_long = requested_size > 0
        adjusted_price = open_price * (1.0 + (spread if is_long else -spread))
        commission_per_unit = open_price * commission
        adjusted_price_plus_commission = adjusted_price + commission_per_unit
        equity = cash_state["cash"] + sum(trade.pnl(close_price) for trade in open_trades)
        margin_used = sum(abs(trade.size) * close_price for trade in open_trades)
        margin_available = max(0.0, equity - margin_used)

        size = requested_size
        if -1.0 < size < 1.0:
            size = math.copysign(int((margin_available * abs(size)) // adjusted_price_plus_commission), size)
        if size and abs(size) * adjusted_price_plus_commission <= margin_available:
            entry_commission = abs(size) * open_price * commission
            open_trades.append(
                Trade(
                    size=int(size),
                    entry_price=adjusted_price,
                    entry_bar=bar_index,
                    entry_commission=entry_commission,
                )
            )
            cash_state["cash"] -= entry_commission
        orders.remove(order)


def _run_smacross(
    prices: pd.DataFrame,
    *,
    spread: float,
    commission: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float]]:
    frame = _build_signal_frame(prices)
    start_bar = 1 + (SLOW_WINDOW - 1)
    cash_state = {"cash": INITIAL_CASH}
    open_trades: list[Trade] = []
    closed_trades: list[Trade] = []
    orders: list[dict] = []
    equity = [np.nan] * len(frame)

    full_equity_order = 1.0 - sys.float_info.epsilon
    for bar_index in range(start_bar, len(frame)):
        row = frame.iloc[bar_index]
        _process_orders(
            orders=orders,
            open_price=float(row["Open"]),
            close_price=float(row["Close"]),
            cash_state=cash_state,
            open_trades=open_trades,
            closed_trades=closed_trades,
            bar_index=bar_index,
            spread=spread,
            commission=commission,
        )
        equity[bar_index] = cash_state["cash"] + sum(trade.pnl(float(row["Close"])) for trade in open_trades)

        if _is_cross_up(frame, bar_index):
            for trade in list(open_trades):
                orders.insert(0, {"type": "close", "trade": trade})
            orders.append({"type": "open", "size": full_equity_order})
        elif _is_cross_down(frame, bar_index):
            for trade in list(open_trades):
                orders.insert(0, {"type": "close", "trade": trade})
            orders.append({"type": "open", "size": -full_equity_order})

    equity_curve = pd.DataFrame(
        {
            "date": frame.index,
            "equity": pd.Series(equity).bfill().fillna(cash_state["cash"]).to_numpy(),
        }
    )
    equity_curve["return"] = equity_curve["equity"].pct_change().fillna(0.0)
    equity_curve["drawdown"] = equity_curve["equity"] / equity_curve["equity"].cummax() - 1.0

    trades = pd.DataFrame(
        [
            {
                "size": trade.size,
                "entry_bar": trade.entry_bar,
                "exit_bar": trade.exit_bar,
                "entry_time": frame.index[trade.entry_bar],
                "exit_time": frame.index[trade.exit_bar] if trade.exit_bar is not None else pd.NaT,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "entry_commission": trade.entry_commission,
                "exit_commission": trade.exit_commission,
                "pnl": trade.pnl(),
                "return_pct": trade.return_pct(),
            }
            for trade in closed_trades
        ]
    )
    metrics = _summarize(prices=prices, equity_curve=equity_curve, trades=trades)
    return equity_curve, trades, metrics


def _summarize(prices: pd.DataFrame, equity_curve: pd.DataFrame, trades: pd.DataFrame) -> dict[str, float]:
    equity_final = float(equity_curve["equity"].iloc[-1])
    returns = trades["return_pct"] if not trades.empty else pd.Series(dtype=float)
    pnl = trades["pnl"] if not trades.empty else pd.Series(dtype=float)
    positive_returns = returns[returns > 0]
    negative_returns = returns[returns < 0]
    avg_trade = float(np.exp(np.log(returns + 1.0).sum() / len(returns)) - 1.0) if len(returns) else 0.0
    first_trading_bar = SLOW_WINDOW - 1
    return {
        "equity_final": equity_final,
        "return_pct": (equity_final / float(equity_curve["equity"].iloc[0]) - 1.0) * 100.0,
        "buy_hold_return_pct": (float(prices["Close"].iloc[-1]) / float(prices["Close"].iloc[0]) - 1.0) * 100.0,
        "buy_hold_return_pct_from_warmup": (
            float(prices["Close"].iloc[-1]) / float(prices["Close"].iloc[first_trading_bar]) - 1.0
        )
        * 100.0,
        "max_drawdown_pct": float(equity_curve["drawdown"].min() * 100.0),
        "trades": int(len(trades)),
        "win_rate_pct": float((pnl > 0).mean() * 100.0) if len(pnl) else 0.0,
        "best_trade_pct": float(returns.max() * 100.0) if len(returns) else 0.0,
        "worst_trade_pct": float(returns.min() * 100.0) if len(returns) else 0.0,
        "avg_trade_pct": avg_trade * 100.0,
        "profit_factor": float(positive_returns.sum() / abs(negative_returns.sum())) if abs(negative_returns.sum()) > 0 else 0.0,
    }


def _status(actual: float, expected: float, tolerance: float) -> str:
    return "pass" if abs(actual - expected) <= tolerance else "fail"


def _matches_public_readme(metrics: dict[str, float]) -> bool:
    checks = [
        ("equity_final", 0.01),
        ("return_pct", 0.01),
        ("buy_hold_return_pct", 0.01),
        ("max_drawdown_pct", 0.01),
        ("win_rate_pct", 0.01),
        ("best_trade_pct", 0.01),
        ("worst_trade_pct", 0.01),
        ("avg_trade_pct", 0.01),
        ("profit_factor", 0.01),
    ]
    return all(_status(metrics[key], PUBLIC_README_METRICS[key], tolerance) == "pass" for key, tolerance in checks) and int(
        metrics["trades"]
    ) == PUBLIC_README_METRICS["trades"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate public GOOG SmaCross result from backtesting.py README.")
    parser.add_argument(
        "--cache-path",
        default=str(PROJECT_ROOT / "outputs" / "validation" / "external" / "sources" / SOURCE_COMMIT / "GOOG.csv"),
    )
    args = parser.parse_args()

    source_path = _download_source(Path(args.cache_path))
    prices = _load_prices(source_path)
    public_equity, public_trades, public_metrics = _run_smacross(prices, spread=0.002, commission=0.0)
    current_equity, current_trades, current_metrics = _run_smacross(prices, spread=0.0, commission=0.002)

    summary_rows = [
        {
            "source": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "scenario": "public_readme_table_legacy_spread_0.002",
            "strategy": f"GOOG SmaCross SMA{FAST_WINDOW}/{SLOW_WINDOW}, next-open execution, exclusive orders, long/short",
            "note": "Matches README public table; table corresponds to legacy spread=.002 economics.",
            **public_metrics,
            "public_return_pct": PUBLIC_README_METRICS["return_pct"],
            "public_equity_final": PUBLIC_README_METRICS["equity_final"],
            "public_max_drawdown_pct": PUBLIC_README_METRICS["max_drawdown_pct"],
            "public_trades": PUBLIC_README_METRICS["trades"],
            "status": "pass" if _matches_public_readme(public_metrics) else "fail",
        },
        {
            "source": SOURCE_REPO,
            "commit": SOURCE_COMMIT,
            "scenario": "current_code_commission_0.002_equivalent_check",
            "strategy": f"GOOG SmaCross SMA{FAST_WINDOW}/{SLOW_WINDOW}, next-open execution, exclusive orders, long/short",
            "note": "Current backtesting.py applies commission at entry and exit; README code text with commission=.002 gives this lower result.",
            **{
                "equity_final": current_metrics["equity_final"],
                "return_pct": current_metrics["return_pct"],
                "buy_hold_return_pct": current_metrics["buy_hold_return_pct"],
                "buy_hold_return_pct_from_warmup": current_metrics["buy_hold_return_pct_from_warmup"],
                "max_drawdown_pct": current_metrics["max_drawdown_pct"],
                "trades": current_metrics["trades"],
                "win_rate_pct": current_metrics["win_rate_pct"],
                "best_trade_pct": current_metrics["best_trade_pct"],
                "worst_trade_pct": current_metrics["worst_trade_pct"],
                "avg_trade_pct": current_metrics["avg_trade_pct"],
                "profit_factor": current_metrics["profit_factor"],
            },
            "public_return_pct": CURRENT_COMMISSION_METRICS["return_pct"],
            "public_equity_final": CURRENT_COMMISSION_METRICS["equity_final"],
            "public_max_drawdown_pct": CURRENT_COMMISSION_METRICS["max_drawdown_pct"],
            "public_trades": CURRENT_COMMISSION_METRICS["trades"],
            "status": "diagnostic",
        },
    ]
    summary = pd.DataFrame(summary_rows)

    out_dir = PROJECT_ROOT / "outputs" / "validation" / "external" / "stock_smacross"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "replication_external_stock_smacross_summary.csv"
    public_equity_path = out_dir / "replication_external_stock_smacross_public_equity.csv"
    public_trades_path = out_dir / "replication_external_stock_smacross_public_trades.csv"
    current_equity_path = out_dir / "replication_external_stock_smacross_current_equity.csv"
    current_trades_path = out_dir / "replication_external_stock_smacross_current_trades.csv"
    summary.to_csv(summary_path, index=False)
    public_equity.to_csv(public_equity_path, index=False)
    public_trades.to_csv(public_trades_path, index=False)
    current_equity.to_csv(current_equity_path, index=False)
    current_trades.to_csv(current_trades_path, index=False)

    print(summary.to_string(index=False))
    print(f"saved={summary_path}")
    if (summary[summary["scenario"] == "public_readme_table_legacy_spread_0.002"]["status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
