from __future__ import annotations

from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_nasdaq_sp500_more_strategies import (  # noqa: E402
    BASE_WEIGHTS,
    CASES,
    SAFE_SYMBOL,
    build_prices,
    is_rebalance_dates,
    segment_metrics,
    yearly_metrics,
    weekly_hold,
    with_safe,
    portfolio_vol_scale,
    simulate,
)


GRID_OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_dual_speed_grid"
COMMISSION = 0.0005
TRADING_DAYS = 252


def dual_speed_weights(
    prices: pd.DataFrame,
    growth_symbol: str,
    stable_symbol: str,
    fast_ma: int,
    slow_ma: int,
    fast_score: float,
    slow_score: float,
    target_vol: float,
) -> pd.DataFrame:
    symbols = [growth_symbol, stable_symbol]
    returns = prices.pct_change().fillna(0.0)
    base = pd.DataFrame(
        {growth_symbol: BASE_WEIGHTS[0], stable_symbol: BASE_WEIGHTS[1]},
        index=prices.index,
    )
    fast = (prices[symbols] > prices[symbols].rolling(fast_ma).mean()).astype(float)
    slow = (prices[symbols] > prices[symbols].rolling(slow_ma).mean()).astype(float)
    state = (fast * fast_score + slow * slow_score).clip(0.0, 1.0)
    weights = with_safe(portfolio_vol_scale(returns, base * state, target_vol))
    return weekly_hold(weights, is_rebalance_dates(prices.index))


def make_param_name(fast_ma: int, slow_ma: int, fast_score: float, slow_score: float, target_vol: float) -> str:
    return (
        f"dual_speed_ma{fast_ma}_{slow_ma}"
        f"_f{int(round(fast_score * 100)):02d}"
        f"_s{int(round(slow_score * 100)):02d}"
        f"_vt{int(round(target_vol * 100)):02d}_safe"
    )


def grid_params() -> list[tuple[int, int, float, float, float]]:
    fast_mas = [40, 50, 60]
    slow_mas = [120, 150, 180]
    score_pairs = [(0.25, 0.75), (0.35, 0.65), (0.45, 0.55)]
    target_vols = [0.11, 0.12, 0.13, 0.14]
    return [
        (fast_ma, slow_ma, fast_score, slow_score, target_vol)
        for fast_ma, slow_ma, (fast_score, slow_score), target_vol in product(
            fast_mas,
            slow_mas,
            score_pairs,
            target_vols,
        )
        if fast_ma < slow_ma
    ]


def rank_robust(summary: pd.DataFrame) -> pd.DataFrame:
    pivot = summary.pivot_table(
        index="strategy",
        columns="pair",
        values=["cagr", "vol", "mdd", "sharpe", "avg_exposure"],
        aggfunc="first",
    )
    rows = []
    for strategy in pivot.index:
        row = {"strategy": strategy}
        for metric in ["cagr", "vol", "mdd", "sharpe", "avg_exposure"]:
            for pair in summary["pair"].unique():
                row[f"{pair}_{metric}"] = pivot.loc[strategy, (metric, pair)]
        sharpe_values = [row[f"{pair}_sharpe"] for pair in summary["pair"].unique()]
        mdd_values = [row[f"{pair}_mdd"] for pair in summary["pair"].unique()]
        cagr_values = [row[f"{pair}_cagr"] for pair in summary["pair"].unique()]
        row["min_sharpe"] = float(np.nanmin(sharpe_values))
        row["avg_sharpe"] = float(np.nanmean(sharpe_values))
        row["worst_mdd"] = float(np.nanmin(mdd_values))
        row["avg_cagr"] = float(np.nanmean(cagr_values))
        current_mdd = row.get("current_513300_513500_mdd", np.nan)
        row["current_mdd_ok"] = bool(pd.notna(current_mdd) and current_mdd >= -0.15)
        rows.append(row)
    ranked = pd.DataFrame(rows)
    ranked["robust_score"] = (
        ranked["min_sharpe"] * 0.45
        + ranked["avg_sharpe"] * 0.35
        + ranked["avg_cagr"] * 1.00
        + ranked["worst_mdd"].clip(lower=-0.50) * 0.20
    )
    return ranked.sort_values(["current_mdd_ok", "robust_score"], ascending=[False, False])


