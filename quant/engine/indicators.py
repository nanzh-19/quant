from __future__ import annotations

import numpy as np
import pandas as pd


def _rolling_log_regression_score(close: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    x_var = float((x_centered**2).sum())

    def _score(values) -> float:
        if len(values) != window or (values <= 0).any():
            return np.nan
        y = np.log(values.astype(float))
        y_centered = y - y.mean()
        slope = float((x_centered * y_centered).sum() / x_var)
        fitted = y.mean() + slope * x_centered
        ss_res = float(((y - fitted) ** 2).sum())
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot
        annualized_return = np.exp(slope * 250.0) - 1.0
        return float(annualized_return * r2)

    return close.rolling(window).apply(_score, raw=True)


def _rolling_weighted_log_slope(close: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    weights = np.linspace(1.0, 2.0, window, dtype=float)
    w_sum = float(weights.sum())
    x_mean = float((weights * x).sum() / w_sum)
    x_centered = x - x_mean
    x_var = float((weights * x_centered**2).sum())

    def _score(values) -> float:
        if len(values) != window or (values <= 0).any():
            return np.nan
        y = np.log(values.astype(float))
        y_mean = float((weights * y).sum() / w_sum)
        slope = float((weights * x_centered * (y - y_mean)).sum() / x_var)
        intercept = y_mean - slope * x_mean
        y_pred = slope * x + intercept
        ss_res = float((weights * (y - y_pred) ** 2).sum())
        ss_tot = float((weights * (y - y_mean) ** 2).sum())
        r_squared = 0.0 if ss_tot <= 0 else 1.0 - ss_res / ss_tot
        annualized_returns = np.exp(slope * 250.0) - 1.0
        return float(annualized_returns * r_squared)

    return close.rolling(window).apply(_score, raw=True)


def _rolling_rsrs_beta(low: pd.Series, high: pd.Series, window: int) -> pd.Series:
    x = low.astype(float)
    y = high.astype(float)
    return x.rolling(window).cov(y) / x.rolling(window).var()


def add_basic_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.sort_values("date").copy()
    out["ret_1"] = out["close"].pct_change(1)
    out["ret_20"] = out["close"].pct_change(20)
    out["ret_60"] = out["close"].pct_change(60)
    out["ret_120"] = out["close"].pct_change(120)
    out["ma_20"] = out["close"].rolling(20).mean()
    out["ma_60"] = out["close"].rolling(60).mean()
    out["ma_120"] = out["close"].rolling(120).mean()
    out["ma_200"] = out["close"].rolling(200).mean()
    out["ma_gap_20_60"] = out["ma_20"] / out["ma_60"] - 1.0
    out["avg_amount_20"] = out["amount"].rolling(20).mean()
    out["volatility_20"] = out["ret_1"].rolling(20).std(ddof=0) * (20**0.5)
    out["drawdown_20"] = out["close"] / out["close"].rolling(20).max() - 1.0
    out["reg_momentum_25"] = _rolling_log_regression_score(out["close"], 25)
    out["weighted_slope_25"] = _rolling_weighted_log_slope(out["close"], 25)
    out["rsrs_beta_18"] = _rolling_rsrs_beta(out["low"], out["high"], 18)
    rsrs_mean = out["rsrs_beta_18"].rolling(600).mean()
    rsrs_std = out["rsrs_beta_18"].rolling(600).std(ddof=0)
    out["rsrs_z_18_600"] = (out["rsrs_beta_18"] - rsrs_mean) / rsrs_std
    return out
