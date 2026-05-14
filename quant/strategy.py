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
        df["reason"] = (
            "trend_ok;ret20="
            + df["ret_20"].round(4).astype(str)
            + ";ret60="
            + df["ret_60"].round(4).astype(str)
            + ";ret120="
            + df["ret_120"].round(4).astype(str)
            + ";ma_gap="
            + df["ma_gap_20_60"].round(4).astype(str)
            + ";vol20="
            + df["volatility_20"].round(4).astype(str)
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
        df["reason"] = "mean_reversion;ret20=" + df["ret_20"].round(4).astype(str)
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
        df["reason"] = "low_volatility;vol20=" + df["volatility_20"].round(4).astype(str)
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
        df["reason"] = "dual_ma;ma_gap=" + df["ma_gap_20_60"].round(4).astype(str)
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class ETFRegressionMomentumStrategy(BaseStrategy):
    """ETF 轮动：用近 25 日对数价格回归年化收益 * R2 作为动量得分。"""

    def __init__(self, strategy_cfg: dict) -> None:
        self.symbols = [str(s).zfill(6) for s in strategy_cfg.get("symbols", ["518880", "513100", "159915", "510300"])]
        self.max_positions = int(strategy_cfg.get("max_positions", 1))
        self.min_score = float(strategy_cfg.get("min_score", 0.0))
        self.score_column = str(strategy_cfg.get("score_column", "reg_momentum_25"))

    @property
    def name(self) -> str:
        return "etf_regression_momentum"

    def params(self) -> dict:
        return {
            "symbols": self.symbols,
            "max_positions": self.max_positions,
            "min_score": self.min_score,
            "score_column": self.score_column,
        }

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        if self.score_column not in df.columns:
            df[self.score_column] = pd.NA
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df = df[df["symbol"].isin(self.symbols)].copy()
        df = df[pd.to_numeric(df[self.score_column], errors="coerce").notna()]
        df["score"] = pd.to_numeric(df[self.score_column], errors="coerce")
        df = df[df["score"] > self.min_score]
        df["reason"] = "etf_regression_momentum;score=" + df["score"].round(4).astype(str)
        return df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class ETFDualMomentumRotationStrategy(BaseStrategy):
    """Public ETF rotation sample: 60-day relative momentum with a 200-day trend filter."""

    def __init__(self, strategy_cfg: dict) -> None:
        self.symbols = [str(s).zfill(6) for s in strategy_cfg.get("symbols", ["510300", "510500", "518880", "159934", "513100", "513500", "511380", "511010"])]
        self.max_positions = int(strategy_cfg.get("max_positions", 2))
        self.momentum_column = str(strategy_cfg.get("momentum_column", "ret_60"))
        self.trend_column = str(strategy_cfg.get("trend_column", "ma_200"))
        self.fallback_symbol = str(strategy_cfg.get("fallback_symbol", "")).zfill(6) if strategy_cfg.get("fallback_symbol") else ""

    @property
    def name(self) -> str:
        return "etf_dual_momentum_rotation"

    def params(self) -> dict:
        return {
            "symbols": self.symbols,
            "max_positions": self.max_positions,
            "momentum_column": self.momentum_column,
            "trend_column": self.trend_column,
            "fallback_symbol": self.fallback_symbol,
        }

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        for column in [self.momentum_column, self.trend_column]:
            if column not in df.columns:
                df[column] = pd.NA
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df = df[df["symbol"].isin(self.symbols)].copy()
        df[self.momentum_column] = pd.to_numeric(df[self.momentum_column], errors="coerce")
        df[self.trend_column] = pd.to_numeric(df[self.trend_column], errors="coerce")
        df = df.dropna(subset=[self.momentum_column, self.trend_column, "close"])
        eligible = df[(df["close"] > df[self.trend_column]) & (df[self.momentum_column] > 0)].copy()

        if eligible.empty and self.fallback_symbol:
            fallback = df[df["symbol"] == self.fallback_symbol].copy()
            if not fallback.empty:
                fallback["score"] = 0.0
                fallback["reason"] = "fallback_cash_proxy"
                return fallback.head(1).reset_index(drop=True)
            return df.head(0)
        if eligible.empty:
            return df.head(0)

        eligible["score"] = eligible[self.momentum_column]
        eligible["reason"] = (
            "etf_dual_momentum;momentum="
            + eligible[self.momentum_column].round(4).astype(str)
            + ";close_gt_"
            + self.trend_column
        )
        return eligible.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


class ETFWeightedSlopeRotationStrategy(BaseStrategy):
    """Cifang-style ETF rotation with weighted 25-day log slope, stop-profit, and cooldown."""

    def __init__(self, strategy_cfg: dict) -> None:
        self.symbols = [str(s).zfill(6) for s in strategy_cfg.get("symbols", ["159915", "159941", "513030", "513520", "159985", "518880"])]
        self.max_positions = int(strategy_cfg.get("max_positions", 1))
        self.score_column = str(strategy_cfg.get("score_column", "weighted_slope_25"))
        self.min_score = float(strategy_cfg.get("min_score", 0.0))
        self.max_score = float(strategy_cfg.get("max_score", 5.0))
        self.stop_profit_drawdown = float(strategy_cfg.get("stop_profit_drawdown", 0.05))
        self.cooldown_days = int(strategy_cfg.get("cooldown_days", 5))
        self._active_symbol: str = ""
        self._active_high: float = 0.0
        self._cooldown_remaining: int = 0

    @property
    def name(self) -> str:
        return "etf_weighted_slope_rotation"

    def params(self) -> dict:
        return {
            "symbols": self.symbols,
            "max_positions": self.max_positions,
            "score_column": self.score_column,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "stop_profit_drawdown": self.stop_profit_drawdown,
            "cooldown_days": self.cooldown_days,
        }

    def _reset_state(self) -> None:
        self._active_symbol = ""
        self._active_high = 0.0
        self._cooldown_remaining = 0

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        if "date" not in df.columns:
            return df.head(0)
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df = df[df["symbol"].isin(self.symbols)].copy()
        if df.empty:
            return df

        score_col = self.score_column
        if score_col not in df.columns:
            df[score_col] = pd.NA
        df[score_col] = pd.to_numeric(df[score_col], errors="coerce")
        df = df.dropna(subset=[score_col, "close"])
        if df.empty:
            return df

        trade_date = pd.to_datetime(df["date"].iloc[0]).date()

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            if self._cooldown_remaining == 0:
                self._reset_state()
            return df.head(0)

        if self._active_symbol:
            active_row = df[df["symbol"] == self._active_symbol]
            if not active_row.empty:
                close = float(active_row["close"].iloc[0])
                self._active_high = max(self._active_high, close)
                if self._active_high > 0 and close <= self._active_high * (1.0 - self.stop_profit_drawdown):
                    self._cooldown_remaining = self.cooldown_days
                    self._reset_state()
                    self._cooldown_remaining = self.cooldown_days
                    return df.head(0)

        df["score"] = df[score_col]
        df = df[(df["score"] >= self.min_score) & (df["score"] <= self.max_score)]
        if df.empty:
            self._reset_state()
            return df

        selected = df.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)
        chosen = str(selected["symbol"].iloc[0]) if not selected.empty else ""
        if chosen and chosen != self._active_symbol:
            self._active_symbol = chosen
            self._active_high = float(selected["close"].iloc[0])
        elif chosen:
            self._active_high = max(self._active_high, float(selected["close"].iloc[0]))
        selected["reason"] = (
            "weighted_slope;score="
            + selected["score"].round(4).astype(str)
            + ";cooldown="
            + str(self.cooldown_days)
            + ";date="
            + str(trade_date)
        )
        return selected


