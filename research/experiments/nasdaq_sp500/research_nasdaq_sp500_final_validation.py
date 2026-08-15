from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_nasdaq_sp500_dual_speed_grid import dual_speed_weights, fast_metrics  # noqa: E402
from research_nasdaq_sp500_more_strategies import (  # noqa: E402
    BASE_WEIGHTS,
    SAFE_SYMBOL,
    read_close,
)


OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_final_validation"

NASDAQ = {
    "513300": "纳斯达克ETF华夏",
    "513100": "纳指ETF国泰",
    "159941": "纳指ETF广发",
    "159632": "纳斯达克ETF华安",
    "159501": "纳指ETF嘉实",
    "159513": "纳斯达克100ETF大成",
    "159659": "纳斯达克100ETF招商",
    "159660": "纳指ETF汇添富",
    "159696": "纳指ETF易方达",
    "513110": "纳指ETF华泰柏瑞",
    "513390": "纳指100ETF博时",
    "513870": "纳指ETF富国",
}

SP500 = {
    "513500": "标普500ETF博时",
    "159612": "标普500ETF国泰",
    "159655": "标普500ETF华夏",
    "513650": "标普500ETF南方",
}


def available_symbols(symbols: dict[str, str]) -> dict[str, str]:
    out = {}
    for symbol, name in symbols.items():
        if (ROOT / "data" / "daily" / f"{symbol}.csv").exists():
            out[symbol] = name
    return out


def build_prices(a: str, b: str) -> pd.DataFrame:
    prices = pd.concat([read_close(a), read_close(b), read_close(SAFE_SYMBOL)], axis=1, join="inner").dropna()
    return prices.sort_index()


def buy_hold_metrics(prices: pd.DataFrame, a: str, b: str) -> dict:
    """Initial 60/40 allocation, then hold without rebalancing."""
    risky = prices[[a, b]]
    normalized = risky / risky.iloc[0]
    equity = normalized[a] * BASE_WEIGHTS[0] + normalized[b] * BASE_WEIGHTS[1]
    returns = equity.pct_change().fillna(0.0)
    years = len(returns) / 252
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 and equity.iloc[-1] > 0 else float("nan")
    vol = float(returns.std(ddof=0) * (252 ** 0.5))
    drawdown = equity / equity.cummax() - 1.0
    mdd = float(drawdown.min())
    return {
        "hold_cumret": float(equity.iloc[-1] - 1.0),
        "hold_cagr": cagr,
        "hold_vol": vol,
        "hold_mdd": mdd,
        "hold_sharpe": float(cagr / vol) if vol > 0 else float("nan"),
    }


def run_pair(a: str, b: str, start: str | None = None) -> dict | None:
    prices = build_prices(a, b)
    if start:
        prices = prices[prices.index >= pd.Timestamp(start)]
    if len(prices) < 252:
        return None
    weights = dual_speed_weights(
        prices,
        growth_symbol=a,
        stable_symbol=b,
        fast_ma=40,
        slow_ma=150,
        fast_score=0.25,
        slow_score=0.75,
        target_vol=0.11,
    )
    row = fast_metrics(prices, weights)
    hold = buy_hold_metrics(prices, a, b)
    row.update(
        {
            "nasdaq": a,
            "nasdaq_name": NASDAQ.get(a, ""),
            "sp500": b,
            "sp500_name": SP500.get(b, ""),
            "pair": f"{a}_{b}",
            "latest_nasdaq_weight": float(weights[a].iloc[-1]),
            "latest_sp500_weight": float(weights[b].iloc[-1]),
            "latest_safe_weight": float(weights[SAFE_SYMBOL].iloc[-1]),
            **hold,
        }
    )
    row["cagr_vs_hold"] = row["cagr"] - row["hold_cagr"]
    row["vol_vs_hold"] = row["vol"] - row["hold_vol"]
    row["mdd_vs_hold"] = row["mdd"] - row["hold_mdd"]
    row["sharpe_vs_hold"] = row["sharpe"] - row["hold_sharpe"]
    return row


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nasdaq = available_symbols(NASDAQ)
    sp500 = available_symbols(SP500)

    inventory_rows = []
    for symbol, name in {**nasdaq, **sp500}.items():
        s = read_close(symbol)
        inventory_rows.append(
            {
                "symbol": symbol,
                "name": name,
                "start": s.index[0].date().isoformat(),
                "end": s.index[-1].date().isoformat(),
                "days": len(s),
            }
        )

    rows = []
    for a in nasdaq:
        for b in sp500:
            row = run_pair(a, b)
            if row is not None:
                rows.append(row)

    long_rows = []
    for a, b, start in [
        ("513100", "513500", "2015-01-01"),
        ("159941", "513500", "2015-07-13"),
        ("513300", "513500", "2020-11-05"),
    ]:
        row = run_pair(a, b, start=start)
        if row is not None:
            row["validation_start"] = start
            long_rows.append(row)

    inventory = pd.DataFrame(inventory_rows).sort_values(["symbol"])
    pair_summary = pd.DataFrame(rows).sort_values(["days", "sharpe"], ascending=[False, False])
    long_summary = pd.DataFrame(long_rows).sort_values("start")

    inventory.to_csv(OUT_DIR / "etf_inventory.csv", index=False)
    pair_summary.to_csv(OUT_DIR / "pair_validation_summary.csv", index=False)
    long_summary.to_csv(OUT_DIR / "long_validation_summary.csv", index=False)

    cols = [
        "pair",
        "start",
        "end",
        "days",
        "cagr",
        "hold_cagr",
        "cagr_vs_hold",
        "vol",
        "hold_vol",
        "mdd",
        "hold_mdd",
        "mdd_vs_hold",
        "sharpe",
        "hold_sharpe",
        "sharpe_vs_hold",
        "latest_nasdaq_weight",
        "latest_sp500_weight",
        "latest_safe_weight",
    ]
    print("LONG VALIDATION")
    print(long_summary[cols].to_string(index=False))
    print("\nPAIR VALIDATION TOP")
    print(pair_summary[cols].head(30).to_string(index=False))


if __name__ == "__main__":
    main()
