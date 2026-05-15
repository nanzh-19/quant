from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


DAILY_COLUMNS = [
    "date",
    "open",
    "close",
    "high",
    "low",
    "volume",
    "amount",
    "amplitude",
    "pct_chg",
    "chg",
    "turnover",
    "symbol",
    "market",
]

PRICE_COLUMNS = ["open", "close", "high", "low"]
NUMERIC_COLUMNS = PRICE_COLUMNS + ["volume", "amount", "amplitude", "pct_chg", "chg", "turnover"]


@dataclass(frozen=True)
class DailyQualityStats:
    rows_before: int
    rows_after: int
    duplicate_dates: int
    invalid_ohlc_rows: int
    lot_volume_rows: int
    pct_chg_mismatch_rows: int
    changed: bool


def _coerce_daily_frame(df: pd.DataFrame, symbol: str = "", market: str = "") -> pd.DataFrame:
    out = df.copy()
    if "date" not in out.columns:
        return pd.DataFrame(columns=DAILY_COLUMNS)

    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    for column in NUMERIC_COLUMNS:
        if column not in out.columns:
            out[column] = 0.0
        out[column] = pd.to_numeric(out[column], errors="coerce")

    if "symbol" not in out.columns:
        out["symbol"] = symbol
    if "market" not in out.columns:
        out["market"] = market
    if symbol:
        out["symbol"] = symbol
    if market:
        out["market"] = market

    out["symbol"] = out["symbol"].astype(str).str.zfill(6)
    out["market"] = out["market"].astype(str)
    return out


def _invalid_ohlc_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    positive_prices = (df[PRICE_COLUMNS] > 0).all(axis=1)
    high_enough = df["high"] >= df[["open", "close", "low"]].max(axis=1)
    low_enough = df["low"] <= df[["open", "close", "high"]].min(axis=1)
    nonnegative_trading = (df["volume"] >= 0) & (df["amount"] >= 0)
    return ~(positive_prices & high_enough & low_enough & nonnegative_trading)


def _lot_volume_mask(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=bool)
    valid = (df["close"] > 0) & (df["volume"] > 0) & (df["amount"] > 0)
    ratio = df["amount"] / (df["close"] * df["volume"])
    candidate = valid & ratio.between(30, 200)
    # Require at least 10 consecutive rows to confirm a genuine lot-unit segment,
    # avoiding false positives on early low-price stocks after forward-adjustment.
    streak = candidate.astype(int)
    groups = streak.ne(streak.shift()).cumsum()
    group_sizes = streak.groupby(groups).transform("sum")
    return candidate & (group_sizes >= 10)


def _recompute_derived_fields(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    out["chg"] = (out["close"] - prev_close).fillna(0.0)
    out["pct_chg"] = ((out["close"] / prev_close - 1.0) * 100.0).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    out["amplitude"] = (((out["high"] - out["low"]) / prev_close) * 100.0).replace([float("inf"), float("-inf")], pd.NA).fillna(0.0)
    return out


def normalize_daily_frame(df: pd.DataFrame, symbol: str = "", market: str = "") -> pd.DataFrame:
    """Return a canonical daily OHLCV frame for local storage.

    Local convention:
    - one row per real trading day
    - OHLC are positive and internally consistent
    - volume is stored in shares, not lots
    - pct_chg/chg/amplitude are recomputed from the full adjusted close series
    - turnover is stored as a fraction when it is clearly reported as percent
    """
    out = _coerce_daily_frame(df, symbol=symbol, market=market)
    if out.empty:
        return out[DAILY_COLUMNS]

    out = out.sort_values("date").reset_index(drop=True)
    out = out.drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out = out.loc[~_invalid_ohlc_mask(out)].copy()
    if out.empty:
        return out[DAILY_COLUMNS]

    lot_mask = _lot_volume_mask(out)
    if lot_mask.any():
        out.loc[lot_mask, "volume"] = out.loc[lot_mask, "volume"] * 100.0

    turnover = pd.to_numeric(out["turnover"], errors="coerce").fillna(0.0)
    out["turnover"] = turnover.mask(turnover > 1.0, turnover / 100.0)
    out = _recompute_derived_fields(out)

    for column in DAILY_COLUMNS:
        if column not in out.columns:
            out[column] = "" if column in {"symbol", "market"} else 0.0
    return out[DAILY_COLUMNS].sort_values("date").reset_index(drop=True)


def audit_daily_frame(df: pd.DataFrame, symbol: str = "", market: str = "") -> DailyQualityStats:
    raw = _coerce_daily_frame(df, symbol=symbol, market=market)
    if raw.empty:
        normalized = normalize_daily_frame(df, symbol=symbol, market=market)
        return DailyQualityStats(
            rows_before=len(df),
            rows_after=len(normalized),
            duplicate_dates=0,
            invalid_ohlc_rows=0,
            lot_volume_rows=0,
            pct_chg_mismatch_rows=0,
            changed=len(df) != len(normalized),
        )

    ordered = raw.sort_values("date").reset_index(drop=True)
    duplicate_dates = int(ordered.duplicated(subset=["date"], keep="last").sum())
    invalid_ohlc_rows = int(_invalid_ohlc_mask(ordered).sum())
    lot_volume_rows = int(_lot_volume_mask(ordered).sum())

    prev_close = ordered["close"].shift(1)
    calc_pct = ((ordered["close"] / prev_close - 1.0) * 100.0).replace([float("inf"), float("-inf")], pd.NA)
    pct_diff = (ordered["pct_chg"] - calc_pct).abs()
    pct_chg_mismatch_rows = int(((ordered.index > 0) & pct_diff.gt(0.05) & calc_pct.notna()).sum())

    normalized = normalize_daily_frame(df, symbol=symbol, market=market)
    comparable = ordered[DAILY_COLUMNS].copy() if set(DAILY_COLUMNS).issubset(ordered.columns) else ordered.copy()
    comparable = normalize_daily_frame(comparable, symbol=symbol, market=market)
    changed = len(normalized) != len(raw) or duplicate_dates > 0 or invalid_ohlc_rows > 0 or lot_volume_rows > 0 or pct_chg_mismatch_rows > 0
    changed = changed or not comparable.equals(normalized)

    return DailyQualityStats(
        rows_before=len(raw),
        rows_after=len(normalized),
        duplicate_dates=duplicate_dates,
        invalid_ohlc_rows=invalid_ohlc_rows,
        lot_volume_rows=lot_volume_rows,
        pct_chg_mismatch_rows=pct_chg_mismatch_rows,
        changed=changed,
    )
