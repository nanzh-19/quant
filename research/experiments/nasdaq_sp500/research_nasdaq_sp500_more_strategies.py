from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
DAILY_DIR = ROOT / "data" / "daily"
OUT_DIR = ROOT / "outputs" / "research" / "nasdaq_sp500_more_strategies"

SAFE_SYMBOL = "511010"
COMMISSION = 0.0005
BASE_WEIGHTS = (0.60, 0.40)
TRADING_DAYS = 252


@dataclass(frozen=True)
class PairCase:
    name: str
    growth_symbol: str
    stable_symbol: str


CASES = [
    PairCase("current_513300_513500", "513300", "513500"),
    PairCase("long_proxy_513100_513500", "513100", "513500"),
    PairCase("mid_proxy_159941_513500", "159941", "513500"),
]


def read_close(symbol: str) -> pd.Series:
    path = DAILY_DIR / f"{symbol}.csv"
    df = pd.read_csv(path, dtype={"symbol": str, "market": str}, parse_dates=["date"])
    return df.sort_values("date").set_index("date")["close"].astype(float).rename(symbol)


def build_prices(case: PairCase) -> pd.DataFrame:
    prices = pd.concat(
        [read_close(case.growth_symbol), read_close(case.stable_symbol), read_close(SAFE_SYMBOL)],
        axis=1,
        join="inner",
    ).dropna()
    return prices.loc[prices.index >= max(prices.index.min(), pd.Timestamp("2010-01-01"))].copy()


def is_rebalance_dates(index: pd.DatetimeIndex) -> pd.Series:
    dates = pd.Series(index=index, data=index)
    next_week = dates.shift(-1).dt.isocalendar().week
    this_week = dates.dt.isocalendar().week
    next_year = dates.shift(-1).dt.year
    this_year = dates.dt.year
    return ((next_week != this_week) | (next_year != this_year)).fillna(True)


def trend_gate(prices: pd.DataFrame, symbols: list[str], ma_window: int = 100) -> pd.DataFrame:
    ma = prices[symbols].rolling(ma_window).mean()
    return (prices[symbols] > ma).astype(float)


def portfolio_vol_scale(
    returns: pd.DataFrame,
    raw_weights: pd.DataFrame,
    target_vol: pd.Series | float,
    lookback: int = 20,
    max_exposure: float = 1.0,
) -> pd.DataFrame:
    shifted = raw_weights.shift(1).fillna(0.0)
    port_ret = (shifted * returns[raw_weights.columns]).sum(axis=1)
    realized_vol = port_ret.rolling(lookback).std(ddof=0) * np.sqrt(TRADING_DAYS)
    if isinstance(target_vol, pd.Series):
        target = target_vol.reindex(raw_weights.index).astype(float)
    else:
        target = pd.Series(target_vol, index=raw_weights.index)
    scale = (target / realized_vol).replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0.0, max_exposure)
    return raw_weights.mul(scale, axis=0)


def with_safe(weights: pd.DataFrame, safe_symbol: str = SAFE_SYMBOL) -> pd.DataFrame:
    out = weights.copy()
    risk_sum = out.sum(axis=1).clip(0.0, 1.0)
    out[safe_symbol] = 1.0 - risk_sum
    return out


def weekly_hold(weights: pd.DataFrame, rebalance: pd.Series) -> pd.DataFrame:
    sparse = weights.where(rebalance.reindex(weights.index).astype(bool), np.nan)
    return sparse.ffill().fillna(0.0)


def simulate(prices: pd.DataFrame, target_weights: pd.DataFrame, name: str, pair: str) -> pd.DataFrame:
    symbols = list(target_weights.columns)
    returns = prices[symbols].pct_change().fillna(0.0)
    weights = target_weights.reindex(prices.index).fillna(0.0).clip(lower=0.0)
    row_sum = weights.sum(axis=1)
    weights = weights.div(row_sum.where(row_sum > 1.0, 1.0), axis=0).fillna(0.0)

    prev_weights = pd.Series(0.0, index=symbols)
    rebalance = is_rebalance_dates(prices.index).reindex(prices.index).astype(bool)
    rows = []
    equity = 1.0
    for date in prices.index:
        target = weights.loc[date]
        gross = float((prev_weights * returns.loc[date]).sum())
        if 1.0 + gross > 0:
            drifted = prev_weights * (1.0 + returns.loc[date]) / (1.0 + gross)
        else:
            drifted = prev_weights

        if bool(rebalance.loc[date]):
            turnover = float((target - drifted).abs().sum())
            cost = turnover * COMMISSION
            next_weights = target
        else:
            turnover = 0.0
            cost = 0.0
            next_weights = drifted

        net = (1.0 + gross) * (1.0 - cost) - 1.0
        equity *= 1.0 + net
        prev_weights = next_weights
        rows.append(
            {
                "date": date,
                "pair": pair,
                "strategy": name,
                "return": net,
                "gross_return": gross,
                "cost": cost,
                "turnover": turnover,
                "equity": equity,
                "risk_exposure": float(target.drop(labels=[SAFE_SYMBOL], errors="ignore").sum()),
                "safe_weight": float(target.get(SAFE_SYMBOL, 0.0)),
            }
        )
    return pd.DataFrame(rows)


