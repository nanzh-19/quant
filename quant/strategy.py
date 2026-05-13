from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd


@dataclass
class StrategySignal:
    symbol: str
    name: str
    score: float
    close: float
    ret_20: float
    ret_60: float
    avg_amount_20: float
    action: str
    reason: str


class BaseStrategy(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def params(self) -> dict: ...

    @abstractmethod
    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        """对截面数据打分排序，返回选中的标的（行数 <= max_positions）。"""
        ...

    def review_holdings(self, holdings: pd.DataFrame, ranking: pd.DataFrame) -> pd.DataFrame:
        if holdings.empty:
            return pd.DataFrame(columns=["symbol", "action", "reason"])
        top_symbols = set(ranking["symbol"].astype(str))
        rows = []
        for row in holdings.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            if symbol in top_symbols:
                rows.append({"symbol": symbol, "action": "HOLD", "reason": "仍在策略候选前列"})
            else:
                rows.append({"symbol": symbol, "action": "REVIEW_SELL", "reason": "已不在当前候选池前列"})
        return pd.DataFrame(rows)


class MomentumStrategy(BaseStrategy):
    def __init__(self, strategy_cfg: dict) -> None:
        self.max_positions = int(strategy_cfg.get("max_positions", 10))
        self.eligible_asset_types = set(strategy_cfg.get("eligible_asset_types", ["stock", "etf"]))
        self.min_price_stock = float(strategy_cfg.get("min_price_stock", 5.0))
        self.min_price_etf = float(strategy_cfg.get("min_price_etf", 0.5))
        self.min_avg_turnover_million_stock = float(strategy_cfg.get("min_avg_turnover_million_stock", 80))
        self.min_avg_turnover_million_etf = float(strategy_cfg.get("min_avg_turnover_million_etf", 30))
        self.min_ret_20 = float(strategy_cfg.get("min_ret_20", 0.0))
        self.min_ret_60 = float(strategy_cfg.get("min_ret_60", 0.0))
        self.min_ret_120 = float(strategy_cfg.get("min_ret_120", 0.1))
        self.max_volatility_20 = float(strategy_cfg.get("max_volatility_20", 0.28))
        self.min_drawdown_20 = float(strategy_cfg.get("min_drawdown_20", -0.12))
        self.require_ma_trend = bool(strategy_cfg.get("require_ma_trend", True))
        self.weight_ret_120 = float(strategy_cfg.get("weight_ret_120", 0.45))
        self.weight_ret_60 = float(strategy_cfg.get("weight_ret_60", 0.3))
        self.weight_ret_20 = float(strategy_cfg.get("weight_ret_20", 0.1))
        self.weight_ma_gap_20_60 = float(strategy_cfg.get("weight_ma_gap_20_60", 0.1))
        self.weight_low_volatility = float(strategy_cfg.get("weight_low_volatility", 0.05))

    @property
    def name(self) -> str:
        return "momentum"

    def params(self) -> dict:
        return {
            "max_positions": self.max_positions,
            "min_price_stock": self.min_price_stock,
            "min_price_etf": self.min_price_etf,
            "min_avg_turnover_million_stock": self.min_avg_turnover_million_stock,
            "min_avg_turnover_million_etf": self.min_avg_turnover_million_etf,
            "min_ret_20": self.min_ret_20,
            "min_ret_60": self.min_ret_60,
            "min_ret_120": self.min_ret_120,
            "max_volatility_20": self.max_volatility_20,
            "min_drawdown_20": self.min_drawdown_20,
            "require_ma_trend": self.require_ma_trend,
            "weight_ret_120": self.weight_ret_120,
            "weight_ret_60": self.weight_ret_60,
            "weight_ret_20": self.weight_ret_20,
            "weight_ma_gap_20_60": self.weight_ma_gap_20_60,
            "weight_low_volatility": self.weight_low_volatility,
        }

    def rank(self, latest_frame: pd.DataFrame) -> pd.DataFrame:
        if latest_frame.empty:
            return latest_frame
        df = latest_frame.copy()
        if "asset_type" not in df.columns:
            df["asset_type"] = "stock"
        for column in ["ret_120", "ma_20", "ma_60", "volatility_20", "drawdown_20", "ma_gap_20_60"]:
            if column not in df.columns:
                df[column] = pd.NA
        df = df[df["asset_type"].isin(self.eligible_asset_types)]
        stock_mask = df["asset_type"] == "stock"
        etf_mask = df["asset_type"] == "etf"
        df = df[
            (stock_mask & (df["close"] >= self.min_price_stock) & (df["avg_amount_20"] >= self.min_avg_turnover_million_stock * 1_000_000))
            | (etf_mask & (df["close"] >= self.min_price_etf) & (df["avg_amount_20"] >= self.min_avg_turnover_million_etf * 1_000_000))
        ]
        df = df[
            (df["ret_20"] >= self.min_ret_20)
            & (df["ret_60"] >= self.min_ret_60)
            & (df["ret_120"] >= self.min_ret_120)
            & (df["drawdown_20"] >= self.min_drawdown_20)
        ]
        if self.max_volatility_20 > 0:
            df = df[df["volatility_20"] <= self.max_volatility_20]
        if self.require_ma_trend:
            df = df[(df["close"] >= df["ma_20"]) & (df["ma_20"] >= df["ma_60"])]
        df["score"] = (
            df["ret_120"] * self.weight_ret_120
            + df["ret_60"] * self.weight_ret_60
            + df["ret_20"] * self.weight_ret_20
            + df["ma_gap_20_60"] * self.weight_ma_gap_20_60
            + (-df["volatility_20"]) * self.weight_low_volatility
        )
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class BuyAndHoldStrategy(BaseStrategy):
    """买入指定标的并一直持有，用于回测引擎验证。"""

    def __init__(self, strategy_cfg: dict) -> None:
        self.symbols = [str(s).zfill(6) for s in strategy_cfg.get("symbols", [])]

    @property
    def name(self) -> str:
        return "buy_and_hold"

    def params(self) -> dict:
        return {"symbols": self.symbols}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty or not self.symbols:
            return snapshot.head(0)
        df = snapshot[snapshot["symbol"].astype(str).str.zfill(6).isin(self.symbols)].copy()
        df["score"] = 1.0
        return df.reset_index(drop=True)


def _liquidity_filter(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """通用流动性过滤：价格 + 成交额门槛。"""
    if df.empty:
        return df
    min_price_stock = float(cfg.get("min_price_stock", 5.0))
    min_price_etf = float(cfg.get("min_price_etf", 0.5))
    min_turnover_stock = float(cfg.get("min_avg_turnover_million_stock", 80)) * 1_000_000
    min_turnover_etf = float(cfg.get("min_avg_turnover_million_etf", 30)) * 1_000_000
    if "asset_type" not in df.columns:
        df["asset_type"] = "stock"
    stock = df["asset_type"] == "stock"
    etf = df["asset_type"] == "etf"
    return df[
        (stock & (df["close"] >= min_price_stock) & (df["avg_amount_20"] >= min_turnover_stock))
        | (etf & (df["close"] >= min_price_etf) & (df["avg_amount_20"] >= min_turnover_etf))
    ]


class MeanReversionStrategy(BaseStrategy):
    """均值回归策略：选近 20 日超跌但 60 日趋势尚可的标的，赌短期反弹。"""

    def __init__(self, strategy_cfg: dict) -> None:
        self.max_positions = int(strategy_cfg.get("max_positions", 10))
        self.max_ret_20 = float(strategy_cfg.get("max_ret_20", -0.05))
        self.min_ret_60 = float(strategy_cfg.get("min_ret_60", -0.10))
        self.max_volatility_20 = float(strategy_cfg.get("max_volatility_20", 0.35))
        self._cfg = strategy_cfg

    @property
    def name(self) -> str:
        return "mean_reversion"

    def params(self) -> dict:
        return {"max_positions": self.max_positions, "max_ret_20": self.max_ret_20, "min_ret_60": self.min_ret_60}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        df = _liquidity_filter(df, self._cfg)
        if df.empty:
            return df
        df = df[
            (df["ret_20"] <= self.max_ret_20)
            & (df["ret_60"] >= self.min_ret_60)
            & (df["volatility_20"] <= self.max_volatility_20)
        ]
        df["score"] = -df["ret_20"]
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class LowVolatilityStrategy(BaseStrategy):
    """低波动策略：选波动率最低的标的，利用低波异象获取超额收益。"""

    def __init__(self, strategy_cfg: dict) -> None:
        self.max_positions = int(strategy_cfg.get("max_positions", 20))
        self.min_ret_60 = float(strategy_cfg.get("min_ret_60", -0.05))
        self._cfg = strategy_cfg

    @property
    def name(self) -> str:
        return "low_volatility"

    def params(self) -> dict:
        return {"max_positions": self.max_positions, "min_ret_60": self.min_ret_60}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        df = _liquidity_filter(df, self._cfg)
        if df.empty:
            return df
        df = df[(df["ret_60"] >= self.min_ret_60) & (df["volatility_20"] > 0)]
        df["score"] = -df["volatility_20"]
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class DualMAStrategy(BaseStrategy):
    """双均线趋势跟踪：MA20 > MA60 且价格在 MA20 之上时买入，按 ma_gap 排序。"""

    def __init__(self, strategy_cfg: dict) -> None:
        self.max_positions = int(strategy_cfg.get("max_positions", 10))
        self.min_ret_120 = float(strategy_cfg.get("min_ret_120", 0.0))
        self._cfg = strategy_cfg

    @property
    def name(self) -> str:
        return "dual_ma"

    def params(self) -> dict:
        return {"max_positions": self.max_positions, "min_ret_120": self.min_ret_120}

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        for col in ["ma_20", "ma_60", "ma_gap_20_60", "ret_120"]:
            if col not in df.columns:
                df[col] = pd.NA
        df = _liquidity_filter(df, self._cfg)
        if df.empty:
            return df
        df = df[
            (df["close"] >= df["ma_20"])
            & (df["ma_20"] >= df["ma_60"])
            & (df["ret_120"] >= self.min_ret_120)
            & (df["ma_gap_20_60"] > 0)
        ]
        df["score"] = df["ma_gap_20_60"]
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "momentum": MomentumStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
    "mean_reversion": MeanReversionStrategy,
    "low_volatility": LowVolatilityStrategy,
    "dual_ma": DualMAStrategy,
}


def create_strategy(strategy_cfg: dict) -> BaseStrategy:
    name = str(strategy_cfg.get("name", "momentum"))
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGY_REGISTRY.keys())}")
    return cls(strategy_cfg)
