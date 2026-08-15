from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_nasdaq_sp500_dual_speed_grid import dual_speed_weights, fast_metrics  # noqa: E402
from research_nasdaq_sp500_more_strategies import (  # noqa: E402
    BASE_WEIGHTS,
    SAFE_SYMBOL,
    PairCase,
    build_prices,
    with_safe,
)
from research_nasdaq_sp500_offensive_strategies import drawdown_ma_filter, ma_band_filter  # noqa: E402


OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_weekly_signals"
GROWTH_SYMBOL = "513300"
STABLE_SYMBOL = "513500"
TOTAL_CAPITAL = 300_000.0
LOT_SIZE = 100

CAPS = {
    GROWTH_SYMBOL: 180_000.0,
    STABLE_SYMBOL: 120_000.0,
}

STRATEGY_LABELS = {
    "hold_60_40": "持仓不动 60/40",
    "defensive_dual_speed_vt11": "防守稳健档：双速度 MA40/150 + 11%波动目标",
    "defensive_dual_speed_vt13": "防守均衡档：双速度 MA40/150 + 13%波动目标",
    "offensive_dd_ma200_h120_cut10_rec05_weak50": "进攻候选：MA200+回撤 10%/5%",
    "offensive_ma200_weak50": "进攻候选：MA200 简单过滤 50%",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate weekly signals for the Nasdaq/S&P500 ETF basket.")
    parser.add_argument("--capital", type=float, default=TOTAL_CAPITAL, help="Total capital allocated to this basket.")
    parser.add_argument("--growth-cap", type=float, default=CAPS[GROWTH_SYMBOL], help=f"Amount cap for {GROWTH_SYMBOL}.")
    parser.add_argument("--stable-cap", type=float, default=CAPS[STABLE_SYMBOL], help=f"Amount cap for {STABLE_SYMBOL}.")
    parser.add_argument(
        "--holdings",
        type=Path,
        default=ROOT / "data" / "holdings.csv",
        help="Local holdings CSV. Expected columns include symbol and shares.",
    )
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="Output directory for weekly signal files.")
    return parser.parse_args()


def read_current_shares(path: Path) -> dict[str, float]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    df = pd.read_csv(path, dtype={"symbol": str})
    if "symbol" not in df.columns or "shares" not in df.columns:
        return {}
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0.0)
    return dict(zip(df["symbol"].astype(str), df["shares"].astype(float), strict=False))


def build_current_prices() -> pd.DataFrame:
    case = PairCase("current_513300_513500", GROWTH_SYMBOL, STABLE_SYMBOL)
    return build_prices(case)


def hold_weights(prices: pd.DataFrame) -> pd.DataFrame:
    weights = pd.DataFrame(
        {
            GROWTH_SYMBOL: BASE_WEIGHTS[0],
            STABLE_SYMBOL: BASE_WEIGHTS[1],
        },
        index=prices.index,
    )
    return with_safe(weights)


def build_strategy_weights(prices: pd.DataFrame) -> dict[str, pd.DataFrame]:
    symbols = [GROWTH_SYMBOL, STABLE_SYMBOL]
    return {
        "hold_60_40": hold_weights(prices),
        "defensive_dual_speed_vt11": dual_speed_weights(
            prices,
            growth_symbol=GROWTH_SYMBOL,
            stable_symbol=STABLE_SYMBOL,
            fast_ma=40,
            slow_ma=150,
            fast_score=0.25,
            slow_score=0.75,
            target_vol=0.11,
        ),
        "defensive_dual_speed_vt13": dual_speed_weights(
            prices,
            growth_symbol=GROWTH_SYMBOL,
            stable_symbol=STABLE_SYMBOL,
            fast_ma=40,
            slow_ma=150,
            fast_score=0.25,
            slow_score=0.75,
            target_vol=0.13,
        ),
        "offensive_dd_ma200_h120_cut10_rec05_weak50": drawdown_ma_filter(
            prices,
            symbols=symbols,
            ma_window=200,
            high_window=120,
            cut=0.10,
            recover=0.05,
            weak_fraction=0.50,
        ),
        "offensive_ma200_weak50": ma_band_filter(
            prices,
            symbols=symbols,
            ma_window=200,
            band=0.00,
            weak_fraction=0.50,
        ),
    }


def latest_signal(weights: pd.DataFrame) -> str:
    risk = float(weights[[GROWTH_SYMBOL, STABLE_SYMBOL]].iloc[-1].sum())
    if risk >= 0.99:
        return "满仓"
    if risk >= 0.75:
        return "偏进攻"
    if risk >= 0.45:
        return "半仓/中性"
    return "防守"


def rounded_lot_shares(amount: float, price: float) -> int:
    if price <= 0:
        return 0
    return int(np.floor(amount / price / LOT_SIZE) * LOT_SIZE)