def metrics(curve: pd.DataFrame) -> dict:
    r = curve["return"].astype(float)
    n = len(curve)
    years = n / TRADING_DAYS
    ending = float(curve["equity"].iloc[-1])
    cagr = ending ** (1.0 / years) - 1.0 if years > 0 and ending > 0 else np.nan
    vol = float(r.std(ddof=0) * np.sqrt(TRADING_DAYS)) if n > 1 else 0.0
    drawdown = curve["equity"] / curve["equity"].cummax() - 1.0
    mdd = float(drawdown.min())
    monthly = curve.assign(month=curve["date"].dt.to_period("M")).groupby("month")["return"].apply(lambda x: (1 + x).prod() - 1)
    return {
        "start": curve["date"].iloc[0].date().isoformat(),
        "end": curve["date"].iloc[-1].date().isoformat(),
        "days": n,
        "cumret": ending - 1.0,
        "cagr": cagr,
        "vol": vol,
        "mdd": mdd,
        "sharpe": cagr / vol if vol > 0 else np.nan,
        "calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "month_win": float((monthly > 0).mean()) if len(monthly) else np.nan,
        "avg_exposure": float(curve["risk_exposure"].mean()),
        "avg_safe": float(curve["safe_weight"].mean()),
        "turnover": float(curve["turnover"].mean()),
    }


def relative_base_weights(prices: pd.DataFrame, symbols: list[str], lookback: int, low: float, high: float) -> pd.DataFrame:
    a, b = symbols
    mom_a = prices[a] / prices[a].shift(lookback) - 1.0
    mom_b = prices[b] / prices[b].shift(lookback) - 1.0
    spread = (mom_a - mom_b).clip(-0.12, 0.12)
    wa = low + (spread + 0.12) / 0.24 * (high - low)
    wa = wa.fillna(BASE_WEIGHTS[0]).clip(low, high)
    return pd.DataFrame({a: wa, b: 1.0 - wa}, index=prices.index)


def make_strategies(prices: pd.DataFrame, case: PairCase) -> dict[str, pd.DataFrame]:
    a, b = case.growth_symbol, case.stable_symbol
    symbols = [a, b]
    returns = prices.pct_change().fillna(0.0)
    rebalance = is_rebalance_dates(prices.index)
    gate100 = trend_gate(prices, symbols, 100)
    gate200 = trend_gate(prices, symbols, 200)
    ma50 = prices[symbols].rolling(50).mean()
    ma150 = prices[symbols].rolling(150).mean()
    ma200 = prices[symbols].rolling(200).mean()

    fixed_base = pd.DataFrame({a: BASE_WEIGHTS[0], b: BASE_WEIGHTS[1]}, index=prices.index)
    hold = with_safe(fixed_base)

    existing_raw = fixed_base * gate100
    existing = with_safe(portfolio_vol_scale(returns, existing_raw, 0.13))

    rs_base_60 = relative_base_weights(prices, symbols, lookback=60, low=0.50, high=0.72)
    rs_tilt_raw = rs_base_60 * gate100
    rs_tilt = with_safe(portfolio_vol_scale(returns, rs_tilt_raw, 0.13))

    rs_base_120 = relative_base_weights(prices, symbols, lookback=120, low=0.52, high=0.70)
    rs_slow_raw = rs_base_120 * gate100
    rs_slow = with_safe(portfolio_vol_scale(returns, rs_slow_raw, 0.13))

    no_full_switch_gate = gate100.replace(0.0, 0.35)
    rs_no_full_switch = with_safe(portfolio_vol_scale(returns, rs_base_60 * no_full_switch_gate, 0.13))

    core = fixed_base * 0.45
    satellite = fixed_base * gate100 * 0.55
    core_sat_vt = with_safe(portfolio_vol_scale(returns, core + satellite, 0.14))

    speed_state = ((prices[symbols] > ma50).astype(float) * 0.35 + (prices[symbols] > ma150).astype(float) * 0.65)
    dual_speed = with_safe(portfolio_vol_scale(returns, fixed_base * speed_state, 0.14))

    dd_60 = prices[symbols] / prices[symbols].rolling(60).max() - 1.0
    pullback_boost = ((prices[symbols] > ma200) & (dd_60 < -0.04) & (dd_60 > -0.16)).astype(float) * 0.12
    pullback_raw = (existing_raw + pullback_boost).clip(0.0, 1.0)
    pullback_add = with_safe(portfolio_vol_scale(returns, pullback_raw, 0.15))

    strong_trend = ((prices[symbols] > ma50) & (prices[symbols] > ma200)).all(axis=1)
    weak_trend = ((prices[symbols] < ma150)).all(axis=1)
    adaptive_target = pd.Series(0.13, index=prices.index)
    adaptive_target.loc[strong_trend] = 0.17
    adaptive_target.loc[weak_trend] = 0.10
    adaptive_rs = with_safe(portfolio_vol_scale(returns, rs_tilt_raw, adaptive_target))

    ratio = prices[a] / prices[b]
    ratio_strong = ratio > ratio.rolling(120).mean()
    ratio_very_strong = ratio > ratio.rolling(120).max().shift(1)
    ratio_base = pd.DataFrame({a: 0.58, b: 0.42}, index=prices.index)
    ratio_base.loc[ratio_strong, [a, b]] = [0.66, 0.34]
    ratio_base.loc[~ratio_strong, [a, b]] = [0.54, 0.46]
    ratio_base.loc[ratio_very_strong.fillna(False), [a, b]] = [0.72, 0.28]
    ratio_breakout = with_safe(portfolio_vol_scale(returns, ratio_base * gate100, 0.13))

    port_price = (prices[a] / prices[a].iloc[0]) * BASE_WEIGHTS[0] + (prices[b] / prices[b].iloc[0]) * BASE_WEIGHTS[1]
    port_gate = (port_price > port_price.rolling(100).mean()).astype(float)
    portfolio_trend = with_safe(portfolio_vol_scale(returns, fixed_base.mul(port_gate, axis=0), 0.13))

    strategies = {
        "hold_60_40": hold,
        "existing_ma100_vt13_safe": existing,
        "rs60_tilt_ma100_vt13_safe": rs_tilt,
        "rs120_tilt_ma100_vt13_safe": rs_slow,
        "rs60_tilt_no_full_switch_vt13_safe": rs_no_full_switch,
        "core45_sat55_ma100_vt14_safe": core_sat_vt,
        "dual_speed_ma50_150_vt14_safe": dual_speed,
        "pullback_add_trend_vt15_safe": pullback_add,
        "adaptive_rs60_vt10_13_17_safe": adaptive_rs,
        "ratio_breakout_tilt_vt13_safe": ratio_breakout,
        "portfolio_ma100_vt13_safe": portfolio_trend,
    }
    return {name: weekly_hold(weights, rebalance) for name, weights in strategies.items()}


