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
    portfolio_vol_scale,
    segment_metrics,
    simulate,
    weekly_hold,
    with_safe,
    yearly_metrics,
)


OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_other_strategies"


def base_frame(index: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=index)


def turn_of_month_weights(prices: pd.DataFrame, symbols: list[str], pre_days: int, post_days: int, trend_ma: int | None) -> pd.DataFrame:
    dates = pd.Series(prices.index, index=prices.index)
    month = dates.dt.to_period("M")
    pos_in_month = dates.groupby(month).cumcount()
    count_in_month = dates.groupby(month).transform("count")
    active = (pos_in_month >= count_in_month - pre_days) | (pos_in_month < post_days)
    fraction = pd.Series(np.where(active, 1.0, 0.25), index=prices.index)
    if trend_ma is not None:
        trend_ok = (prices[symbols] > prices[symbols].rolling(trend_ma).mean()).all(axis=1)
        fraction = fraction * np.where(trend_ok, 1.0, 0.45)
    return with_safe(base_frame(prices.index, symbols).mul(fraction, axis=0))


def halloween_weights(prices: pd.DataFrame, symbols: list[str], weak_fraction: float, trend_ma: int | None) -> pd.DataFrame:
    month = pd.Series(prices.index.month, index=prices.index)
    strong = month.isin([11, 12, 1, 2, 3, 4])
    fraction = pd.Series(np.where(strong, 1.0, weak_fraction), index=prices.index)
    if trend_ma is not None:
        trend_ok = (prices[symbols] > prices[symbols].rolling(trend_ma).mean()).all(axis=1)
        fraction = fraction * np.where(trend_ok, 1.0, 0.55)
    return with_safe(base_frame(prices.index, symbols).mul(fraction, axis=0))