def target_rows(
    strategy: str,
    weights: pd.Series,
    prices: pd.Series,
    current_shares_by_symbol: dict[str, float],
    total_capital: float,
    caps: dict[str, float],
) -> list[dict]:
    rows = []
    for symbol in [GROWTH_SYMBOL, STABLE_SYMBOL, SAFE_SYMBOL]:
        weight = float(weights.get(symbol, 0.0))
        target_amount = weight * total_capital
        if symbol in caps:
            target_amount = min(target_amount, caps[symbol])
        price = float(prices.get(symbol, np.nan))
        target_shares = rounded_lot_shares(target_amount, price) if symbol != SAFE_SYMBOL else np.nan
        current_shares = current_shares_by_symbol.get(symbol, 0.0)
        current_amount = current_shares * price if symbol != SAFE_SYMBOL else 0.0
        rows.append(
            {
                "strategy": strategy,
                "strategy_label": STRATEGY_LABELS[strategy],
                "symbol": symbol,
                "price": price,
                "target_weight": weight,
                "target_amount": target_amount,
                "target_shares_lot": target_shares,
                "current_shares": current_shares if symbol != SAFE_SYMBOL else np.nan,
                "current_amount": current_amount if symbol != SAFE_SYMBOL else np.nan,
                "share_diff_lot": target_shares - current_shares if symbol != SAFE_SYMBOL else np.nan,
                "amount_diff": target_amount - current_amount if symbol != SAFE_SYMBOL else target_amount,
            }
        )
    return rows


def trailing_returns(prices: pd.DataFrame) -> dict[str, float]:
    portfolio = prices[GROWTH_SYMBOL] / prices[GROWTH_SYMBOL].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[STABLE_SYMBOL] / prices[STABLE_SYMBOL].iloc[0] * BASE_WEIGHTS[1]
    out = {}
    for days in [20, 60, 120]:
        if len(portfolio) > days:
            out[f"portfolio_ret_{days}d"] = float(portfolio.iloc[-1] / portfolio.iloc[-days - 1] - 1.0)
        else:
            out[f"portfolio_ret_{days}d"] = np.nan
    return out


def write_markdown(summary: pd.DataFrame, targets: pd.DataFrame, path: Path) -> None:
    latest_date = summary["date"].iloc[0]
    lines = [
        "# 纳指/标普 ETF 周度信号",
        "",
        f"日期：{latest_date}",
        "",
        "说明：本文件为本地研究输出，不推送远端仓库。",
        "",
        "## 策略摘要",
        "",
        summary[
            [
                "strategy_label",
                "signal",
                "risk_weight",
                "safe_weight",
                "cagr",
                "mdd",
                "sharpe",
                "portfolio_ret_20d",
                "portfolio_ret_60d",
                "portfolio_ret_120d",
            ]
        ].to_markdown(index=False, floatfmt=".4f"),
        "",
        "## 目标仓位",
        "",
        targets[
            [
                "strategy_label",
                "symbol",
                "price",
                "target_weight",
                "target_amount",
                "target_shares_lot",
                "current_shares",
                "share_diff_lot",
                "amount_diff",
            ]
        ].to_markdown(index=False, floatfmt=".2f"),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    current_shares_by_symbol = read_current_shares(args.holdings)
    caps = {
        GROWTH_SYMBOL: args.growth_cap,
        STABLE_SYMBOL: args.stable_cap,
    }
    prices = build_current_prices()
    strategies = build_strategy_weights(prices)
    latest_prices = prices.iloc[-1]
    latest_date = prices.index[-1].date().isoformat()
    trailing = trailing_returns(prices)

    summary_rows = []
    target_row_list = []
    for name, weights in strategies.items():
        weights = weights.reindex(prices.index).ffill().fillna(0.0)
        row = fast_metrics(prices, weights)
        latest_weights = weights.iloc[-1]
        row.update(
            {
                "date": latest_date,
                "strategy": name,
                "strategy_label": STRATEGY_LABELS[name],
                "signal": latest_signal(weights),
                "risk_weight": float(latest_weights.get(GROWTH_SYMBOL, 0.0) + latest_weights.get(STABLE_SYMBOL, 0.0)),
                "safe_weight": float(latest_weights.get(SAFE_SYMBOL, 0.0)),
                "growth_weight": float(latest_weights.get(GROWTH_SYMBOL, 0.0)),
                "stable_weight": float(latest_weights.get(STABLE_SYMBOL, 0.0)),
                **trailing,
            }
        )
        summary_rows.append(row)
        target_row_list.extend(
            target_rows(
                name,
                latest_weights,
                latest_prices,
                current_shares_by_symbol=current_shares_by_symbol,
                total_capital=args.capital,
                caps=caps,
            )
        )

    summary = pd.DataFrame(summary_rows)
    targets = pd.DataFrame(target_row_list)
    summary.to_csv(out_dir / "weekly_signal_summary.csv", index=False)
    targets.to_csv(out_dir / "weekly_signal_targets.csv", index=False)
    write_markdown(summary, targets, out_dir / "weekly_signal_report.md")
    summary.to_csv(out_dir / f"weekly_signal_summary_{latest_date}.csv", index=False)
    targets.to_csv(out_dir / f"weekly_signal_targets_{latest_date}.csv", index=False)
    write_markdown(summary, targets, out_dir / f"weekly_signal_report_{latest_date}.md")

    print(summary[["strategy", "signal", "risk_weight", "safe_weight", "cagr", "mdd", "sharpe"]].to_string(index=False))
    print(f"\nWrote {out_dir}")


if __name__ == "__main__":
    main()