def fast_metrics(prices: pd.DataFrame, weights: pd.DataFrame) -> dict:
    symbols = list(weights.columns)
    returns = prices[symbols].pct_change().fillna(0.0).to_numpy(dtype=float)
    weight_arr = weights.reindex(prices.index).fillna(0.0).clip(lower=0.0).to_numpy(dtype=float).copy()
    row_sum = weight_arr.sum(axis=1)
    too_high = row_sum > 1.0
    weight_arr[too_high] = weight_arr[too_high] / row_sum[too_high, None]
    rebalance = is_rebalance_dates(prices.index).reindex(prices.index).to_numpy(dtype=bool)

    prev = np.zeros(weight_arr.shape[1], dtype=float)
    rets = np.zeros(len(prices), dtype=float)
    turnovers = np.zeros(len(prices), dtype=float)
    risk_idx = [i for i, symbol in enumerate(symbols) if symbol != SAFE_SYMBOL]
    risk_exposure = weight_arr[:, risk_idx].sum(axis=1) if risk_idx else np.zeros(len(prices))
    safe_weight = weight_arr[:, symbols.index(SAFE_SYMBOL)] if SAFE_SYMBOL in symbols else np.zeros(len(prices))

    for i in range(len(prices)):
        gross = float(prev @ returns[i])
        if 1.0 + gross > 0:
            drifted = prev * (1.0 + returns[i]) / (1.0 + gross)
        else:
            drifted = prev
        if rebalance[i]:
            turnover = float(np.abs(weight_arr[i] - drifted).sum())
            cost = turnover * COMMISSION
            prev = weight_arr[i].copy()
        else:
            turnover = 0.0
            cost = 0.0
            prev = drifted
        rets[i] = (1.0 + gross) * (1.0 - cost) - 1.0
        turnovers[i] = turnover

    equity = np.cumprod(1.0 + rets)
    years = len(rets) / TRADING_DAYS
    cagr = equity[-1] ** (1.0 / years) - 1.0 if years > 0 and equity[-1] > 0 else np.nan
    vol = float(np.std(rets, ddof=0) * np.sqrt(TRADING_DAYS))
    drawdown = equity / np.maximum.accumulate(equity) - 1.0
    mdd = float(np.min(drawdown))
    monthly = pd.Series(rets, index=prices.index).groupby(prices.index.to_period("M")).apply(lambda x: (1 + x).prod() - 1)
    return {
        "start": prices.index[0].date().isoformat(),
        "end": prices.index[-1].date().isoformat(),
        "days": len(rets),
        "cumret": float(equity[-1] - 1.0),
        "cagr": float(cagr),
        "vol": vol,
        "mdd": mdd,
        "sharpe": float(cagr / vol) if vol > 0 else np.nan,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else np.nan,
        "month_win": float((monthly > 0).mean()),
        "avg_exposure": float(np.mean(risk_exposure)),
        "avg_safe": float(np.mean(safe_weight)),
        "turnover": float(np.mean(turnovers)),
    }


