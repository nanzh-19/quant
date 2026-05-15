from __future__ import annotations

from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle


def render_candlestick_chart(
    df: pd.DataFrame,
    symbol: str,
    output_path: Path,
    title: str | None = None,
    days: int = 120,
) -> Path:
    if df.empty:
        raise RuntimeError(f"{symbol} 没有可用日线数据")

    frame = df.copy().sort_values("date").tail(days)
    frame["date"] = pd.to_datetime(frame["date"])
    frame["ma20"] = frame["close"].rolling(20).mean()
    frame["ma60"] = frame["close"].rolling(60).mean()
    frame["date_num"] = mdates.date2num(frame["date"])

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax_price, ax_vol) = plt.subplots(
        2,
        1,
        figsize=(14, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [3, 1]},
    )

    width = 0.6
    for row in frame.itertuples(index=False):
        color = "#d84a4a" if row.close >= row.open else "#2f8f5b"
        ax_price.vlines(row.date_num, row.low, row.high, color=color, linewidth=1)
        body_low = min(row.open, row.close)
        body_height = max(abs(row.close - row.open), 0.001)
        ax_price.add_patch(
            Rectangle(
                (row.date_num - width / 2, body_low),
                width,
                body_height,
                facecolor=color,
                edgecolor=color,
                linewidth=0.8,
            )
        )

    ax_price.plot(frame["date_num"], frame["ma20"], color="#1f77b4", linewidth=1.2, label="MA20")
    ax_price.plot(frame["date_num"], frame["ma60"], color="#ff7f0e", linewidth=1.2, label="MA60")
    ax_price.set_title(title or f"{symbol} Candlestick")
    ax_price.set_ylabel("Price")
    ax_price.grid(True, linestyle="--", alpha=0.25)
    ax_price.legend(loc="upper left")

    vol_colors = ["#d84a4a" if c >= o else "#2f8f5b" for o, c in zip(frame["open"], frame["close"])]
    ax_vol.bar(frame["date_num"], frame["volume"], color=vol_colors, width=0.8, alpha=0.8)
    ax_vol.set_ylabel("Volume")
    ax_vol.grid(True, linestyle="--", alpha=0.25)

    ax_vol.xaxis_date()
    ax_vol.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate(rotation=30)
    fig.tight_layout()
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output_path
