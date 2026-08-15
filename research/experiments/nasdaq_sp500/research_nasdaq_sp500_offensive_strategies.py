from __future__ import annotations

from itertools import product
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_nasdaq_sp500_dual_speed_grid import fast_metrics  # noqa: E402
from research_nasdaq_sp500_final_validation import (  # noqa: E402
    NASDAQ,
    SP500,
    available_symbols,
    build_prices as build_pair_prices,
    buy_hold_metrics,
)
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


OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_offensive_strategies"


def base_frame(index: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    return pd.DataFrame({symbols[0]: BASE_WEIGHTS[0], symbols[1]: BASE_WEIGHTS[1]}, index=index)


def fixed_fraction_weights(prices: pd.DataFrame, symbols: list[str], fraction: float) -> pd.DataFrame:
    return with_safe(base_frame(prices.index, symbols) * fraction)


def portfolio_ma_filter(
    prices: pd.DataFrame,
    symbols: list[str],
    ma_window: int,
    weak_fraction: float,
    confirm_days: int,
) -> pd.DataFrame:
    portfolio = prices[symbols[0]] / prices[symbols[0]].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[symbols[1]] / prices[symbols[1]].iloc[0] * BASE_WEIGHTS[1]
    trend = portfolio > portfolio.rolling(ma_window).mean()
    if confirm_days > 1:
        trend = trend.rolling(confirm_days).sum() == confirm_days
    fraction = pd.Series(np.where(trend.fillna(False), 1.0, weak_fraction), index=prices.index)
    return with_safe(base_frame(prices.index, symbols).mul(fraction, axis=0))


def asset_ma_filter(
    prices: pd.DataFrame,
    symbols: list[str],
    ma_window: int,
    weak_fraction: float,
    confirm_days: int,
) -> pd.DataFrame:
    trend = prices[symbols] > prices[symbols].rolling(ma_window).mean()
    if confirm_days > 1:
        trend = trend.rolling(confirm_days).sum() == confirm_days
    fraction = trend.fillna(False).astype(float) + (~trend.fillna(False)).astype(float) * weak_fraction
    return with_safe(base_frame(prices.index, symbols) * fraction)


def drawdown_filter(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    cut: float,
    recover: float,
    weak_fraction: float,
) -> pd.DataFrame:
    portfolio = prices[symbols[0]] / prices[symbols[0]].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[symbols[1]] / prices[symbols[1]].iloc[0] * BASE_WEIGHTS[1]
    drawdown = portfolio / portfolio.rolling(high_window).max() - 1.0

    risk_on = True
    fraction = []
    for value in drawdown.fillna(0.0):
        if risk_on and value <= -cut:
            risk_on = False
        elif not risk_on and value >= -recover:
            risk_on = True
        fraction.append(1.0 if risk_on else weak_fraction)
    fraction_s = pd.Series(fraction, index=prices.index)
    return with_safe(base_frame(prices.index, symbols).mul(fraction_s, axis=0))


def drawdown_ma_filter(
    prices: pd.DataFrame,
    symbols: list[str],
    ma_window: int,
    high_window: int,
    cut: float,
    recover: float,
    weak_fraction: float,
) -> pd.DataFrame:
    portfolio = prices[symbols[0]] / prices[symbols[0]].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[symbols[1]] / prices[symbols[1]].iloc[0] * BASE_WEIGHTS[1]
    drawdown = portfolio / portfolio.rolling(high_window).max() - 1.0
    ma = portfolio.rolling(ma_window).mean()

    risk_on = True
    fraction = []
    for date, value in drawdown.fillna(0.0).items():
        below_ma = bool(portfolio.loc[date] < ma.loc[date]) if pd.notna(ma.loc[date]) else False
        above_ma = bool(portfolio.loc[date] > ma.loc[date]) if pd.notna(ma.loc[date]) else False
        if risk_on and value <= -cut and below_ma:
            risk_on = False
        elif not risk_on and (value >= -recover or above_ma):
            risk_on = True
        fraction.append(1.0 if risk_on else weak_fraction)
    fraction_s = pd.Series(fraction, index=prices.index)
    return with_safe(base_frame(prices.index, symbols).mul(fraction_s, axis=0))


def ma_band_filter(
    prices: pd.DataFrame,
    symbols: list[str],
    ma_window: int,
    band: float,
    weak_fraction: float,
) -> pd.DataFrame:
    portfolio = prices[symbols[0]] / prices[symbols[0]].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[symbols[1]] / prices[symbols[1]].iloc[0] * BASE_WEIGHTS[1]
    ma = portfolio.rolling(ma_window).mean()
    risk_on = True
    fraction = []
    for date, value in portfolio.items():
        ma_value = ma.loc[date]
        if pd.notna(ma_value):
            if risk_on and value < ma_value * (1.0 - band):
                risk_on = False
            elif not risk_on and value > ma_value * (1.0 + band):
                risk_on = True
        fraction.append(1.0 if risk_on else weak_fraction)
    fraction_s = pd.Series(fraction, index=prices.index)
    return with_safe(base_frame(prices.index, symbols).mul(fraction_s, axis=0))


def volatility_brake(
    prices: pd.DataFrame,
    symbols: list[str],
    vol_window: int,
    vol_quantile: float,
    ma_window: int,
    weak_fraction: float,
) -> pd.DataFrame:
    returns = prices[symbols].pct_change().fillna(0.0)
    portfolio_ret = returns[symbols[0]] * BASE_WEIGHTS[0] + returns[symbols[1]] * BASE_WEIGHTS[1]
    vol = portfolio_ret.rolling(vol_window).std(ddof=0) * np.sqrt(252)
    threshold = vol.rolling(252).quantile(vol_quantile)
    portfolio = prices[symbols[0]] / prices[symbols[0]].iloc[0] * BASE_WEIGHTS[0]
    portfolio = portfolio + prices[symbols[1]] / prices[symbols[1]].iloc[0] * BASE_WEIGHTS[1]
    below_ma = portfolio < portfolio.rolling(ma_window).mean()
    brake = (vol > threshold) & below_ma
    fraction = pd.Series(np.where(brake.fillna(False), weak_fraction, 1.0), index=prices.index)
    return with_safe(base_frame(prices.index, symbols).mul(fraction, axis=0))


def momentum_acceleration(
    prices: pd.DataFrame,
    symbols: list[str],
    lookback: int,
    weak_fraction: float,
    strong_fraction: float,
) -> pd.DataFrame:
    momentum = prices[symbols] / prices[symbols].shift(lookback) - 1.0
    fraction = pd.DataFrame(1.0, index=prices.index, columns=symbols)
    fraction = fraction.mask(momentum <= 0.0, weak_fraction)
    fraction = fraction.mask(momentum > momentum.rolling(252).quantile(0.70), strong_fraction)
    return with_safe((base_frame(prices.index, symbols) * fraction).clip(upper=base_frame(prices.index, symbols)))


def new_high_participation(
    prices: pd.DataFrame,
    symbols: list[str],
    high_window: int,
    trend_ma: int,
    normal_fraction: float,
    weak_fraction: float,
) -> pd.DataFrame:
    near_high = prices[symbols] >= prices[symbols].rolling(high_window).max().shift(1) * 0.98
    trend = prices[symbols] > prices[symbols].rolling(trend_ma).mean()
    fraction = pd.DataFrame(normal_fraction, index=prices.index, columns=symbols)
    fraction = fraction.mask(near_high & trend, 1.0)
    fraction = fraction.mask(~trend, weak_fraction)
    return with_safe(base_frame(prices.index, symbols) * fraction)


def make_strategies(prices: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    rebalance = is_rebalance_dates(prices.index)
    strategies: dict[str, pd.DataFrame] = {}

    for fraction in [0.95, 1.00]:
        strategies[f"fixed_fraction_{int(fraction * 100):03d}"] = weekly_hold(
            fixed_fraction_weights(prices, symbols, fraction),
            rebalance,
        )

    for ma_window, weak_fraction, confirm_days in product([80, 120, 200], [0.50, 0.65], [1, 5]):
        strategies[f"portfolio_ma{ma_window}_weak{int(weak_fraction * 100):02d}_c{confirm_days}"] = weekly_hold(
            portfolio_ma_filter(prices, symbols, ma_window, weak_fraction, confirm_days),
            rebalance,
        )

    for ma_window, weak_fraction, confirm_days in product([200], [0.50], [3]):
        strategies[f"asset_ma{ma_window}_weak{int(weak_fraction * 100):02d}_c{confirm_days}"] = weekly_hold(
            asset_ma_filter(prices, symbols, ma_window, weak_fraction, confirm_days),
            rebalance,
        )

    for high_window, cut, recover, weak_fraction in product(
        [120, 180],
        [0.10, 0.15],
        [0.05, 0.08],
        [0.50, 0.65],
    ):
        if recover >= cut:
            continue
        strategies[
            f"dd_filter_h{high_window}_cut{int(cut * 100):02d}"
            f"_rec{int(recover * 100):02d}_weak{int(weak_fraction * 100):02d}"
        ] = weekly_hold(
            drawdown_filter(prices, symbols, high_window, cut, recover, weak_fraction),
            rebalance,
        )

    for ma_window, high_window, cut, recover, weak_fraction in product(
        [80, 200],
        [120, 180],
        [0.10, 0.15],
        [0.05],
        [0.50, 0.65],
    ):
        if recover >= cut:
            continue
        strategies[
            f"dd_ma_ma{ma_window}_h{high_window}_cut{int(cut * 100):02d}"
            f"_rec{int(recover * 100):02d}_weak{int(weak_fraction * 100):02d}"
        ] = weekly_hold(
            drawdown_ma_filter(prices, symbols, ma_window, high_window, cut, recover, weak_fraction),
            rebalance,
        )

    for ma_window, band, weak_fraction in product([80, 120, 200], [0.00, 0.02], [0.50, 0.65]):
        strategies[f"ma_band_ma{ma_window}_b{int(band * 100):02d}_weak{int(weak_fraction * 100):02d}"] = weekly_hold(
            ma_band_filter(prices, symbols, ma_window, band, weak_fraction),
            rebalance,
        )

    for vol_window, vol_quantile, ma_window, weak_fraction in product(
        [20],
        [0.80, 0.90],
        [80, 200],
        [0.50, 0.65],
    ):
        strategies[
            f"vol_brake_v{vol_window}_q{int(vol_quantile * 100):02d}"
            f"_ma{ma_window}_weak{int(weak_fraction * 100):02d}"
        ] = weekly_hold(
            volatility_brake(prices, symbols, vol_window, vol_quantile, ma_window, weak_fraction),
            rebalance,
        )

    for lookback, weak_fraction in product([120], [0.65]):
        strategies[f"momentum_accel_l{lookback}_weak{int(weak_fraction * 100):02d}"] = weekly_hold(
            momentum_acceleration(prices, symbols, lookback, weak_fraction, strong_fraction=1.0),
            rebalance,
        )

    for high_window, trend_ma, normal_fraction, weak_fraction in product(
        [60],
        [150],
        [0.90],
        [0.65],
    ):
        strategies[
            f"new_high_h{high_window}_ma{trend_ma}"
            f"_n{int(normal_fraction * 100):02d}_weak{int(weak_fraction * 100):02d}"
        ] = weekly_hold(
            new_high_participation(prices, symbols, high_window, trend_ma, normal_fraction, weak_fraction),
            rebalance,
        )

    return strategies


def summarize_case(case_name: str, prices: pd.DataFrame, symbols: list[str], strategies: dict[str, pd.DataFrame]) -> list[dict]:
    hold = buy_hold_metrics(prices, symbols[0], symbols[1])
    rows = []
    for name, weights in strategies.items():
        row = fast_metrics(prices, weights)
        row.update(
            {
                "pair": case_name,
                "strategy": name,
                **hold,
            }
        )
        row["cagr_vs_hold"] = row["cagr"] - row["hold_cagr"]
        row["vol_vs_hold"] = row["vol"] - row["hold_vol"]
        row["mdd_vs_hold"] = row["mdd"] - row["hold_mdd"]
        row["sharpe_vs_hold"] = row["sharpe"] - row["hold_sharpe"]
        row["beats_hold_cagr"] = row["cagr_vs_hold"] > 0
        row["beats_hold_sharpe"] = row["sharpe_vs_hold"] > 0
        return_guard = 0.85 if case_name == "current_513300_513500" else 0.75
        row["offensive_ok"] = (row["cagr"] >= row["hold_cagr"] * return_guard) and (row["mdd"] >= row["hold_mdd"] * 0.70)
        rows.append(row)
    return rows


def rank_strategies(summary: pd.DataFrame) -> pd.DataFrame:
    pairs = list(summary["pair"].drop_duplicates())
    rows = []
    for strategy, group in summary.groupby("strategy"):
        by_pair = group.set_index("pair")
        row = {"strategy": strategy}
        cagr_gaps = []
        sharpe_gaps = []
        mdd_gaps = []
        for pair in pairs:
            data = by_pair.loc[pair]
            for col in ["cagr", "hold_cagr", "cagr_vs_hold", "mdd", "hold_mdd", "mdd_vs_hold", "sharpe", "hold_sharpe"]:
                row[f"{pair}_{col}"] = float(data[col])
            cagr_gaps.append(float(data["cagr_vs_hold"]))
            sharpe_gaps.append(float(data["sharpe_vs_hold"]))
            mdd_gaps.append(float(data["mdd_vs_hold"]))
        row["cagr_win_count"] = int(sum(x > 0 for x in cagr_gaps))
        row["sharpe_win_count"] = int(sum(x > 0 for x in sharpe_gaps))
        row["mdd_win_count"] = int(sum(x > 0 for x in mdd_gaps))
        row["avg_cagr_vs_hold"] = float(np.mean(cagr_gaps))
        row["min_cagr_vs_hold"] = float(np.min(cagr_gaps))
        row["avg_sharpe_vs_hold"] = float(np.mean(sharpe_gaps))
        row["avg_mdd_vs_hold"] = float(np.mean(mdd_gaps))
        row["score"] = (
            row["cagr_win_count"] * 1.00
            + row["sharpe_win_count"] * 0.35
            + row["mdd_win_count"] * 0.20
            + row["avg_cagr_vs_hold"] * 10.0
            + row["avg_sharpe_vs_hold"] * 0.50
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values(
        ["cagr_win_count", "avg_cagr_vs_hold", "score"],
        ascending=[False, False, False],
    )


def make_selected_strategy(prices: pd.DataFrame, symbols: list[str], name: str) -> pd.DataFrame:
    rebalance = is_rebalance_dates(prices.index)
    if match := re.fullmatch(r"fixed_fraction_(\d+)", name):
        return weekly_hold(fixed_fraction_weights(prices, symbols, int(match.group(1)) / 100), rebalance)
    if match := re.fullmatch(r"portfolio_ma(\d+)_weak(\d+)_c(\d+)", name):
        return weekly_hold(
            portfolio_ma_filter(prices, symbols, int(match.group(1)), int(match.group(2)) / 100, int(match.group(3))),
            rebalance,
        )
    if match := re.fullmatch(r"asset_ma(\d+)_weak(\d+)_c(\d+)", name):
        return weekly_hold(
            asset_ma_filter(prices, symbols, int(match.group(1)), int(match.group(2)) / 100, int(match.group(3))),
            rebalance,
        )
    if match := re.fullmatch(r"dd_filter_h(\d+)_cut(\d+)_rec(\d+)_weak(\d+)", name):
        return weekly_hold(
            drawdown_filter(
                prices,
                symbols,
                int(match.group(1)),
                int(match.group(2)) / 100,
                int(match.group(3)) / 100,
                int(match.group(4)) / 100,
            ),
            rebalance,
        )
    if match := re.fullmatch(r"dd_ma_ma(\d+)_h(\d+)_cut(\d+)_rec(\d+)_weak(\d+)", name):
        return weekly_hold(
            drawdown_ma_filter(
                prices,
                symbols,
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)) / 100,
                int(match.group(4)) / 100,
                int(match.group(5)) / 100,
            ),
            rebalance,
        )
    if match := re.fullmatch(r"ma_band_ma(\d+)_b(\d+)_weak(\d+)", name):
        return weekly_hold(
            ma_band_filter(prices, symbols, int(match.group(1)), int(match.group(2)) / 100, int(match.group(3)) / 100),
            rebalance,
        )
    if match := re.fullmatch(r"vol_brake_v(\d+)_q(\d+)_ma(\d+)_weak(\d+)", name):
        return weekly_hold(
            volatility_brake(
                prices,
                symbols,
                int(match.group(1)),
                int(match.group(2)) / 100,
                int(match.group(3)),
                int(match.group(4)) / 100,
            ),
            rebalance,
        )
    if match := re.fullmatch(r"momentum_accel_l(\d+)_weak(\d+)", name):
        return weekly_hold(
            momentum_acceleration(prices, symbols, int(match.group(1)), int(match.group(2)) / 100, strong_fraction=1.0),
            rebalance,
        )
    if match := re.fullmatch(r"new_high_h(\d+)_ma(\d+)_n(\d+)_weak(\d+)", name):
        return weekly_hold(
            new_high_participation(
                prices,
                symbols,
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)) / 100,
                int(match.group(4)) / 100,
            ),
            rebalance,
        )
    raise ValueError(f"unsupported selected strategy: {name}")


