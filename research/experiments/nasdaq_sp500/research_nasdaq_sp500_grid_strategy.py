from __future__ import annotations

from itertools import product
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_nasdaq_sp500_dual_speed_grid import dual_speed_weights, fast_metrics  # noqa: E402
from research_nasdaq_sp500_more_strategies import (  # noqa: E402
    BASE_WEIGHTS,
    CASES,
    SAFE_SYMBOL,
    build_prices,
    is_rebalance_dates,
    metrics,
    segment_metrics,
    simulate,
    weekly_hold,
    with_safe,
    yearly_metrics,
)


OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_grid_strategy"


def cap_weights(weights: pd.DataFrame, caps: dict[str, float]) -> pd.DataFrame:
    out = weights.copy()
    for symbol, cap in caps.items():
        if symbol in out.columns:
            out[symbol] = out[symbol].clip(lower=0.0, upper=cap)
    return out


def ma_deviation_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    ma_window: int,
    grid_step: float,
    unit: float,
    neutral: float,
    min_fraction: float,
    max_fraction: float,
    trend_floor: float | None = None,
) -> pd.DataFrame:
    ma = prices[symbols].rolling(ma_window).mean()
    deviation = prices[symbols] / ma - 1.0
    fraction = neutral + (-deviation / grid_step) * unit
    fraction = fraction.replace([np.inf, -np.inf], np.nan).fillna(neutral).clip(min_fraction, max_fraction)
    if trend_floor is not None:
        trend_ok = (prices[symbols] > ma).astype(float)
        floor = trend_floor + (1.0 - trend_floor) * trend_ok
        fraction = fraction * floor
    base = pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=prices.index)
    return with_safe(cap_weights(base * fraction, {symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}))


def drawdown_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    grid_step: float,
    unit: float,
    neutral: float,
    min_fraction: float,
    max_fraction: float,
    trend_ma: int | None = None,
    trend_floor: float = 0.45,
) -> pd.DataFrame:
    high = prices[symbols].rolling(high_window).max()
    drawdown = prices[symbols] / high - 1.0
    levels = (-drawdown / grid_step).clip(lower=0.0)
    fraction = (neutral + levels * unit).replace([np.inf, -np.inf], np.nan).fillna(neutral)
    fraction = fraction.clip(min_fraction, max_fraction)
    if trend_ma is not None:
        ma = prices[symbols].rolling(trend_ma).mean()
        trend_ok = (prices[symbols] > ma).astype(float)
        fraction = fraction * (trend_floor + (1.0 - trend_floor) * trend_ok)
    base = pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=prices.index)
    return with_safe(cap_weights(base * fraction, {symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}))


def portfolio_rebalance_band(
    prices: pd.DataFrame,
    symbols: list[str],
    target_exposure: float,
    band: float,
) -> pd.DataFrame:
    target = pd.Series(
        {symbols[0]: BASE_WEIGHTS[0] * target_exposure, symbols[1]: BASE_WEIGHTS[1] * target_exposure},
    )
    target[SAFE_SYMBOL] = 1.0 - target.sum()
    weights = pd.DataFrame(index=prices.index, columns=[symbols[0], symbols[1], SAFE_SYMBOL], dtype=float)
    current = target.copy()
    weights.iloc[0] = current
    returns = prices[[symbols[0], symbols[1], SAFE_SYMBOL]].pct_change().fillna(0.0)
    rebalance = is_rebalance_dates(prices.index)
    for i, date in enumerate(prices.index[1:], start=1):
        gross = float((current * returns.loc[date, current.index]).sum())
        if gross > -1.0:
            current = current * (1.0 + returns.loc[date, current.index]) / (1.0 + gross)
        if bool(rebalance.loc[date]) and float((current - target).abs().sum()) >= band:
            current = target.copy()
        weights.iloc[i] = current
    return weights.ffill()