def segment_metrics(curve: pd.DataFrame) -> list[dict]:
    segments = [
        ("early", None, "2018-12-31"),
        ("middle", "2019-01-01", "2022-12-31"),
        ("recent", "2023-01-01", None),
    ]
    rows = []
    for label, start, end in segments:
        part = curve.copy()
        if start:
            part = part[part["date"] >= pd.Timestamp(start)]
        if end:
            part = part[part["date"] <= pd.Timestamp(end)]
        if len(part) < 60:
            continue
        normalized = part.copy()
        normalized["equity"] = (1.0 + normalized["return"]).cumprod()
        row = metrics(normalized)
        row.update({"segment": label, "pair": curve["pair"].iloc[0], "strategy": curve["strategy"].iloc[0]})
        rows.append(row)
    return rows


def yearly_metrics(curve: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, part in curve.groupby(curve["date"].dt.year):
        normalized = part.copy()
        normalized["equity"] = (1.0 + normalized["return"]).cumprod()
        row = metrics(normalized)
        row.update({"year": int(year), "pair": curve["pair"].iloc[0], "strategy": curve["strategy"].iloc[0]})
        rows.append(row)
    return pd.DataFrame(rows)


def latest_targets(case: PairCase, prices: pd.DataFrame, strategies: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    last_date = prices.index[-1]
    for name, weights in strategies.items():
        row = {"pair": case.name, "strategy": name, "date": last_date.date().isoformat()}
        for symbol in weights.columns:
            row[symbol] = float(weights.loc[last_date, symbol])
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_rows = []
    segment_rows = []
    yearly_frames = []
    target_frames = []
    curves = []

    for case in CASES:
        prices = build_prices(case)
        strategies = make_strategies(prices, case)
        target_frames.append(latest_targets(case, prices, strategies))
        for name, weights in strategies.items():
            curve = simulate(prices, weights, name=name, pair=case.name)
            curves.append(curve)
            row = metrics(curve)
            row.update({"pair": case.name, "strategy": name})
            summary_rows.append(row)
            segment_rows.extend(segment_metrics(curve))
            yearly_frames.append(yearly_metrics(curve))

    summary = pd.DataFrame(summary_rows).sort_values(["pair", "sharpe", "cagr"], ascending=[True, False, False])
    segments = pd.DataFrame(segment_rows).sort_values(["pair", "segment", "sharpe"], ascending=[True, True, False])
    yearly = pd.concat(yearly_frames, ignore_index=True)
    targets = pd.concat(target_frames, ignore_index=True)
    all_curves = pd.concat(curves, ignore_index=True)

    summary.to_csv(OUT_DIR / "more_strategy_summary.csv", index=False)
    segments.to_csv(OUT_DIR / "more_strategy_segments.csv", index=False)
    yearly.to_csv(OUT_DIR / "more_strategy_yearly.csv", index=False)
    targets.to_csv(OUT_DIR / "more_strategy_latest_targets.csv", index=False)
    all_curves.to_csv(OUT_DIR / "more_strategy_returns.csv", index=False)

    print(summary[["pair", "strategy", "cagr", "vol", "mdd", "sharpe", "avg_exposure", "avg_safe", "turnover"]].to_string(index=False))


if __name__ == "__main__":
    main()
