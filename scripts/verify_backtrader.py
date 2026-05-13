#!/usr/bin/env python3
"""用 backtrader 交叉验证自研回测引擎：510300 买入持有 + 简单动量策略。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import backtrader as bt
from quant.indicators import add_basic_indicators
from quant.strategy import BuyAndHoldStrategy
from quant.backtest import run_cross_sectional_backtest_from_panel, summarize_backtest

SYMBOL = "510300"
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "daily" / f"{SYMBOL}.csv"
START_DATE = "2018-01-01"
END_DATE = "2023-12-31"


class BtBuyAndHold(bt.Strategy):
    def __init__(self):
        self.bought = False

    def next(self):
        if not self.bought:
            self.buy(size=int(self.broker.getcash() / self.data.close[0]))
            self.bought = True


class BtMomentum(bt.Strategy):
    params = (("period", 20), ("rebal_interval", 5),)

    def __init__(self):
        self.counter = 0
        self.roc = bt.indicators.ROC(self.data.close, period=self.p.period)

    def next(self):
        self.counter += 1
        if self.counter % self.p.rebal_interval != 1:
            return
        if self.roc[0] > 0 and not self.position:
            self.buy(size=int(self.broker.getcash() / self.data.close[0]))
        elif self.roc[0] <= 0 and self.position:
            self.close()


def run_backtrader(bt_strategy_cls, df: pd.DataFrame, **kwargs) -> dict:
    cerebro = bt.Cerebro()
    cerebro.addstrategy(bt_strategy_cls, **kwargs)
    bt_df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    bt_df = bt_df.set_index("date")
    data = bt.feeds.PandasData(dataname=bt_df)
    cerebro.adddata(data)
    cerebro.broker.setcash(1_000_000)
    cerebro.broker.setcommission(commission=0.0)
    cerebro.broker.set_coc(True)
    results = cerebro.run()
    final_value = cerebro.broker.getvalue()
    cum_return = final_value / 1_000_000 - 1
    return {"cum_return": cum_return, "final_value": final_value}


def run_our_engine_bah(df: pd.DataFrame) -> tuple[dict, pd.Timestamp, pd.Timestamp]:
    """返回引擎结果和实际使用的数据区间。"""
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
    cfg = {
        "initial_capital": 1_000_000,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "slippage_rate": 0.0,
        "rebalance_frequency": "daily",
    }
    returns_df, _ = run_cross_sectional_backtest_from_panel(panel, strategy, cfg)
    summary = summarize_backtest(returns_df, 1_000_000)
    row = summary.iloc[0]
    return {
        "cum_return": row["cum_return"],
        "final_value": row["ending_equity"],
    }, actual_start, actual_end


def main():
    print("=== backtrader 交叉验证 ===\n")

    if not DATA_PATH.exists():
        print(f"错误：找不到数据文件 {DATA_PATH}")
        sys.exit(1)

    raw = pd.read_csv(DATA_PATH)
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw[(raw["date"] >= START_DATE) & (raw["date"] <= END_DATE)].sort_values("date").reset_index(drop=True)
    print(f"原始数据范围: {raw['date'].iloc[0].date()} ~ {raw['date'].iloc[-1].date()}, 共 {len(raw)} 个交易日\n")

    # --- 测试 1: 买入持有（对齐数据区间） ---
    print("--- 测试 1: 买入持有 ---")
    our_bah, engine_start, engine_end = run_our_engine_bah(raw)

    # 用引擎相同的区间喂给 backtrader，确保公平对比
    aligned = raw[(raw["date"] >= engine_start) & (raw["date"] <= engine_end)].reset_index(drop=True)
    print(f"对齐后区间: {aligned['date'].iloc[0].date()} ~ {aligned['date'].iloc[-1].date()}, 共 {len(aligned)} 个交易日")

    bt_bah = run_backtrader(BtBuyAndHold, aligned)
    diff_bah = abs(bt_bah["cum_return"] - our_bah["cum_return"])
    print(f"  backtrader  累计收益: {bt_bah['cum_return']:.6f}")
    print(f"  自研引擎    累计收益: {our_bah['cum_return']:.6f}")
    print(f"  差异: {diff_bah:.6f}  {'OK' if diff_bah < 0.02 else 'FAIL'}")
    print(f"  (注: backtrader 持有到最后一天收盘，引擎用 fwd_ret_1 持有到次日收盘，会有微小差异)\n")

    # --- 测试 2: 简单动量 ---
    print("--- 测试 2: 简单动量（ROC20 > 0 买入，<= 0 卖出，每5天检查） ---")
    bt_mom = run_backtrader(BtMomentum, raw, period=20, rebal_interval=5)
    print(f"  backtrader  累计收益: {bt_mom['cum_return']:.6f}")
    print(f"  (自研引擎的动量策略是多标的横截面选股，与 backtrader 单标的时序策略不直接可比)")
    print(f"  此测试仅验证 backtrader 能正常运行，策略逻辑差异是预期的\n")

    all_pass = diff_bah < 0.02
    if all_pass:
        print("交叉验证通过：买入持有策略在两个引擎中结果一致。")
    else:
        print("交叉验证失败：买入持有策略结果差异过大。")
        sys.exit(1)


if __name__ == "__main__":
    main()
