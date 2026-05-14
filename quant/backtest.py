from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from quant.indicators import add_basic_indicators


def build_backtest_panel(
    universe: pd.DataFrame,
    daily_dir: Path,
    min_history_days_stock: int,
    min_history_days_etf: int,
) -> pd.DataFrame:
    frames = []
    for item in universe.itertuples(index=False):
        symbol = str(item.symbol).zfill(6)
        path = daily_dir / f"{symbol}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        except (EmptyDataError, ParserError):
            continue
        asset_type = getattr(item, "asset_type", "stock")
        min_history = min_history_days_etf if asset_type == "etf" else min_history_days_stock
        if len(df) < min_history or "date" not in df.columns:
            continue
        df["date"] = pd.to_datetime(df["date"])
        df = add_basic_indicators(df)
        df["fwd_ret_1"] = df["close"].shift(-1) / df["close"] - 1
        df["symbol"] = symbol
        df["name"] = getattr(item, "name", "")
        df["asset_type"] = asset_type
        df["market_group"] = getattr(item, "market_group", "")
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, ignore_index=True).dropna(subset=["ret_20", "ret_60", "avg_amount_20", "fwd_ret_1"])
    return panel


def _is_rebalance_day(current_date, prev_rebalance_date, frequency: str, next_date=None) -> bool:
    if frequency == "daily":
        return True
    if prev_rebalance_date is None:
        return True
    if frequency == "weekly":
        return current_date.weekday() < prev_rebalance_date.weekday() or (current_date - prev_rebalance_date).days >= 3
    if frequency == "monthly":
        return current_date.month != prev_rebalance_date.month or current_date.year != prev_rebalance_date.year
    if frequency == "month_end":
        if next_date is None:
            return True
        return current_date.month != next_date.month or current_date.year != next_date.year
    return True