def window_validation(
    prices: pd.DataFrame,
    symbols: list[str],
    pair: str,
    strategy_name: str,
    weights: pd.DataFrame,
) -> list[dict]:
    windows = [
        ("full", None, None),
        ("pre_2019", None, "2018-12-31"),
        ("stress_2019_2022", "2019-01-01", "2022-12-31"),
        ("recent_2023_now", "2023-01-01", None),
        ("current_etf_live", "2020-11-05", None),
    ]
    curve = simulate(prices, weights, name=strategy_name, pair=pair)
    rows = []
    for label, start, end in windows:
        curve_part = curve.copy()
        price_part = prices.copy()
        if start:
            start_ts = pd.Timestamp(start)
            curve_part = curve_part[curve_part["date"] >= start_ts]
            price_part = price_part[price_part.index >= start_ts]
        if end:
            end_ts = pd.Timestamp(end)
            curve_part = curve_part[curve_part["date"] <= end_ts]
            price_part = price_part[price_part.index <= end_ts]
        if len(curve_part) < 126 or len(price_part) < 126:
            continue
        row = metrics(curve_part)
        hold = buy_hold_metrics(price_part, symbols[0], symbols[1])
        row.update(
            {
                "pair": pair,
                "strategy": strategy_name,
                "window": label,
                **hold,
            }
        )
        row["cagr_vs_hold"] = row["cagr"] - row["hold_cagr"]
        row["mdd_vs_hold"] = row["mdd"] - row["hold_mdd"]
        row["sharpe_vs_hold"] = row["sharpe"] - row["hold_sharpe"]
        rows.append(row)
    return rows


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    target_rows = []
    curves = []
    strategies_by_case: dict[str, dict[str, pd.DataFrame]] = {}
    prices_by_case: dict[str, pd.DataFrame] = {}

    for case in CASES:
        prices = build_prices(case)
        symbols = [case.growth_symbol, case.stable_symbol]
        strategies = make_strategies(prices, symbols)
        strategies_by_case[case.name] = strategies
        prices_by_case[case.name] = prices
        summary_rows.extend(summarize_case(case.name, prices, symbols, strategies))
        last_date = prices.index[-1].date().isoformat()
        for name, weights in strategies.items():
            target = {"pair": case.name, "strategy": name, "date": last_date}
            for symbol, value in weights.iloc[-1].items():
                target[symbol] = float(value)
            target_rows.append(target)

    summary = pd.DataFrame(summary_rows)
    rank = rank_strategies(summary)
    selected = list(rank.head(12)["strategy"])

    segment_rows = []
    window_rows = []
    yearly_frames = []
    for case in CASES:
        prices = prices_by_case[case.name]
        symbols = [case.growth_symbol, case.stable_symbol]
        for name in selected:
            weights = strategies_by_case[case.name][name]
            curve = simulate(prices, weights, name=name, pair=case.name)
            curves.append(curve)
            segment_rows.extend(segment_metrics(curve))
            window_rows.extend(window_validation(prices, symbols, case.name, name, weights))
            yearly_frames.append(yearly_metrics(curve))

    all_pair_rows = []
    nasdaq = available_symbols(NASDAQ)
    sp500 = available_symbols(SP500)
    for a in nasdaq:
        for b in sp500:
            prices = build_pair_prices(a, b)
            if len(prices) < 252:
                continue
            symbols = [a, b]
            for name in selected:
                weights = make_selected_strategy(prices, symbols, name)
                row = fast_metrics(prices, weights)
                hold = buy_hold_metrics(prices, a, b)
                row.update(
                    {
                        "pair": f"{a}_{b}",
                        "nasdaq": a,
                        "nasdaq_name": NASDAQ.get(a, ""),
                        "sp500": b,
                        "sp500_name": SP500.get(b, ""),
                        "strategy": name,
                        **hold,
                    }
                )
                row["cagr_vs_hold"] = row["cagr"] - row["hold_cagr"]
                row["mdd_vs_hold"] = row["mdd"] - row["hold_mdd"]
                row["sharpe_vs_hold"] = row["sharpe"] - row["hold_sharpe"]
                all_pair_rows.append(row)

    summary.to_csv(OUT_DIR / "offensive_strategy_summary.csv", index=False)
    rank.to_csv(OUT_DIR / "offensive_strategy_rank.csv", index=False)
    pd.DataFrame(target_rows).to_csv(OUT_DIR / "offensive_strategy_latest_targets.csv", index=False)
    pd.concat(curves, ignore_index=True).to_csv(OUT_DIR / "offensive_strategy_selected_curves.csv", index=False)
    pd.DataFrame(segment_rows).to_csv(OUT_DIR / "offensive_strategy_selected_segments.csv", index=False)
    pd.DataFrame(window_rows).to_csv(OUT_DIR / "offensive_strategy_window_validation.csv", index=False)
    pd.concat(yearly_frames, ignore_index=True).to_csv(OUT_DIR / "offensive_strategy_selected_yearly.csv", index=False)
    all_pair_summary = pd.DataFrame(all_pair_rows)
    all_pair_summary.to_csv(OUT_DIR / "offensive_strategy_pair_validation.csv", index=False)
    pair_stats = (
        all_pair_summary.groupby("strategy")
        .agg(
            pairs=("pair", "count"),
            cagr_wins=("cagr_vs_hold", lambda s: int((s > 0).sum())),
            sharpe_wins=("sharpe_vs_hold", lambda s: int((s > 0).sum())),
            mdd_wins=("mdd_vs_hold", lambda s: int((s > 0).sum())),
            avg_cagr_vs_hold=("cagr_vs_hold", "mean"),
            min_cagr_vs_hold=("cagr_vs_hold", "min"),
            avg_mdd_vs_hold=("mdd_vs_hold", "mean"),
        )
        .reset_index()
        .sort_values(["cagr_wins", "avg_cagr_vs_hold"], ascending=[False, False])
    )
    pair_stats.to_csv(OUT_DIR / "offensive_strategy_pair_stats.csv", index=False)

    cols = [
        "strategy",
        "cagr_win_count",
        "avg_cagr_vs_hold",
        "min_cagr_vs_hold",
        "current_513300_513500_cagr",
        "current_513300_513500_hold_cagr",
        "current_513300_513500_cagr_vs_hold",
        "current_513300_513500_mdd",
        "current_513300_513500_hold_mdd",
        "long_proxy_513100_513500_cagr_vs_hold",
        "mid_proxy_159941_513500_cagr_vs_hold",
    ]
    print(rank[cols].head(30).to_string(index=False))
    print("\nALL PAIR VALIDATION")
    print(pair_stats.head(12).to_string(index=False))


if __name__ == "__main__":
    main()