def dual_speed_with_drawdown_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    grid_step: float,
    unit: float,
    trend_ma: int,
) -> pd.DataFrame:
    base = dual_speed_weights(
        prices,
        growth_symbol=symbols[0],
        stable_symbol=symbols[1],
        fast_ma=40,
        slow_ma=150,
        fast_score=0.25,
        slow_score=0.75,
        target_vol=0.11,
    )
    high = prices[symbols].rolling(high_window).max()
    drawdown = (prices[symbols] / high - 1.0).fillna(0.0)
    trend_ok = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
    levels = (-drawdown / grid_step).clip(lower=0.0, upper=3.0)
    boost = levels * unit
    boost = boost.where(trend_ok, 0.0)
    out = base.copy()
    for symbol, cap in zip(symbols, BASE_WEIGHTS):
        out[symbol] = (out[symbol] + cap * boost[symbol]).clip(0.0, cap)
    risk_sum = out[symbols].sum(axis=1).clip(0.0, 1.0)
    out[SAFE_SYMBOL] = 1.0 - risk_sum
    return out


def bollinger_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    window: int,
    unit: float,
    neutral: float,
    min_fraction: float,
    max_fraction: float,
    trend_ma: int | None,
    trend_floor: float = 0.45,
) -> pd.DataFrame:
    mid = prices[symbols].rolling(window).mean()
    std = prices[symbols].rolling(window).std(ddof=0)
    z_score = ((prices[symbols] - mid) / std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    fraction = (neutral - z_score * unit).clip(min_fraction, max_fraction)
    if trend_ma is not None:
        trend_ok = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
        fraction = fraction * (trend_floor + (1.0 - trend_floor) * trend_ok.astype(float))
    base = pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=prices.index)
    return with_safe(cap_weights(base * fraction, {symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}))


def close_atr_drawdown_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    atr_window: int,
    atr_mult: float,
    unit: float,
    neutral: float,
    trend_ma: int | None,
    trend_floor: float = 0.45,
) -> pd.DataFrame:
    high = prices[symbols].rolling(high_window).max()
    drawdown = prices[symbols] / high - 1.0
    atr_pct = prices[symbols].pct_change().abs().rolling(atr_window).mean().clip(lower=0.01)
    levels = (-drawdown / (atr_pct * atr_mult)).clip(lower=0.0, upper=4.0)
    fraction = (neutral + levels * unit).replace([np.inf, -np.inf], np.nan).fillna(neutral).clip(0.25, 1.0)
    if trend_ma is not None:
        trend_ok = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
        fraction = fraction * (trend_floor + (1.0 - trend_floor) * trend_ok.astype(float))
    base = pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=prices.index)
    return with_safe(cap_weights(base * fraction, {symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}))


def rsi_grid(
    prices: pd.DataFrame,
    symbols: list[str],
    window: int,
    unit: float,
    neutral: float,
    trend_ma: int | None,
    trend_floor: float = 0.45,
) -> pd.DataFrame:
    diff = prices[symbols].diff()
    gain = diff.clip(lower=0.0).rolling(window).mean()
    loss = (-diff.clip(upper=0.0)).rolling(window).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = (100.0 - 100.0 / (1.0 + rs)).replace([np.inf, -np.inf], np.nan).fillna(50.0)
    fraction = (neutral + (50.0 - rsi) / 10.0 * unit).clip(0.25, 1.0)
    if trend_ma is not None:
        trend_ok = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
        fraction = fraction * (trend_floor + (1.0 - trend_floor) * trend_ok.astype(float))
    base = pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=prices.index)
    return with_safe(cap_weights(base * fraction, {symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}))