def run_cross_sectional_backtest_from_panel(
    panel: pd.DataFrame,
    strategy,
    backtest_cfg: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if panel.empty:
        return pd.DataFrame(), pd.DataFrame()

    rebalance_frequency = str(backtest_cfg.get("rebalance_frequency", "weekly"))
    commission_rate = float(backtest_cfg.get("commission_rate", 0.001))
    slippage_rate = float(backtest_cfg.get("slippage_rate", 0.0005))
    stamp_duty_rate = float(backtest_cfg.get("stamp_duty_rate", 0.001))

    portfolio_returns = []
    selections = []
    current_symbols: set[str] = set()
    prev_rebalance_date = None
    day_turnover = 0.0

    daily_groups = list(panel.groupby("date"))
    for index, (trade_date, day_df) in enumerate(daily_groups):
        next_date = daily_groups[index + 1][0] if index + 1 < len(daily_groups) else None
        rebalance = _is_rebalance_day(trade_date, prev_rebalance_date, rebalance_frequency, next_date=next_date)
        cost = 0.0
        day_turnover = 0.0

        if rebalance:
            ranked = strategy.rank(day_df)
            if ranked.empty:
                new_symbols = set()
                old_symbols = current_symbols
                if old_symbols:
                    sell_fraction = 1.0
                    cost = sell_fraction * (commission_rate + slippage_rate + stamp_duty_rate)
                    day_turnover = sell_fraction
                    current_symbols = new_symbols
                    prev_rebalance_date = trade_date
            else:
                new_symbols = set(ranked["symbol"].astype(str))
                old_symbols = current_symbols

                if not old_symbols:
                    buy_fraction = 1.0
                    sell_fraction = 0.0
                else:
                    buy_fraction = len(new_symbols - old_symbols) / max(len(new_symbols), 1)
                    sell_fraction = len(old_symbols - new_symbols) / max(len(old_symbols), 1)

                cost = buy_fraction * (commission_rate + slippage_rate) + sell_fraction * (
                    commission_rate + slippage_rate + stamp_duty_rate
                )
                day_turnover = buy_fraction + sell_fraction
                current_symbols = new_symbols
                prev_rebalance_date = trade_date

                ranked = ranked.copy()
                ranked["date"] = trade_date
                selection_cols = [col for col in ["date", "symbol", "name", "score", "close", "reason"] if col in ranked.columns]
                selections.append(ranked[selection_cols])

        if not current_symbols:
            portfolio_returns.append(
                {
                    "date": trade_date,
                    "return": -cost,
                    "gross_return": 0.0,
                    "cost": cost,
                    "turnover": day_turnover,
                    "positions": 0,
                    "rebalanced": rebalance,
                }
            )
            continue

        held = day_df[day_df["symbol"].isin(current_symbols)]
        if held.empty:
            continue

        gross_return = held["fwd_ret_1"].mean()
        net_return = gross_return - cost

        portfolio_returns.append(
            {
                "date": trade_date,
                "return": net_return,
                "gross_return": gross_return,
                "cost": cost,
                "turnover": day_turnover,
                "positions": len(current_symbols),
                "rebalanced": rebalance,
            }
        )

    returns_df = pd.DataFrame(portfolio_returns).sort_values("date")
    if returns_df.empty:
        return pd.DataFrame(), pd.DataFrame()
    initial_capital = float(backtest_cfg.get("initial_capital", 1_000_000))
    returns_df["equity"] = initial_capital * (1 + returns_df["return"]).cumprod()
    returns_df["cum_return"] = returns_df["equity"] / initial_capital - 1
    picks_df = pd.concat(selections, ignore_index=True) if selections else pd.DataFrame()
    return returns_df, picks_df


def run_cross_sectional_backtest(
    universe: pd.DataFrame,
    daily_dir: Path,
    strategy,
    backtest_cfg: dict,
    min_history_days_stock: int,
    min_history_days_etf: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = build_backtest_panel(
        universe=universe,
        daily_dir=daily_dir,
        min_history_days_stock=min_history_days_stock,
        min_history_days_etf=min_history_days_etf,
    )
    return run_cross_sectional_backtest_from_panel(panel=panel, strategy=strategy, backtest_cfg=backtest_cfg)


def summarize_backtest(returns_df: pd.DataFrame, initial_capital: float) -> pd.DataFrame:
    if returns_df.empty:
        return pd.DataFrame()

    frame = returns_df.copy().sort_values("date").reset_index(drop=True)
    total_days = len(frame)
    trading_years = total_days / 252 if total_days else 0.0
    ending_equity = float(frame["equity"].iloc[-1])
    cumulative_return = ending_equity / initial_capital - 1
    annual_return = (ending_equity / initial_capital) ** (1 / trading_years) - 1 if trading_years > 0 and ending_equity > 0 else -1.0
    annual_volatility = float(frame["return"].std(ddof=0) * (252**0.5)) if total_days > 1 else 0.0
    sharpe = annual_return / annual_volatility if annual_volatility > 0 else 0.0
    equity_curve = frame["equity"]
    drawdown = equity_curve / equity_curve.cummax() - 1
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0
    win_rate = float((frame["return"] > 0).mean()) if total_days else 0.0
    avg_turnover = float(frame["turnover"].mean()) if "turnover" in frame.columns else 0.0
    avg_positions = float(frame["positions"].mean()) if "positions" in frame.columns else 0.0

    return pd.DataFrame(
        [
            {
                "start_date": frame["date"].iloc[0],
                "end_date": frame["date"].iloc[-1],
                "trading_days": total_days,
                "ending_equity": ending_equity,
                "cum_return": cumulative_return,
                "annual_return": annual_return,
                "annual_volatility": annual_volatility,
                "sharpe": sharpe,
                "max_drawdown": max_drawdown,
                "win_rate": win_rate,
                "avg_turnover": avg_turnover,
                "avg_positions": avg_positions,
            }
        ]
    )
