#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


SOURCE_URL = "https://www.investor.org.cn/xxzx/tjzl/tjnrgmjytx/bk/kj/jyxl_3783/202311/P020231116582854616218.pdf"
SYMBOL = "510300"
START_DATE = "2018-12-31"
END_DATE = "2021-06-30"
PUBLIC_BUY_HOLD_RETURN = 0.7985
PUBLIC_MA60_RETURN = 1.5676


def _load_data() -> pd.DataFrame:
    path = PROJECT_ROOT / "data" / "daily" / f"{SYMBOL}.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df = df[(df["date"] >= START_DATE) & (df["date"] <= END_DATE)].sort_values("date").reset_index(drop=True)
    if df.empty:
        raise RuntimeError(f"no local data for {SYMBOL} in {START_DATE} ~ {END_DATE}")
    return df


def _strategy_return(df: pd.DataFrame, execution: str) -> tuple[float, float, int]:
    frame = df.copy()
    frame["ma60"] = frame["close"].rolling(60).mean()
    frame["ret_1"] = frame["close"].pct_change()
    signal = (frame["close"] > frame["ma60"]).astype(int)
    if execution == "same_close":
        position = signal
    elif execution == "next_close":
        position = signal.shift(1).fillna(0)
    else:
        raise ValueError(f"unsupported execution: {execution}")
    strategy_returns = (position * frame["ret_1"]).dropna()
    return float((1.0 + strategy_returns).prod() - 1.0), float(position.mean()), int((position.diff().abs() > 0).sum())


def main() -> None:
    df = _load_data()
    buy_hold_return = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1.0)
    next_close_return, next_close_exposure, next_close_trades = _strategy_return(df, "next_close")
    same_close_return, same_close_exposure, same_close_trades = _strategy_return(df, "same_close")

    rows = [
        {
            "case": "public_target_buy_hold",
            "return": PUBLIC_BUY_HOLD_RETURN,
            "target_return": PUBLIC_BUY_HOLD_RETURN,
            "diff_vs_target": 0.0,
            "execution": "public_reported",
            "exposure": None,
            "position_changes": None,
        },
        {
            "case": "local_buy_hold",
            "return": buy_hold_return,
            "target_return": PUBLIC_BUY_HOLD_RETURN,
            "diff_vs_target": buy_hold_return - PUBLIC_BUY_HOLD_RETURN,
            "execution": "close_to_close",
            "exposure": 1.0,
            "position_changes": 0,
        },
        {
            "case": "public_target_ma60",
            "return": PUBLIC_MA60_RETURN,
            "target_return": PUBLIC_MA60_RETURN,
            "diff_vs_target": 0.0,
            "execution": "public_reported",
            "exposure": None,
            "position_changes": None,
        },
        {
            "case": "local_ma60_next_close",
            "return": next_close_return,
            "target_return": PUBLIC_MA60_RETURN,
            "diff_vs_target": next_close_return - PUBLIC_MA60_RETURN,
            "execution": "signal_at_close_trade_next_close",
            "exposure": next_close_exposure,
            "position_changes": next_close_trades,
        },
        {
            "case": "local_ma60_same_close_lookahead",
            "return": same_close_return,
            "target_return": PUBLIC_MA60_RETURN,
            "diff_vs_target": same_close_return - PUBLIC_MA60_RETURN,
            "execution": "signal_at_close_trade_same_close",
            "exposure": same_close_exposure,
            "position_changes": same_close_trades,
        },
    ]
    result = pd.DataFrame(rows)
    out_path = PROJECT_ROOT / "outputs" / "replication_510300_ma60_public.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)

    notes_path = PROJECT_ROOT / "outputs" / "replication_510300_ma60_public.md"
    notes = [
        "# 510300 MA60 Public Replication",
        "",
        f"- source: {SOURCE_URL}",
        f"- symbol: {SYMBOL}",
        f"- local data period: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}",
        "- local adjustment: configured qfq daily close",
        f"- public buy-hold target: {PUBLIC_BUY_HOLD_RETURN:.2%}",
        f"- local buy-hold: {buy_hold_return:.2%}",
        f"- public MA60 target: {PUBLIC_MA60_RETURN:.2%}",
        f"- local MA60 next-close execution: {next_close_return:.2%}",
        f"- local MA60 same-close execution: {same_close_return:.2%}",
        "",
        "This public target is not currently matched. The buy-hold gap suggests a data/adjustment or exact date range difference before strategy timing is considered.",
    ]
    notes_path.write_text("\n".join(notes), encoding="utf-8")

    print(result.to_string(index=False))
    print(f"saved={out_path}")
    print(f"notes={notes_path}")


if __name__ == "__main__":
    main()