class ETFRSRSRotationStrategy(BaseStrategy):
    """RSRS ETF rotation with 18-day high/low slope and 600-day z-score hysteresis."""

    def __init__(self, strategy_cfg: dict) -> None:
        self.symbols = [str(s).zfill(6) for s in strategy_cfg.get("symbols", ["518880", "513100"])]
        self.score_column = str(strategy_cfg.get("score_column", "rsrs_z_18_600"))
        self.max_positions = int(strategy_cfg.get("max_positions", len(self.symbols)))
        self._held_symbols: set[str] = set()

    @property
    def name(self) -> str:
        return "etf_rsrs_rotation"

    def params(self) -> dict:
        return {
            "symbols": self.symbols,
            "score_column": self.score_column,
            "max_positions": self.max_positions,
        }

    def rank(self, snapshot: pd.DataFrame) -> pd.DataFrame:
        if snapshot.empty:
            return snapshot
        df = snapshot.copy()
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df = df[df["symbol"].isin(self.symbols)].copy()
        if df.empty:
            return df
        if self.score_column not in df.columns:
            df[self.score_column] = pd.NA
        df[self.score_column] = pd.to_numeric(df[self.score_column], errors="coerce")
        df = df.dropna(subset=[self.score_column])
        if df.empty:
            return df

        for row in df.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            z_score = float(getattr(row, self.score_column))
            buy_signal = (0.0 < z_score < 2.0) or (z_score < -2.0)
            sell_signal = (-2.0 < z_score < -1.0) or (z_score > 3.0)
            if symbol in self._held_symbols and sell_signal:
                self._held_symbols.remove(symbol)
            elif symbol not in self._held_symbols and buy_signal:
                self._held_symbols.add(symbol)

        selected = df[df["symbol"].isin(self._held_symbols)].copy()
        if selected.empty:
            return selected
        selected["score"] = selected[self.score_column]
        selected["reason"] = "rsrs;z=" + selected["score"].round(4).astype(str)
        return selected.sort_values("score", ascending=False).head(self.max_positions).reset_index(drop=True)


STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    "momentum": MomentumStrategy,
    "buy_and_hold": BuyAndHoldStrategy,
    "mean_reversion": MeanReversionStrategy,
    "low_volatility": LowVolatilityStrategy,
    "dual_ma": DualMAStrategy,
    "etf_regression_momentum": ETFRegressionMomentumStrategy,
    "etf_dual_momentum_rotation": ETFDualMomentumRotationStrategy,
    "etf_weighted_slope_rotation": ETFWeightedSlopeRotationStrategy,
    "etf_rsrs_rotation": ETFRSRSRotationStrategy,
}


def create_strategy(strategy_cfg: dict) -> BaseStrategy:
    name = str(strategy_cfg.get("name", "momentum"))
    cls = STRATEGY_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知策略: {name}，可选: {list(STRATEGY_REGISTRY.keys())}")
    return cls(strategy_cfg)