def ratio_mean_reversion_weights(
    prices: pd.DataFrame,
    symbols: list[str],
    z_window: int,
    tilt: float,
    target_vol: float,
    trend_ma: int,
) -> pd.DataFrame:
    a, b = symbols
    ratio = prices[a] / prices[b]
    z = ((ratio - ratio.rolling(z_window).mean()) / ratio.rolling(z_window).std(ddof=0)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    # Ratio high means Nasdaq has outrun S&P, so mean reversion tilts away from Nasdaq.
    wa = (BASE_WEIGHTS[0] - z * tilt).clip(0.45, 0.72)
    wb = 1.0 - wa
    raw = pd.DataFrame({a: wa, b: wb}, index=prices.index)
    trend = (prices[symbols] > prices[symbols].rolling(trend_ma).mean()).astype(float)
    raw = raw * trend
    returns = prices.pct_change().fillna(0.0)
    return with_safe(portfolio_vol_scale(returns, raw, target_vol))


def volatility_regime_weights(
    prices: pd.DataFrame,
    symbols: list[str],
    vol_window: int,
    rank_window: int,
    low_fraction: float,
    mid_fraction: float,
    high_fraction: float,
    trend_ma: int,
) -> pd.DataFrame:
    returns = prices[symbols].pct_change().fillna(0.0)
    base_ret = returns[symbols[0]] * BASE_WEIGHTS[0] + returns[symbols[1]] * BASE_WEIGHTS[1]
    vol = base_ret.rolling(vol_window).std(ddof=0) * np.sqrt(252)
    rank = vol.rolling(rank_window).rank(pct=True).fillna(0.5)
    fraction = pd.Series(mid_fraction, index=prices.index)
    fraction.loc[rank <= 0.33] = low_fraction
    fraction.loc[rank >= 0.67] = high_fraction
    trend_ok = (prices[symbols] > prices[symbols].rolling(trend_ma).mean()).all(axis=1)
    fraction = fraction * np.where(trend_ok, 1.0, 0.50)
    return with_safe(base_frame(prices.index, symbols).mul(fraction, axis=0))


def new_high_trend_weights(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    low_window: int,
    trend_ma: int,
    strong_fraction: float,
    normal_fraction: float,
    weak_fraction: float,
) -> pd.DataFrame:
    near_high = prices[symbols] >= prices[symbols].rolling(high_window).max().shift(1) * 0.98
    breakdown = prices[symbols] <= prices[symbols].rolling(low_window).min().shift(1) * 1.02
    trend_ok = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
    fraction = pd.DataFrame(normal_fraction, index=prices.index, columns=symbols)
    fraction = fraction.mask(near_high & trend_ok, strong_fraction)
    fraction = fraction.mask(breakdown | ~trend_ok, weak_fraction)
    return with_safe(base_frame(prices.index, symbols) * fraction)


def dual_speed_calendar_filter(prices: pd.DataFrame, symbols: list[str], weak_fraction: float) -> pd.DataFrame:
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
    month = pd.Series(prices.index.month, index=prices.index)
    strong = month.isin([11, 12, 1, 2, 3, 4])
    out = base.copy()
    risk_cols = [s for s in symbols if s in out.columns]
    out.loc[~strong, risk_cols] = out.loc[~strong, risk_cols] * weak_fraction
    out[SAFE_SYMBOL] = 1.0 - out[risk_cols].sum(axis=1).clip(0.0, 1.0)
    return out


def make_strategies(prices: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    rebalance = is_rebalance_dates(prices.index)
    strategies: dict[str, pd.DataFrame] = {}

    for pre_days, post_days, trend_ma in product([2, 4, 6], [2, 4], [None, 150]):
        name = f"turn_month_pre{pre_days}_post{post_days}{'_tf150' if trend_ma else ''}"
        strategies[name] = weekly_hold(turn_of_month_weights(prices, symbols, pre_days, post_days, trend_ma), rebalance)

    for weak_fraction, trend_ma in product([0.25, 0.40, 0.55, 0.70], [None, 150]):
        name = f"halloween_weak{int(weak_fraction*100):02d}{'_tf150' if trend_ma else ''}"
        strategies[name] = weekly_hold(halloween_weights(prices, symbols, weak_fraction, trend_ma), rebalance)

    for z_window, tilt, target_vol in product([60, 120, 180], [0.04, 0.06, 0.08], [0.11, 0.13]):
        name = f"ratio_mr_z{z_window}_tilt{int(tilt*100):02d}_vt{int(target_vol*100):02d}"
        strategies[name] = weekly_hold(
            ratio_mean_reversion_weights(prices, symbols, z_window, tilt, target_vol, trend_ma=150),
            rebalance,
        )

    for vol_window, low_fraction, mid_fraction, high_fraction in product(
        [20, 40],
        [0.75, 0.90],
        [0.55, 0.70],
        [0.25, 0.40],
    ):
        name = (
            f"vol_regime_v{vol_window}_lo{int(low_fraction*100):02d}"
            f"_mid{int(mid_fraction*100):02d}_hi{int(high_fraction*100):02d}"
        )
        strategies[name] = weekly_hold(
            volatility_regime_weights(
                prices,
                symbols,
                vol_window=vol_window,
                rank_window=252,
                low_fraction=low_fraction,
                mid_fraction=mid_fraction,
                high_fraction=high_fraction,
                trend_ma=150,
            ),
            rebalance,
        )

    for high_window, low_window, strong_fraction, normal_fraction in product(
        [60, 120],
        [40, 80],
        [0.90, 1.00],
        [0.55, 0.70],
    ):
        name = (
            f"new_high_h{high_window}_l{low_window}"
            f"_s{int(strong_fraction*100):02d}_n{int(normal_fraction*100):02d}"
        )
        strategies[name] = weekly_hold(
            new_high_trend_weights(
                prices,
                symbols,
                high_window=high_window,
                low_window=low_window,
                trend_ma=150,
                strong_fraction=strong_fraction,
                normal_fraction=normal_fraction,
                weak_fraction=0.25,
            ),
            rebalance,
        )

    for weak_fraction in [0.50, 0.65, 0.80]:
        name = f"dual_speed_halloween_weak{int(weak_fraction*100):02d}"
        strategies[name] = dual_speed_calendar_filter(prices, symbols, weak_fraction)

    return strategies


def rank_robust(summary: pd.DataFrame) -> pd.DataFrame:
    pivot = summary.pivot_table(
        index="strategy",
        columns="pair",
        values=["cagr", "vol", "mdd", "sharpe", "avg_exposure"],
        aggfunc="first",
    )
    pairs = list(summary["pair"].drop_duplicates())
    rows = []
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
            + row["avg_cagr"]
            + max(min(row["worst_mdd"], 0.0), -0.55) * 0.20
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["current_mdd_ok", "robust_score"], ascending=[False, False])


def selected_strategy_names(robust: pd.DataFrame) -> list[str]:
    names = []
    for prefix in ["ratio_mr", "vol_regime", "new_high", "turn_month", "halloween", "dual_speed_halloween"]:
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
        strategies = make_strategies(prices, symbols)
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

    summary.to_csv(OUT_DIR / "other_strategy_summary.csv", index=False)
    robust.to_csv(OUT_DIR / "other_strategy_robust_rank.csv", index=False)
    targets.to_csv(OUT_DIR / "other_strategy_latest_targets.csv", index=False)
    selected_summary.to_csv(OUT_DIR / "other_strategy_selected_summary.csv", index=False)
    pd.DataFrame(segment_rows).to_csv(OUT_DIR / "other_strategy_selected_segments.csv", index=False)
    pd.concat(yearly_frames, ignore_index=True).to_csv(OUT_DIR / "other_strategy_selected_yearly.csv", index=False)

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
