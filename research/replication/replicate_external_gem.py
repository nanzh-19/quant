#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from quant.backtest import run_cross_sectional_backtest_from_panel
from quant.strategy import BaseStrategy


SOURCE_REPO = "https://github.com/alexjansenhome/GEM"
SOURCE_COMMIT = "d3815bc66cfbbbe023a02f9cedcb2dc477896596"
SOURCE_FILE = "msci_all_gross.csv"
SOURCE_URL = f"https://raw.githubusercontent.com/alexjansenhome/GEM/{SOURCE_COMMIT}/{SOURCE_FILE}"

PUBLIC_EXPECTED = {
    "scenario": "GEM 12-month dual momentum since 1970, not inflation adjusted",
    "annual_return_pct": 16.29,
    "max_drawdown_pct": -18.96,
    "max_drawdown_years": 3.1,
    "min_10y_annual_pct": 8.05,
    "min_20y_annual_pct": 12.08,
    "min_30y_annual_pct": 13.44,
}

INITIAL_CAPITAL = 100_000.0
START_DATE = pd.Timestamp("1970-12-01")
MOMENTUM_MONTHS = 12

FOREIGN_COL = 3
SP500_COL = 7
BONDS_COL = 10
BILLS_COL = 15
ASSETS = {
    "FOREIGN": FOREIGN_COL,
    "SP500": SP500_COL,
    "BONDS": BONDS_COL,
}


class PrecomputedTargetStrategy(BaseStrategy):
    @property
    def name(self) -> str:
        return "external_gem_precomputed_target"

    def params(self) -> dict:
        return {"source": SOURCE_REPO, "commit": SOURCE_COMMIT}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty or "target" not in snapshot.columns:
            return snapshot.head(0)
        selected = snapshot[snapshot["target"].fillna(False)].copy()
        if selected.empty:
            return snapshot.head(0)
        selected["score"] = 1.0
        selected["reason"] = "external_gem_target"
        return selected.reset_index(drop=True)