def strategy_grid(prices: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    strategies: dict[str, pd.DataFrame] = {}
    rebalance = is_rebalance_dates(prices.index)

    for ma_window, grid_step, unit, neutral, min_fraction in product(
        [100, 150, 200],
        [0.05, 0.08, 0.10],
        [0.08, 0.12, 0.16],
        [0.55, 0.65, 0.75],
        [0.25, 0.35, 0.45],
    ):
        max_fraction = 1.0
        if min_fraction >= neutral:
            continue
        name = (
            f"ma_grid_ma{ma_window}_step{int(grid_step*100):02d}"
            f"_unit{int(unit*100):02d}_n{int(neutral*100):02d}_min{int(min_fraction*100):02d}"
        )
        weights = ma_deviation_grid(
            prices,
            symbols,
            ma_window=ma_window,
            grid_step=grid_step,
            unit=unit,
            neutral=neutral,
            min_fraction=min_fraction,
            max_fraction=max_fraction,
        )
        strategies[name] = weekly_hold(weights, rebalance)

    for high_window, grid_step, unit, neutral, trend_ma in product(
        [120, 250],
        [0.05, 0.08, 0.10],
        [0.08, 0.12, 0.16],
        [0.45, 0.55, 0.65],
        [None, 150],
    ):
        name = (
            f"dd_grid_hi{high_window}_step{int(grid_step*100):02d}"
            f"_unit{int(unit*100):02d}_n{int(neutral*100):02d}"
            f"{'_tf150' if trend_ma else ''}"
        )
        weights = drawdown_grid(
            prices,
            symbols,
            high_window=high_window,
            grid_step=grid_step,
            unit=unit,
            neutral=neutral,
            min_fraction=0.25,
            max_fraction=1.0,
            trend_ma=trend_ma,
        )
        strategies[name] = weekly_hold(weights, rebalance)

    for target_exposure, band in product([0.55, 0.65, 0.75], [0.03, 0.05, 0.08, 0.10]):
        name = f"rebalance_band_exp{int(target_exposure*100):02d}_band{int(band*100):02d}"
        strategies[name] = portfolio_rebalance_band(prices, symbols, target_exposure=target_exposure, band=band)

    for high_window, grid_step, unit in product([120, 250], [0.05, 0.08, 0.10], [0.04, 0.08, 0.12]):
        name = (
            f"hybrid_dual_grid_hi{high_window}_step{int(grid_step*100):02d}"
            f"_unit{int(unit*100):02d}_tf150"
        )
        strategies[name] = dual_speed_with_drawdown_grid(
            prices,
            symbols,
            high_window=high_window,
            grid_step=grid_step,
            unit=unit,
            trend_ma=150,
        )

    for window, unit, neutral, trend_ma in product(
        [20, 40, 60],
        [0.08, 0.12, 0.16],
        [0.45, 0.55, 0.65],
        [None, 150],
    ):
        name = (
            f"boll_grid_w{window}_unit{int(unit*100):02d}_n{int(neutral*100):02d}"
            f"{'_tf150' if trend_ma else ''}"
        )
        strategies[name] = weekly_hold(
            bollinger_grid(
                prices,
                symbols,
                window=window,
                unit=unit,
                neutral=neutral,
                min_fraction=0.25,
                max_fraction=1.0,
                trend_ma=trend_ma,
            ),
            rebalance,
        )

    for high_window, atr_window, atr_mult, unit, neutral, trend_ma in product(
        [120, 250],
        [14, 20],
        [8.0, 12.0, 16.0],
        [0.04, 0.08, 0.12],
        [0.45, 0.55],
        [None, 150],
    ):
        name = (
            f"atr_grid_hi{high_window}_a{atr_window}_m{int(atr_mult):02d}"
            f"_u{int(unit*100):02d}_n{int(neutral*100):02d}"
            f"{'_tf150' if trend_ma else ''}"
        )
        strategies[name] = weekly_hold(
            close_atr_drawdown_grid(
                prices,
                symbols,
                high_window=high_window,
                atr_window=atr_window,
                atr_mult=atr_mult,
                unit=unit,
                neutral=neutral,
                trend_ma=trend_ma,
            ),
            rebalance,
        )

    for window, unit, neutral, trend_ma in product([10, 14, 20], [0.04, 0.08, 0.12], [0.45, 0.55, 0.65], [None, 150]):
        name = (
            f"rsi_grid_w{window}_unit{int(unit*100):02d}_n{int(neutral*100):02d}"
            f"{'_tf150' if trend_ma else ''}"
        )
        strategies[name] = weekly_hold(
            rsi_grid(
                prices,
                symbols,
                window=window,
                unit=unit,
                neutral=neutral,
                trend_ma=trend_ma,
            ),
            rebalance,
        )

    return strategies


def rank_robust(summary: pd.DataFrame) -> pd.DataFrame:
    pivot = summary.pivot_table(
        index="strategy",
        columns="pair",
        values=["cagr", "vol", "mdd", "sharpe", "avg_exposure"],
        aggfunc="first",
    )
    rows = []
    pairs = list(summary["pair"].drop_duplicates())
    for strategy in pivot.index:
        row = {"strategy": strategy}
        for metric in ["cagr", "vol", "mdd", "sharpe", "avg_exposure"]:
            for pair in pairs:
                row[f"{pair}_{metric}"] = pivot.loc[strategy, (metric, pair)]
        sharpes = [row[f"{pair}_sharpe"] for pair in pairs]
        mdds = [row[f"{pair}_mdd"] for pair in pairs]
        cagrs = [row[f"{pair}_cagr"] for pair in pairs]
        row["min_sharpe"] = float(np.nanmin(sharpes))
        row["avg_sharpe"] = float(np.nanmean(sharpes))
        row["worst_mdd"] = float(np.nanmin(mdds))
        row["avg_cagr"] = float(np.nanmean(cagrs))
        row["current_mdd_ok"] = bool(row["current_513300_513500_mdd"] >= -0.16)
        row["robust_score"] = (
            row["min_sharpe"] * 0.45
            + row["avg_sharpe"] * 0.35
            + row["avg_cagr"] * 1.0
            + max(min(row["worst_mdd"], 0.0), -0.55) * 0.20
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["current_mdd_ok", "robust_score"], ascending=[False, False])


def selected_strategy_names(robust: pd.DataFrame) -> list[str]:
    names: list[str] = []
    for prefix in ["hybrid_dual_grid", "boll_grid", "atr_grid", "rsi_grid", "ma_grid", "dd_grid", "rebalance_band"]:
        part = robust[robust["strategy"].str.startswith(prefix)]
        if not part.empty:
            names.append(str(part.iloc[0]["strategy"]))
    return names


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    target_rows = []
    strategies_by_case: dict[str, dict[str, pd.DataFrame]] = {}
    prices_by_case: dict[str, pd.DataFrame] = {}

    for case in CASES:
        prices = build_prices(case)
        symbols = [case.growth_symbol, case.stable_symbol]
        strategies = strategy_grid(prices, symbols)
        strategies_by_case[case.name] = strategies
        prices_by_case[case.name] = prices
        for name, weights in strategies.items():
            row = fast_metrics(prices, weights)
            row.update({"pair": case.name, "strategy": name})
            summary_rows.append(row)
        last_date = prices.index[-1].date().isoformat()
        for name, weights in strategies.items():
            target = {"pair": case.name, "strategy": name, "date": last_date}
            for symbol, value in weights.iloc[-1].items():
                target[symbol] = float(value)
            target_rows.append(target)

    summary = pd.DataFrame(summary_rows)
    robust = rank_robust(summary)
    targets = pd.DataFrame(target_rows)
    selected = selected_strategy_names(robust)

    selected_summary = summary[summary["strategy"].isin(selected)].copy()
    segment_rows = []
    yearly_frames = []
    for case in CASES:
        prices = prices_by_case[case.name]
        for name in selected:
            weights = strategies_by_case[case.name][name]
            curve = simulate(prices, weights, name=name, pair=case.name)
            segment_rows.extend(segment_metrics(curve))
            yearly_frames.append(yearly_metrics(curve))

    summary.to_csv(OUT_DIR / "grid_strategy_summary.csv", index=False)
    robust.to_csv(OUT_DIR / "grid_strategy_robust_rank.csv", index=False)
    targets.to_csv(OUT_DIR / "grid_strategy_latest_targets.csv", index=False)
    selected_summary.to_csv(OUT_DIR / "grid_strategy_selected_summary.csv", index=False)
    pd.DataFrame(segment_rows).to_csv(OUT_DIR / "grid_strategy_selected_segments.csv", index=False)
    pd.concat(yearly_frames, ignore_index=True).to_csv(OUT_DIR / "grid_strategy_selected_yearly.csv", index=False)

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


if __name__ == "__main__":
    main()
