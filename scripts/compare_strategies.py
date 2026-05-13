#!/usr/bin/env python3
"""多策略回测对比 + backtrader 交叉验证，生成 JSON 结果供报告使用。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import backtrader as bt
from quant.indicators import add_basic_indicators
from quant.strategy import (
    MomentumStrategy,
    MeanReversionStrategy,
    LowVolatilityStrategy,
    DualMAStrategy,
    BuyAndHoldStrategy,
)
from quant.backtest import build_backtest_panel, run_cross_sectional_backtest_from_panel, summarize_backtest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DAILY_DIR = PROJECT_ROOT / "data" / "daily"
UNIVERSE_PATH = PROJECT_ROOT / "data" / "universe.csv"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
START_DATE = "2018-01-01"
END_DATE = "2025-12-31"


STRATEGIES = {
    "momentum": {
        "class": MomentumStrategy,
        "cfg": {
            "name": "momentum",
            "max_positions": 10,
            "eligible_asset_types": ["stock", "etf"],
            "min_price_stock": 5.0,
            "min_price_etf": 0.5,
            "min_avg_turnover_million_stock": 80,
            "min_avg_turnover_million_etf": 30,
            "min_ret_20": 0.0,
            "min_ret_60": 0.0,
            "min_ret_120": 0.1,
            "max_volatility_20": 0.28,
            "min_drawdown_20": -0.12,
            "require_ma_trend": True,
        },
        "label": "动量策略",
    },
    "mean_reversion": {
        "class": MeanReversionStrategy,
        "cfg": {
            "name": "mean_reversion",
            "max_positions": 10,
            "max_ret_20": -0.05,
            "min_ret_60": -0.10,
            "max_volatility_20": 0.35,
            "min_price_stock": 5.0,
            "min_price_etf": 0.5,
            "min_avg_turnover_million_stock": 80,
            "min_avg_turnover_million_etf": 30,
        },
        "label": "均值回归策略",
    },
    "low_volatility": {
        "class": LowVolatilityStrategy,
        "cfg": {
            "name": "low_volatility",
            "max_positions": 20,
            "min_ret_60": -0.05,
            "min_price_stock": 5.0,
            "min_price_etf": 0.5,
            "min_avg_turnover_million_stock": 80,
            "min_avg_turnover_million_etf": 30,
        },
        "label": "低波动策略",
    },
    "dual_ma": {
        "class": DualMAStrategy,
        "cfg": {
            "name": "dual_ma",
            "max_positions": 10,
            "min_ret_120": 0.0,
            "min_price_stock": 5.0,
            "min_price_etf": 0.5,
            "min_avg_turnover_million_stock": 80,
            "min_avg_turnover_million_etf": 30,
        },
        "label": "双均线趋势策略",
    },
}

BACKTEST_CFG_WEEKLY = {
    "initial_capital": 1_000_000,
    "commission_rate": 0.001,
    "stamp_duty_rate": 0.001,
    "slippage_rate": 0.0005,
    "rebalance_frequency": "weekly",
}


class BtBuyAndHold(bt.Strategy):
    def __init__(self):
        self.bought = False

    def next(self):
        if not self.bought:
            self.buy(size=int(self.broker.getcash() * 0.95 / self.data.close[0]))
            self.bought = True


class BtMomentumSimple(bt.Strategy):
    """简化版动量：ROC60 > 0 且价格在 SMA20 之上时满仓，否则空仓。"""
    params = (("roc_period", 60), ("ma_period", 20), ("rebal_interval", 5),)

    def __init__(self):
        self.counter = 0
        self.roc = bt.indicators.ROC(self.data.close, period=self.p.roc_period)
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)

    def next(self):
        self.counter += 1
        if self.counter % self.p.rebal_interval != 1:
            return
        if self.roc[0] > 0 and self.data.close[0] > self.sma[0] and not self.position:
            self.buy(size=int(self.broker.getcash() * 0.95 / self.data.close[0]))
        elif (self.roc[0] <= 0 or self.data.close[0] <= self.sma[0]) and self.position:
            self.close()


class BtMeanReversion(bt.Strategy):
    """简化版均值回归：RSI14 < 30 时买入，> 70 时卖出。"""
    params = (("rsi_period", 14), ("oversold", 30), ("overbought", 70),)

    def __init__(self):
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)

    def next(self):
        if self.rsi[0] < self.p.oversold and not self.position:
            self.buy(size=int(self.broker.getcash() * 0.95 / self.data.close[0]))
        elif self.rsi[0] > self.p.overbought and self.position:
            self.close()


class BtDualMA(bt.Strategy):
    """双均线：MA20 > MA60 时满仓，否则空仓。"""
    params = (("fast", 20), ("slow", 60),)

    def __init__(self):
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow)

    def next(self):
        if self.fast_ma[0] > self.slow_ma[0] and not self.position:
            self.buy(size=int(self.broker.getcash() * 0.95 / self.data.close[0]))
        elif self.fast_ma[0] <= self.slow_ma[0] and self.position:
            self.close()


def run_bt(strategy_cls, df, commission=0.001, **kwargs):
    cerebro = bt.Cerebro()
    cerebro.addstrategy(strategy_cls, **kwargs)
    bt_df = df[["date", "open", "high", "low", "close", "volume"]].copy().set_index("date")
    cerebro.adddata(bt.feeds.PandasData(dataname=bt_df))
    cerebro.broker.setcash(1_000_000)
    cerebro.broker.setcommission(commission=commission)
    cerebro.broker.set_coc(True)
    cerebro.run()
    final = cerebro.broker.getvalue()
    cum_ret = final / 1_000_000 - 1
    return {"cum_return": cum_ret, "final_value": final}


def main():
    print("=== 多策略回测对比 ===\n")

    if not UNIVERSE_PATH.exists():
        print(f"错误：找不到 {UNIVERSE_PATH}")
        sys.exit(1)

    universe = pd.read_csv(UNIVERSE_PATH)
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    print(f"股票池: {len(universe)} 个标的")

    print("构建回测面板（这可能需要几分钟）...")
    t0 = time.time()
    panel = build_backtest_panel(
        universe=universe,
        daily_dir=DAILY_DIR,
        min_history_days_stock=200,
        min_history_days_etf=120,
    )
    panel = panel[(panel["date"] >= START_DATE) & (panel["date"] <= END_DATE)]
    elapsed = time.time() - t0
    print(f"面板构建完成: {len(panel)} 行, {panel['symbol'].nunique()} 个标的, 耗时 {elapsed:.1f}s\n")

    results = {}
    for key, spec in STRATEGIES.items():
        print(f"回测 [{spec['label']}] ...")
        strategy = spec["class"](spec["cfg"])
        returns_df, picks_df = run_cross_sectional_backtest_from_panel(panel, strategy, BACKTEST_CFG_WEEKLY)
        if returns_df.empty:
            print(f"  {spec['label']}: 无交易记录，跳过")
            results[key] = {"label": spec["label"], "error": "无交易记录"}
            continue
        summary = summarize_backtest(returns_df, 1_000_000)
        row = summary.iloc[0]
        r = {
            "label": spec["label"],
            "cum_return": float(row["cum_return"]),
            "annual_return": float(row["annual_return"]),
            "annual_volatility": float(row["annual_volatility"]),
            "sharpe": float(row["sharpe"]),
            "max_drawdown": float(row["max_drawdown"]),
            "win_rate": float(row["win_rate"]),
            "avg_turnover": float(row["avg_turnover"]),
            "avg_positions": float(row["avg_positions"]),
            "trading_days": int(row["trading_days"]),
            "start_date": str(row["start_date"]),
            "end_date": str(row["end_date"]),
        }
        results[key] = r
        print(f"  累计收益: {r['cum_return']:.2%}, 年化: {r['annual_return']:.2%}, "
              f"Sharpe: {r['sharpe']:.3f}, 最大回撤: {r['max_drawdown']:.2%}")

    # --- backtrader 交叉验证（用 510300 单标的） ---
    print("\n=== backtrader 交叉验证（510300 单标的） ===\n")
    etf_path = DAILY_DIR / "510300.csv"
    etf_df = pd.read_csv(etf_path)
    etf_df["date"] = pd.to_datetime(etf_df["date"])
    etf_df = etf_df[(etf_df["date"] >= START_DATE) & (etf_df["date"] <= END_DATE)].sort_values("date").reset_index(drop=True)

    bt_results = {}
    bt_tests = [
        ("buy_and_hold", "买入持有", BtBuyAndHold, {}),
        ("bt_momentum", "动量(ROC60+MA20)", BtMomentumSimple, {"roc_period": 60, "ma_period": 20, "rebal_interval": 5}),
        ("bt_mean_reversion", "均值回归(RSI14)", BtMeanReversion, {"rsi_period": 14, "oversold": 30, "overbought": 70}),
        ("bt_dual_ma", "双均线(20/60)", BtDualMA, {"fast": 20, "slow": 60}),
    ]
    for key, label, cls, kwargs in bt_tests:
        r = run_bt(cls, etf_df, commission=0.001, **kwargs)
        bt_results[key] = {"label": label, "cum_return": r["cum_return"]}
        print(f"  {label}: 累计收益 {r['cum_return']:.2%}")

    output = {
        "our_engine": results,
        "backtrader_510300": bt_results,
        "config": {
            "backtest": BACKTEST_CFG_WEEKLY,
            "period": f"{START_DATE} ~ {END_DATE}",
            "universe_size": len(universe),
            "panel_symbols": int(panel["symbol"].nunique()),
        },
    }
    output_path = OUTPUT_DIR / "strategy_comparison.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存到 {output_path}")


if __name__ == "__main__":
    main()