def _download_source(cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        urllib.request.urlretrieve(SOURCE_URL, cache_path)
    return cache_path


def _load_monthlies(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        thousands=",",
        decimal=".",
        index_col=0,
        skiprows=2,
        parse_dates=[0],
        date_format="%m/%d/%y",
    )
    return df.sort_index()


def _choose_asset(monthlies: pd.DataFrame, idx_now: int) -> str:
    row_now = monthlies.iloc[idx_now]
    row_prev = monthlies.iloc[idx_now - MOMENTUM_MONTHS]
    m_foreign = (row_now.iloc[FOREIGN_COL] - row_prev.iloc[FOREIGN_COL]) / row_prev.iloc[FOREIGN_COL]
    m_sp500 = (row_now.iloc[SP500_COL] - row_prev.iloc[SP500_COL]) / row_prev.iloc[SP500_COL]
    m_bonds = (row_now.iloc[BONDS_COL] - row_prev.iloc[BONDS_COL]) / row_prev.iloc[BONDS_COL]
    m_bills = (row_now.iloc[BILLS_COL] - row_prev.iloc[BILLS_COL]) / row_prev.iloc[BILLS_COL]
    if m_sp500 >= m_bills:
        return "SP500" if m_sp500 >= m_foreign else "FOREIGN"
    return "BONDS"


def _external_reference_run(monthlies: pd.DataFrame) -> tuple[list[float], list[pd.Timestamp], list[str]]:
    current_alloc: list[tuple[str, float]] = []
    prev_cash = INITIAL_CAPITAL
    result = [prev_cash]
    dates = []
    decisions = []

    for date in monthlies.index:
        if date < START_DATE:
            continue
        idx_now = monthlies.index.get_loc(date)
        row_now = monthlies.iloc[idx_now]

        cash = 0.0
        for symbol, shares in current_alloc:
            if shares:
                cash += shares * row_now.iloc[ASSETS[symbol]]
        if cash == 0.0:
            cash = prev_cash

        chosen = _choose_asset(monthlies, idx_now)
        current_alloc = [(chosen, cash / row_now.iloc[ASSETS[chosen]])]

        dates.append(pd.Timestamp(date))
        decisions.append(chosen)
        prev_cash = cash
        result.append(cash)

    return result, dates, decisions


def _build_engine_panel(monthlies: pd.DataFrame, dates: list[pd.Timestamp], decisions: list[str]) -> pd.DataFrame:
    rows = []
    date_set = set(dates)
    date_to_decision = dict(zip(dates, decisions))
    for date in dates:
        idx = monthlies.index.get_loc(date)
        prev_date = monthlies.index[idx - 1] if idx > 0 else None
        previous_decision = date_to_decision.get(prev_date) if prev_date in date_set else ""
        row_now = monthlies.iloc[idx]
        row_prev = monthlies.iloc[idx - 1] if idx > 0 else None
        for symbol, col_idx in ASSETS.items():
            if row_prev is None or prev_date not in date_set:
                realized_return = 0.0
            else:
                realized_return = float(row_now.iloc[col_idx] / row_prev.iloc[col_idx] - 1.0)
            rows.append(
                {
                    "date": pd.Timestamp(date),
                    "symbol": symbol,
                    "name": symbol,
                    "asset_type": "external_index",
                    "market_group": "gem",
                    "close": float(row_now.iloc[col_idx]),
                    "ret_20": 0.0,
                    "ret_60": 0.0,
                    "avg_amount_20": 1.0,
                    "fwd_ret_1": realized_return,
                    "target": symbol == previous_decision,
                }
            )
    return pd.DataFrame(rows)


def _external_metrics(result: list[float], dates: list[pd.Timestamp]) -> dict:
    maxdown_pct = 0.0
    maxdown_pct_end = 0
    maxdown_pct_start = 0
    curdown_pct_start = 0
    maxdown_len = 0
    maxdown_len_start = 0
    curdown_len_start = 0

    for d in range(0, len(result) - 1):
        if result[d] < result[curdown_len_start]:
            if maxdown_len < d - curdown_len_start:
                maxdown_len = d - curdown_len_start
                maxdown_len_start = curdown_len_start
        else:
            curdown_len_start = d

        if result[d] < result[curdown_pct_start]:
            drawdown = (result[d] - result[curdown_pct_start]) / result[curdown_pct_start]
            if drawdown < maxdown_pct:
                maxdown_pct = drawdown
                maxdown_pct_start = curdown_pct_start
                maxdown_pct_end = d
        else:
            curdown_pct_start = d

    years = (len(result) - 1) / 12.0
    annual_return_pct = (math.pow(result[-1] / result[0], 1.0 / years) - 1.0) * 100.0
    bounds = []
    for dur in range(1, 482):
        gain_min = 100000000000.0
        gain_max = -100000000000.0
        gain_avg = 0.0
        dmin = 0
        for win in range(0, len(result) - dur - 1):
            gain = (result[win + dur] - result[win]) / result[win]
            if gain < gain_min:
                dmin = win
                gain_min = gain
            if gain > gain_max:
                gain_max = gain
            gain_avg += gain
        gain_avg = gain_avg / (len(result) - dur - 1)
        bounds.append({"min": gain_min, "mindate": dmin, "avg": gain_avg, "max": gain_max})

    worst_annualized = {}
    avg_annualized = {}
    for dur in [120, 240, 360]:
        gain_min = math.pow(1.0 + bounds[dur]["min"], 12.0 / dur) - 1.0
        gain_avg = math.pow(1.0 + bounds[dur]["avg"], 12.0 / dur) - 1.0
        for dur2 in range(dur, 481):
            candidate = math.pow(1.0 + bounds[dur2]["min"], 12.0 / dur2) - 1.0
            if candidate < gain_min:
                gain_min = candidate
        worst_annualized[dur] = gain_min * 100.0
        avg_annualized[dur] = gain_avg * 100.0

    return {
        "annual_return_pct": annual_return_pct,
        "max_drawdown_pct": maxdown_pct * 100.0,
        "max_drawdown_years": maxdown_len / 12.0,
        "max_drawdown_start": str(dates[max(maxdown_pct_start - 1, 0)].date()),
        "max_drawdown_end": str(dates[max(maxdown_pct_end - 2, 0)].date()),
        "min_10y_annual_pct": worst_annualized[120],
        "min_20y_annual_pct": worst_annualized[240],
        "min_30y_annual_pct": worst_annualized[360],
        "avg_10y_annual_pct": avg_annualized[120],
        "avg_20y_annual_pct": avg_annualized[240],
        "avg_30y_annual_pct": avg_annualized[360],
    }


def _status(value: float, expected: float, tolerance: float = 0.005) -> str:
    return "pass" if abs(value - expected) <= tolerance else "fail"


def _public_status(metric: str, value: float, expected: float) -> str:
    tolerance = 0.05 if metric == "max_drawdown_years" else 0.005
    return _status(value, expected, tolerance=tolerance)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replicate external GEM public code/data and compare with local backtest engine.")
    parser.add_argument("--cache-path", default=str(PROJECT_ROOT / "outputs" / "validation" / "external" / "sources" / SOURCE_COMMIT / SOURCE_FILE))
    args = parser.parse_args()

    source_path = _download_source(Path(args.cache_path))
    monthlies = _load_monthlies(source_path)
    reference_result, dates, decisions = _external_reference_run(monthlies)
    reference_metrics = _external_metrics(reference_result, dates)

    panel = _build_engine_panel(monthlies, dates, decisions)
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(
        panel,
        PrecomputedTargetStrategy(),
        {
            "initial_capital": INITIAL_CAPITAL,
            "commission_rate": 0.0,
            "slippage_rate": 0.0,
            "stamp_duty_rate": 0.0,
            "rebalance_frequency": "daily",
        },
    )
    engine_result = [INITIAL_CAPITAL] + returns_df["equity"].astype(float).tolist()
    engine_metrics = _external_metrics(engine_result, dates)

    reference_returns = pd.Series(reference_result[1:], index=dates).pct_change().fillna(0.0).reset_index(drop=True)
    engine_returns = returns_df["return"].reset_index(drop=True)
    max_monthly_return_diff = float((reference_returns - engine_returns).abs().max())
    final_equity_diff = abs(float(reference_result[-1]) - float(returns_df["equity"].iloc[-1]))

    rows = []
    for key, public_value in PUBLIC_EXPECTED.items():
        if key == "scenario":
            continue
        rows.append(
            {
                "metric": key,
                "public_expected": public_value,
                "external_reference": reference_metrics[key],
                "engine_result": engine_metrics[key],
                "public_status": _public_status(key, reference_metrics[key], public_value),
                "engine_status": _status(engine_metrics[key], reference_metrics[key], tolerance=1e-9),
            }
        )
    summary = pd.DataFrame(rows)
    out_dir = PROJECT_ROOT / "outputs" / "validation" / "external" / "gem"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_path = out_dir / "replication_external_gem_summary.csv"
    returns_path = out_dir / "replication_external_gem_returns.csv"
    picks_path = out_dir / "replication_external_gem_picks.csv"
    summary.to_csv(summary_path, index=False)
    returns_df.to_csv(returns_path, index=False)
    picks_df.to_csv(picks_path, index=False)

    print(f"source={SOURCE_REPO}")
    print(f"commit={SOURCE_COMMIT}")
    print(f"scenario={PUBLIC_EXPECTED['scenario']}")
    print(f"max_monthly_return_diff={max_monthly_return_diff:.16f}")
    print(f"final_equity_diff={final_equity_diff:.10f}")
    print(summary.to_string(index=False))
    print(f"saved={summary_path}")

    if max_monthly_return_diff > 1e-12 or final_equity_diff > 1e-6:
        raise SystemExit(1)
    if (summary["public_status"] != "pass").any() or (summary["engine_status"] != "pass").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
