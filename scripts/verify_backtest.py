#!/usr/bin/env python3
"""验证回测引擎正确性：用 510300 买入持有策略对比手动计算结果。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from quant.indicators import add_basic_indicators
from quant.strategy import BuyAndHoldStrategy
from quant.backtest import run_cross_sectional_backtest_from_panel, summarize_backtest

SYMBOL = "510300"
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "daily" / f"{SYMBOL}.csv"
START_DATE = "2015-01-01"
END_DATE = "2025-12-31"


def manual_buy_and_hold(df: pd.DataFrame) -> dict:
    """手动计算买入持有的收益。"""
    first_close = df["close"].iloc[0]
    last_close = df["close"].iloc[-1]
    cum_return = last_close / first_close - 1
    trading_days = len(df)
    trading_years = trading_days / 252
    annual_return = (1 + cum_return) ** (1 / trading_years) - 1 if trading_years > 0 else 0.0
    daily_returns = df["close"].pct_change().dropna()
    annual_volatility = daily_returns.std() * (252 ** 0.5)
    sharpe = annual_return / annual_volatility if annual_volatility > 0 else 0.0
    equity_curve = df["close"] / first_close
    max_drawdown = (equity_curve / equity_curve.cummax() - 1).min()
    return {
        "cum_return": cum_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "trading_days": trading_days,
        "start_date": str(df["date"].iloc[0].date()),
        "end_date": str(df["date"].iloc[-1].date()),
    }


def engine_buy_and_hold(df: pd.DataFrame) -> tuple[dict, pd.Timestamp, pd.Timestamp]:
    """用回测引擎跑买入持有策略。返回结果和实际使用的日期区间。"""
    enriched = add_basic_indicators(df.copy())
    enriched["fwd_ret_1"] = enriched["close"].shift(-1) / enriched["close"] - 1
    enriched["symbol"] = SYMBOL
    enriched["name"] = "沪深300ETF"
    enriched["asset_type"] = "etf"
    enriched["market_group"] = "etf_sh"
    panel = enriched.dropna(subset=["ret_20", "ret_60", "avg_amount_20", "fwd_ret_1"])

    actual_start = panel["date"].min()
    actual_end = panel["date"].max()

    strategy = BuyAndHoldStrategy({"symbols": [SYMBOL]})
    backtest_cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, _ = run_cross_sectional_backtest_from_panel(panel, strategy, backtest_cfg)
    summary = summarize_backtest(returns_df, 1_000_000)
    row = summary.iloc[0]
    return {
        "cum_return": row["cum_return"],
        "annual_return": row["annual_return"],
        "annual_volatility": row["annual_volatility"],
        "sharpe": row["sharpe"],
        "max_drawdown": row["max_drawdown"],
        "trading_days": int(row["trading_days"]),
        "start_date": str(row["start_date"]),
        "end_date": str(row["end_date"]),
    }, actual_start, actual_end


def main():
    print(f"=== 回测引擎验证：{SYMBOL} 买入持有 ===\n")

    if not DATA_PATH.exists():
        print(f"错误：找不到数据文件 {DATA_PATH}")
        sys.exit(1)

    raw = pd.read_csv(DATA_PATH)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[(raw["date"] >= START_DATE) & (raw["date"] <= END_DATE)].sort_values("date").reset_index(drop=True)
    print(f"数据范围: {raw['date'].iloc[0].date()} ~ {raw['date'].iloc[-1].date()}, 共 {len(raw)} 个交易日\n")

    manual = manual_buy_and_hold(raw)
    engine, engine_start, engine_end = engine_buy_and_hold(raw)

    # 用引擎实际使用的区间重新计算手动结果，确保对齐
    # 引擎用 fwd_ret_1 所以实际持有到 engine_end 的下一个交易日
    aligned = raw[(raw["date"] >= engine_start) & (raw["date"] <= engine_end)].reset_index(drop=True)
    # 引擎的收益 = 从 engine_start 买入，持有到 engine_end 的下一天收盘
    # fwd_ret_1 的最后一天是 engine_end，对应的收益是 engine_end+1 的收盘价
    # 所以手动计算也应该用 engine_start 到 engine_end+1 的收盘价
    next_day_idx = raw.index[raw["date"] > engine_end]
    if len(next_day_idx) > 0:
        last_close = raw.loc[next_day_idx[0], "close"]
    else:
        last_close = aligned["close"].iloc[-1]
    first_close = aligned["close"].iloc[0]
    trading_days = len(aligned)

    cum_return_manual = last_close / first_close - 1
    trading_years = trading_days / 252
    annual_return_manual = (1 + cum_return_manual) ** (1 / trading_years) - 1 if trading_years > 0 else 0.0
    # 用 aligned 区间的日收益率算波动率
    daily_rets = aligned["close"].pct_change().dropna()
    vol_manual = daily_rets.std() * (252 ** 0.5)
    sharpe_manual = annual_return_manual / vol_manual if vol_manual > 0 else 0.0
    eq = aligned["close"] / first_close
    mdd_manual = (eq / eq.cummax() - 1).min()

    manual_aligned = {
        "cum_return": cum_return_manual,
        "annual_return": annual_return_manual,
        "annual_volatility": vol_manual,
        "sharpe": sharpe_manual,
        "max_drawdown": mdd_manual,
        "trading_days": trading_days,
    }

    print(f"{'指标':<20} {'手动计算':>14} {'回测引擎':>14} {'差异':>14}")
    print("-" * 66)
    all_pass = True
    for key in ["cum_return", "annual_return", "annual_volatility", "sharpe", "max_drawdown"]:
        m = manual_aligned[key]
        e = engine[key]
        diff = abs(m - e)
        status = "OK" if diff < 0.02 else "FAIL"
        if status == "FAIL":
            all_pass = False
        print(f"{key:<20} {m:>14.6f} {e:>14.6f} {diff:>12.6f}  {status}")

    print(f"\n手动交易日: {manual_aligned['trading_days']}, 引擎交易日: {engine['trading_days']}")
    print(f"引擎区间: {engine['start_date']} ~ {engine['end_date']}")

    if all_pass:
        print("\n验证通过：回测引擎与手动计算结果一致。")
    else:
        print("\n验证失败：存在较大差异，请检查回测引擎逻辑。")
        sys.exit(1)


if __name__ == "__main__":
    main()