def main() -> None:
    GRID_OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    target_rows = []

    for case in CASES:
        prices = build_prices(case)
        target_weights = {}
        for fast_ma, slow_ma, fast_score, slow_score, target_vol in grid_params():
            name = make_param_name(fast_ma, slow_ma, fast_score, slow_score, target_vol)
            weights = dual_speed_weights(
                prices,
                growth_symbol=case.growth_symbol,
                stable_symbol=case.stable_symbol,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                fast_score=fast_score,
                slow_score=slow_score,
                target_vol=target_vol,
            )
            target_weights[name] = weights
            row = fast_metrics(prices, weights)
            row.update(
                {
                    "pair": case.name,
                    "strategy": name,
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                    "fast_score": fast_score,
                    "slow_score": slow_score,
                    "target_vol": target_vol,
                }
            )
            summary_rows.append(row)
        last_date = prices.index[-1].date().isoformat()
        for name, weights in target_weights.items():
            target_row = {"pair": case.name, "strategy": name, "date": last_date}
            for symbol, value in weights.iloc[-1].items():
                target_row[symbol] = float(value)
            target_rows.append(target_row)

    summary = pd.DataFrame(summary_rows)
    robust = rank_robust(summary)
    targets = pd.DataFrame(target_rows)

    summary.to_csv(GRID_OUT_DIR / "dual_speed_grid_summary.csv", index=False)
    robust.to_csv(GRID_OUT_DIR / "dual_speed_grid_robust_rank.csv", index=False)
    targets.to_csv(GRID_OUT_DIR / "dual_speed_grid_latest_targets.csv", index=False)
    write_selected_details()

    cols = [
        "strategy",
        "current_513300_513500_cagr",
        "current_513300_513500_mdd",
        "current_513300_513500_sharpe",
        "long_proxy_513100_513500_cagr",
        "long_proxy_513100_513500_mdd",
        "long_proxy_513100_513500_sharpe",
        "mid_proxy_159941_513500_cagr",
        "mid_proxy_159941_513500_mdd",
        "mid_proxy_159941_513500_sharpe",
        "min_sharpe",
        "worst_mdd",
        "robust_score",
    ]
    print(robust[cols].head(30).to_string(index=False))


def selected_candidates() -> dict[str, tuple[int, int, float, float, float]]:
    return {
        "strict_ma40_120_f35_s65_vt11": (40, 120, 0.35, 0.65, 0.11),
        "strict_ma40_120_f25_s75_vt11": (40, 120, 0.25, 0.75, 0.11),
        "balanced_ma40_150_f35_s65_vt11": (40, 150, 0.35, 0.65, 0.11),
        "balanced_ma40_150_f25_s75_vt11": (40, 150, 0.25, 0.75, 0.11),
        "growth_ma40_150_f25_s75_vt13": (40, 150, 0.25, 0.75, 0.13),
    }


def write_selected_details() -> None:
    rows = []
    segments = []
    yearly = []
    targets = []
    for case in CASES:
        prices = build_prices(case)
        for label, params in selected_candidates().items():
            fast_ma, slow_ma, fast_score, slow_score, target_vol = params
            strategy = make_param_name(fast_ma, slow_ma, fast_score, slow_score, target_vol)
            weights = dual_speed_weights(
                prices,
                growth_symbol=case.growth_symbol,
                stable_symbol=case.stable_symbol,
                fast_ma=fast_ma,
                slow_ma=slow_ma,
                fast_score=fast_score,
                slow_score=slow_score,
                target_vol=target_vol,
            )
            curve = simulate(prices, weights, name=strategy, pair=case.name)
            row = fast_metrics(prices, weights)
            row.update({"pair": case.name, "strategy": strategy, "candidate": label})
            rows.append(row)
            for segment in segment_metrics(curve):
                segment["candidate"] = label
                segments.append(segment)
            y = yearly_metrics(curve)
            y["candidate"] = label
            yearly.append(y)
            target = {"pair": case.name, "strategy": strategy, "candidate": label, "date": prices.index[-1].date().isoformat()}
            for symbol, value in weights.iloc[-1].items():
                target[symbol] = float(value)
            targets.append(target)

    pd.DataFrame(rows).to_csv(GRID_OUT_DIR / "dual_speed_selected_summary.csv", index=False)
    pd.DataFrame(segments).to_csv(GRID_OUT_DIR / "dual_speed_selected_segments.csv", index=False)
    pd.concat(yearly, ignore_index=True).to_csv(GRID_OUT_DIR / "dual_speed_selected_yearly.csv", index=False)
    pd.DataFrame(targets).to_csv(GRID_OUT_DIR / "dual_speed_selected_latest_targets.csv", index=False)


if __name__ == "__main__":
    main()
