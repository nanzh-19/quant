from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import product
from collections import Counter

import pandas as pd

from quant.backtest import build_backtest_panel, run_cross_sectional_backtest, run_cross_sectional_backtest_from_panel, summarize_backtest
from quant.charting import render_candlestick_chart
from quant.config import AppConfig
from quant.data_provider import EastMoneyDataProvider
from quant.inventory import build_data_inventory
from quant.reporting import build_backtest_diagnostics
from quant.research import build_latest_snapshot
from quant.storage import Storage
from quant.strategy import BaseStrategy, MomentumStrategy, create_strategy


@dataclass
class AppContext:
    config: AppConfig
    storage: Storage
    provider: EastMoneyDataProvider
    strategy: BaseStrategy


def _infer_market(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("600", "601", "603", "605", "688", "510", "511", "512", "513", "515", "516", "517", "518", "519", "520", "560", "561", "562", "563", "588")):
        return "SH"
    return "SZ"


def _infer_asset_type(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("510", "511", "512", "513", "515", "516", "517", "518", "519", "520", "560", "561", "562", "563", "588", "159")):
        return "etf"
    return "stock"


def _load_merged_universe(ctx: AppContext) -> pd.DataFrame:
    local_universe = ctx.storage.read_universe()
    try:
        live_universe = ctx.provider.fetch_universe()
    except Exception:
        live_universe = pd.DataFrame()
    frames = [df for df in [live_universe, local_universe] if not df.empty]
    if not frames:
        raise RuntimeError("没有可用 universe 数据")
    universe = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["symbol"], keep="first")
    if "symbol" in universe.columns:
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    if "asset_type" not in universe.columns:
        universe["asset_type"] = universe["symbol"].map(_infer_asset_type)
    if "market" not in universe.columns:
        universe["market"] = universe["symbol"].map(_infer_market)
    ctx.storage.write_universe(universe)
    return universe


def _validate_daily_frame(df: pd.DataFrame, symbol: str, min_rows: int = 5) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date", "open", "close", "high", "low"]).sort_values("date").reset_index(drop=True)
    if len(out) < min_rows:
        return out
    volumes = pd.to_numeric(out["volume"], errors="coerce")
    amounts = pd.to_numeric(out["amount"], errors="coerce")
    vol_ratio = volumes / volumes.shift(1).replace(0, pd.NA)
    amt_ratio = amounts / amounts.shift(1).replace(0, pd.NA)
    suspicious = (
        (vol_ratio < 0.05)
        & (amt_ratio > 0.4)
        & (amt_ratio < 2.5)
        & volumes.shift(1).notna()
        & amounts.shift(1).notna()
    )
    if suspicious.any():
        bad_row = out.loc[suspicious].iloc[0]
        raise RuntimeError(f"suspicious volume-unit shift detected for {symbol} at {bad_row['date'].strftime('%Y-%m-%d')}")
    return out


def _fetch_stock_history_via_akshare(symbol: str, market: str, start_date: date) -> pd.DataFrame:
    import akshare as ak

    ak_symbol = f"{market.lower()}{symbol}"
    raw = ak.stock_zh_a_daily(symbol=ak_symbol, adjust="qfq")
    if raw.empty:
        return pd.DataFrame()

    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df[df["date"] >= pd.Timestamp(start_date)].copy()
    if df.empty:
        return pd.DataFrame()

    prev_close = df["close"].shift(1)
    df["chg"] = (df["close"] - prev_close).fillna(0.0)
    df["pct_chg"] = ((df["close"] / prev_close - 1.0) * 100.0).replace([pd.NA, pd.NaT, float("inf"), float("-inf")], pd.NA).fillna(0.0)
    df["amplitude"] = (((df["high"] - df["low"]) / prev_close) * 100.0).replace([pd.NA, pd.NaT, float("inf"), float("-inf")], pd.NA).fillna(0.0)
    df["symbol"] = symbol
    df["market"] = market
    return df[["date", "open", "close", "high", "low", "volume", "amount", "amplitude", "pct_chg", "chg", "turnover", "symbol", "market"]]


def _fetch_etf_history_via_akshare(symbol: str, market: str, start_date: date) -> pd.DataFrame:
    import akshare as ak

    ak_symbol = f"{market.lower()}{symbol}"
    raw = ak.fund_etf_hist_sina(symbol=ak_symbol)
    if raw.empty:
        return pd.DataFrame()
    df = raw.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = df[df["date"] >= pd.Timestamp(start_date)].copy()
    if df.empty:
        return pd.DataFrame()
    prev_close = df["close"].shift(1)
    df["chg"] = (df["close"] - prev_close).fillna(0.0)
    df["pct_chg"] = ((df["close"] / prev_close - 1.0) * 100.0).replace([pd.NA, pd.NaT, float("inf"), float("-inf")], pd.NA).fillna(0.0)
    df["amplitude"] = (((df["high"] - df["low"]) / prev_close) * 100.0).replace([pd.NA, pd.NaT, float("inf"), float("-inf")], pd.NA).fillna(0.0)
    df["turnover"] = 0.0
    df["symbol"] = symbol
    df["market"] = market
    return df[["date", "open", "close", "high", "low", "volume", "amount", "amplitude", "pct_chg", "chg", "turnover", "symbol", "market"]]


def _fetch_history_with_reliable_fallback(
    ctx: AppContext,
    symbol: str,
    market: str,
    asset_type: str,
    start_date: date,
) -> pd.DataFrame:
    if asset_type == "etf":
        try:
            return _validate_daily_frame(
                _fetch_etf_history_via_akshare(symbol=symbol, market=market, start_date=start_date),
                symbol=symbol,
            )
        except Exception:
            result = ctx.provider.fetch_daily_history(symbol=symbol, market=market, start_date=start_date)
            return _validate_daily_frame(result.rows, symbol=symbol)

    try:
        return _validate_daily_frame(
            _fetch_stock_history_via_akshare(symbol=symbol, market=market, start_date=start_date),
            symbol=symbol,
        )
    except Exception:
        result = ctx.provider.fetch_daily_history(symbol=symbol, market=market, start_date=start_date)
        return _validate_daily_frame(result.rows, symbol=symbol)


def build_app(config: AppConfig) -> AppContext:
    storage = Storage(config)
    storage.ensure_dirs()
    return AppContext(
        config=config,
        storage=storage,
        provider=EastMoneyDataProvider(config),
        strategy=create_strategy(config.section("strategy")),
    )


def update_data(ctx: AppContext) -> dict:
    data_cfg = ctx.config.section("data")
    local_universe = ctx.storage.read_universe()
    try:
        universe = ctx.provider.fetch_universe()
    except Exception:
        universe = pd.DataFrame()
    if not universe.empty and "symbol" in universe.columns:
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    if not local_universe.empty and "symbol" in local_universe.columns:
        local_universe["symbol"] = local_universe["symbol"].astype(str).str.zfill(6)
    if universe.empty:
        universe = local_universe
    elif not local_universe.empty:
        for column, default in [("asset_type", "stock"), ("market_group", ""), ("market", ""), ("name", "")]:
            if column not in local_universe.columns:
                local_universe[column] = default
        universe = (
            pd.concat([universe, local_universe], ignore_index=True)
            .drop_duplicates(subset=["symbol"], keep="first")
            .reset_index(drop=True)
        )
    if universe.empty:
        raise RuntimeError("未获取到股票列表，且本地也没有可用 universe 数据")
    holdings = ctx.storage.read_holdings()
    if not holdings.empty:
        missing_held = holdings[~holdings["symbol"].isin(set(universe["symbol"].astype(str)))]
        if not missing_held.empty:
            appended = []
            for row in missing_held.itertuples(index=False):
                symbol = str(row.symbol).zfill(6)
                asset_type = row.asset_type if getattr(row, "asset_type", "") else _infer_asset_type(symbol)
                market = _infer_market(symbol)
                appended.append(
                    {
                        "symbol": symbol,
                        "name": getattr(row, "name", ""),
                        "market": market,
                        "asset_type": asset_type,
                        "market_group": f"{asset_type}_{market.lower()}",
                        "list_date": None,
                        "close": None,
                        "pct_chg": None,
                        "amount": None,
                    }
                )
            universe = pd.concat([universe, pd.DataFrame(appended)], ignore_index=True).drop_duplicates(subset=["symbol"], keep="first")
    allowed_asset_types = set(data_cfg.get("include_asset_types", ["stock", "etf"]))
    if "asset_type" in universe.columns:
        universe = universe[universe["asset_type"].isin(allowed_asset_types)].reset_index(drop=True)
    ctx.storage.write_universe(universe)

    limit = int(data_cfg.get("initial_max_symbols", 1200))
    incremental_lookback_days = int(data_cfg.get("incremental_lookback_days", 10))
    incremental_new_symbol_max = int(data_cfg.get("incremental_new_symbol_max", 400))
    existing = {path.stem for path in ctx.storage.daily_dir.glob("*.csv")}
    lookback_days = int(data_cfg.get("initial_lookback_days", 500))
    held_symbols = set(holdings["symbol"].astype(str).str.zfill(6)) if not holdings.empty else set()
    if existing:
        existing_df = universe[universe["symbol"].astype(str).isin(existing)]
        new_df = universe[~universe["symbol"].astype(str).isin(existing)].copy()
        new_df["priority"] = 3
        if held_symbols:
            new_df.loc[new_df["symbol"].astype(str).isin(held_symbols), "priority"] = 0
        if "asset_type" in new_df.columns:
            new_df.loc[new_df["asset_type"] == "etf", "priority"] = new_df["priority"].clip(upper=1)
        new_df = new_df.sort_values(["priority", "asset_type", "symbol"]).head(incremental_new_symbol_max)
        batches = [
            ("incremental_existing", existing_df, date.today() - timedelta(days=incremental_lookback_days)),
            ("incremental_new", new_df, date.today() - timedelta(days=lookback_days)),
        ]
        scope = "incremental"
    else:
        batches = [("initial", universe.head(limit), date.today() - timedelta(days=lookback_days))]
        scope = "initial"

    rows_written = 0
    downloaded_symbols = 0
    failed_symbols = 0
    states = []
    for _, target_df, start_date in batches:
        for row in target_df.itertuples(index=False):
            symbol = str(row.symbol).zfill(6)
            market = str(row.market)
            asset_type = str(getattr(row, "asset_type", _infer_asset_type(symbol)))
            try:
                history_df = _fetch_history_with_reliable_fallback(
                    ctx=ctx,
                    symbol=symbol,
                    market=market,
                    asset_type=asset_type,
                    start_date=start_date,
                )
            except Exception:
                failed_symbols += 1
                continue
            if history_df.empty:
                continue
            downloaded_symbols += 1
            rows_written += ctx.storage.write_symbol(symbol, history_df)
            states.append(
                {
                    "symbol": symbol,
                    "name": getattr(row, "name", ""),
                    "market": market,
                    "last_trade_date": history_df["date"].max().strftime("%Y-%m-%d"),
                    "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "list_date": pd.to_datetime(getattr(row, "list_date", None)).strftime("%Y-%m-%d")
                    if pd.notna(getattr(row, "list_date", None))
                    else "",
                }
            )
    ctx.storage.upsert_symbol_state(pd.DataFrame(states))
    ctx.storage.log_update(
        run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scope=scope,
        symbols=downloaded_symbols,
        rows_written=rows_written,
        note=f"universe={len(universe)}",
    )
    return {
        "scope": scope,
        "universe_size": len(universe),
        "downloaded_symbols": downloaded_symbols,
        "failed_symbols": failed_symbols,
        "rows_written": rows_written,
    }


def _read_symbol_last_date(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        end = handle.tell()
        if end == 0:
            return ""
        pos = end - 1
        # If the file ends with a newline, skip it and find the previous line break.
        handle.seek(pos)
        if handle.read(1) == b"\n" and pos > 0:
            pos -= 1
        while pos > 0:
            handle.seek(pos)
            if handle.read(1) == b"\n":
                pos += 1
                break
            pos -= 1
        handle.seek(pos if pos > 0 else 0)
        line = handle.readline().decode("utf-8", errors="ignore").strip()
    if not line or line.startswith("date,"):
        return ""
    return line.split(",", 1)[0]


def _load_last_dates(ctx: AppContext, existing_paths: list[Path] | None = None) -> dict[str, str]:
    paths = existing_paths or sorted(ctx.storage.daily_dir.glob("*.csv"))
    last_dates: dict[str, str] = {}
    symbol_state = ctx.storage.read_symbol_state()
    if not symbol_state.empty and "last_trade_date" in symbol_state.columns:
        valid_state = symbol_state[symbol_state["last_trade_date"].fillna("") != ""].copy()
        if not valid_state.empty:
            last_dates.update(dict(valid_state[["symbol", "last_trade_date"]].itertuples(index=False, name=None)))
    for path in paths:
        last_date = _read_symbol_last_date(path)
        if last_date and last_date > last_dates.get(path.stem, ""):
            last_dates[path.stem] = last_date
    return last_dates


def _run_targeted_update(
    ctx: AppContext,
    target_df: pd.DataFrame,
    start_date: date,
    workers: int,
    scope: str,
    reference_date: str = "",
) -> dict:
    rows_written = 0
    downloaded_symbols = 0
    failed_symbols = 0
    states: list[dict] = []
    failures: list[dict] = []
    target_rows = list(target_df.itertuples(index=False))

    def _fetch_one(row) -> tuple[object, pd.DataFrame | None, str]:
        symbol = str(row.symbol).zfill(6)
        market = str(row.market)
        asset_type = str(getattr(row, "asset_type", _infer_asset_type(symbol)))
        try:
            history_df = _fetch_history_with_reliable_fallback(
                ctx=ctx,
                symbol=symbol,
                market=market,
                asset_type=asset_type,
                start_date=start_date,
            )
            return row, history_df, ""
        except Exception as exc:
            return row, None, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_fetch_one, row) for row in target_rows]
        for index, future in enumerate(as_completed(futures), start=1):
            row, history_df, error = future.result()
            symbol = str(row.symbol).zfill(6)
            market = str(row.market)
            if history_df is None or history_df.empty:
                failed_symbols += 1
                failures.append(
                    {
                        "symbol": symbol,
                        "name": getattr(row, "name", ""),
                        "asset_type": getattr(row, "asset_type", ""),
                        "market": market,
                        "last_date_before": getattr(row, "_last_date", ""),
                        "error": error or "empty_result",
                    }
                )
            else:
                downloaded_symbols += 1
                rows_written += ctx.storage.write_symbol(symbol, history_df)
                states.append(
                    {
                        "symbol": symbol,
                        "name": getattr(row, "name", ""),
                        "market": market,
                        "last_trade_date": history_df["date"].max().strftime("%Y-%m-%d"),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "list_date": pd.to_datetime(getattr(row, "list_date", None)).strftime("%Y-%m-%d")
                        if pd.notna(getattr(row, "list_date", None))
                        else "",
                    }
                )
            if index % 100 == 0 or index == len(target_rows):
                print(
                    f"[{scope}] {index}/{len(target_rows)} done; "
                    f"success={downloaded_symbols}; failed={failed_symbols}",
                    flush=True,
                )

    failures_df = pd.DataFrame(failures)
    failures_path = ctx.storage.outputs_dir / f"{scope}_failures.csv"
    failures_df.to_csv(failures_path, index=False)
    if scope == "fast_update":
        failures_df.to_csv(ctx.storage.outputs_dir / "fast_update_failures.csv", index=False)
    ctx.storage.upsert_symbol_state(pd.DataFrame(states))
    ctx.storage.log_update(
        run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scope="fast_incremental" if scope == "fast_update" else scope,
        symbols=downloaded_symbols,
        rows_written=rows_written,
        note=(
            f"reference_date={reference_date};requested={len(target_rows)};"
            f"lookback_days={(date.today() - start_date).days};failed={failed_symbols}"
        ),
    )
    return {
        "scope": "fast_incremental" if scope == "fast_update" else scope,
        "reference_date": reference_date,
        "requested_symbols": len(target_rows),
        "downloaded_symbols": downloaded_symbols,
        "failed_symbols": failed_symbols,
        "rows_written": rows_written,
        "failures_path": str(failures_path),
    }


def build_stale_report(ctx: AppContext) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    universe = ctx.storage.read_universe()
    if universe.empty:
        raise RuntimeError("本地没有 universe 数据，请先执行 update")
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    existing_paths = sorted(ctx.storage.daily_dir.glob("*.csv"))
    last_dates = _load_last_dates(ctx, existing_paths=existing_paths)
    if not last_dates:
        raise RuntimeError("本地没有可用的 daily 数据")
    reference_date = max(last_dates.values())
    stale_df = universe.copy()
    stale_df["last_date"] = stale_df["symbol"].map(lambda s: last_dates.get(s, ""))
    stale_df["days_lag"] = stale_df["last_date"].map(
        lambda value: (pd.Timestamp(reference_date) - pd.Timestamp(value)).days if value else None
    )
    stale_df = stale_df[stale_df["last_date"] < reference_date].copy()
    stale_df = stale_df.sort_values(["last_date", "asset_type", "market", "symbol"]).reset_index(drop=True)
    summary_df = (
        stale_df.groupby(["asset_type", "market", "last_date"], dropna=False)
        .agg(symbols=("symbol", "count"))
        .reset_index()
        .sort_values(["last_date", "asset_type", "market"])
        .reset_index(drop=True)
    )
    stale_df.to_csv(ctx.storage.outputs_dir / "stale_symbols.csv", index=False)
    summary_df.to_csv(ctx.storage.outputs_dir / "stale_summary.csv", index=False)
    return stale_df, summary_df, reference_date


def retry_failed_symbols(ctx: AppContext, workers: int = 8, lookback_days: int = 14) -> dict:
    failure_path = ctx.storage.outputs_dir / "fast_update_failures.csv"
    if not failure_path.exists():
        raise RuntimeError("未找到 fast_update_failures.csv，请先执行 fast_update")
    failures = pd.read_csv(failure_path, dtype={"symbol": str})
    if failures.empty:
        return {
            "scope": "retry_failed",
            "requested_symbols": 0,
            "downloaded_symbols": 0,
            "failed_symbols": 0,
            "rows_written": 0,
        }
    universe = ctx.storage.read_universe()
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    target_df = universe[universe["symbol"].isin(set(failures["symbol"].astype(str).str.zfill(6)))].copy()
    target_df["_last_date"] = target_df["symbol"].map(
        lambda s: failures.set_index(failures["symbol"].astype(str).str.zfill(6))["last_date_before"].to_dict().get(s, "")
    )
    start_date = date.today() - timedelta(days=lookback_days)
    result = _run_targeted_update(
        ctx=ctx,
        target_df=target_df,
        start_date=start_date,
        workers=workers,
        scope="retry_failed",
    )
    retry_failure_path = ctx.storage.outputs_dir / "retry_failed_failures.csv"
    if retry_failure_path.exists():
        pd.read_csv(retry_failure_path, dtype={"symbol": str}).to_csv(failure_path, index=False)
    return result


def fast_update_data(ctx: AppContext, workers: int = 16, lookback_days: int = 7) -> dict:
    universe = ctx.storage.read_universe()
    if universe.empty:
        raise RuntimeError("本地没有 universe 数据，请先执行 update")
    if "symbol" in universe.columns:
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)

    existing_paths = sorted(ctx.storage.daily_dir.glob("*.csv"))
    if not existing_paths:
        return update_data(ctx)

    existing_symbols = {path.stem for path in existing_paths}
    targets = universe[universe["symbol"].isin(existing_symbols)].copy()
    if targets.empty:
        return update_data(ctx)

    last_dates = _load_last_dates(ctx, existing_paths=existing_paths)
    counter: Counter[str] = Counter(last_dates.values())
    if not counter:
        return update_data(ctx)

    reference_date = max(counter)
    stale_targets = targets[targets["symbol"].map(lambda s: last_dates.get(str(s).zfill(6), "")) < reference_date].copy()
    if stale_targets.empty:
        ctx.storage.log_update(
            run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            scope="fast_incremental",
            symbols=0,
            rows_written=0,
            note=f"reference_date={reference_date};stale=0",
        )
        return {
            "scope": "fast_incremental",
            "reference_date": reference_date,
            "requested_symbols": 0,
            "downloaded_symbols": 0,
            "failed_symbols": 0,
            "rows_written": 0,
        }

    stale_targets["_last_date"] = stale_targets["symbol"].map(lambda s: last_dates.get(str(s).zfill(6), ""))
    stale_targets = stale_targets.sort_values(["_last_date", "asset_type", "symbol"]).reset_index(drop=True)

    start_date = pd.to_datetime(reference_date).date() - timedelta(days=lookback_days)
    return _run_targeted_update(
        ctx=ctx,
        target_df=stale_targets,
        start_date=start_date,
        workers=workers,
        scope="fast_update",
        reference_date=reference_date,
    )


def run_experiment(ctx: AppContext) -> pd.DataFrame:
    data_cfg = ctx.config.section("data")
    universe = ctx.storage.read_universe()
    if universe.empty:
        raise RuntimeError("本地没有 universe 数据，请先执行 update")
    snapshot = build_latest_snapshot(
        universe=universe,
        daily_dir=ctx.storage.daily_dir,
        min_history_days_stock=int(data_cfg.get("min_history_days_stock", 120)),
        min_history_days_etf=int(data_cfg.get("min_history_days_etf", 60)),
    )
    ranking = ctx.strategy.rank(snapshot)
    output_path = ctx.storage.outputs_dir / "latest_ranking.csv"
    ranking.to_csv(output_path, index=False)
    return ranking


def run_backtest(ctx: AppContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    data_cfg = ctx.config.section("data")
    universe = ctx.storage.read_universe()
    if universe.empty:
        raise RuntimeError("本地没有 universe 数据，请先执行 update")
    panel = build_backtest_panel(
        universe=universe,
        daily_dir=ctx.storage.daily_dir,
        min_history_days_stock=int(data_cfg.get("min_history_days_stock", 120)),
        min_history_days_etf=int(data_cfg.get("min_history_days_etf", 60)),
    )
    returns_df, picks_df = run_cross_sectional_backtest_from_panel(
        panel=panel,
        strategy=ctx.strategy,
        backtest_cfg=ctx.config.section("backtest"),
    )
    if returns_df.empty:
        raise RuntimeError("回测没有生成结果，通常是因为本地数据不足")
    returns_df.to_csv(ctx.storage.outputs_dir / "backtest_returns.csv", index=False)
    picks_df.to_csv(ctx.storage.outputs_dir / "backtest_picks.csv", index=False)
    summary_df = summarize_backtest(returns_df, initial_capital=float(ctx.config.section("backtest").get("initial_capital", 1_000_000)))
    summary_df.to_csv(ctx.storage.outputs_dir / "backtest_summary.csv", index=False)
    build_backtest_diagnostics(returns_df=returns_df, picks_df=picks_df, outputs_dir=ctx.storage.outputs_dir)
    return returns_df, picks_df


def sweep_strategy_params(ctx: AppContext) -> pd.DataFrame:
    data_cfg = ctx.config.section("data")
    backtest_cfg = ctx.config.section("backtest")
    tuning_cfg = ctx.config.section("tuning")
    universe = ctx.storage.read_universe()
    if universe.empty:
        raise RuntimeError("本地没有 universe 数据，请先执行 update")
    panel = build_backtest_panel(
        universe=universe,
        daily_dir=ctx.storage.daily_dir,
        min_history_days_stock=int(data_cfg.get("min_history_days_stock", 120)),
        min_history_days_etf=int(data_cfg.get("min_history_days_etf", 60)),
    )
    if panel.empty:
        raise RuntimeError("参数扫描没有可用回测面板")

    grid = {
        "max_positions": tuning_cfg.get("max_positions_list", [5, 10, 15]),
        "min_price_stock": tuning_cfg.get("min_price_stock_list", [3.0, 5.0, 8.0]),
        "min_avg_turnover_million_stock": tuning_cfg.get("min_avg_turnover_million_stock_list", [50, 80, 120]),
        "min_ret_20": tuning_cfg.get("min_ret_20_list", [0.0, 0.05]),
        "min_ret_60": tuning_cfg.get("min_ret_60_list", [0.0, 0.2]),
        "min_ret_120": tuning_cfg.get("min_ret_120_list", [0.1, 0.15]),
        "weight_ret_60": tuning_cfg.get("weight_ret_60_list", [0.6, 0.7, 0.8]),
    }

    base_strategy_cfg = ctx.config.section("strategy").copy()
    rows = []
    keys = list(grid.keys())
    for values in product(*(grid[key] for key in keys)):
        strategy_cfg = {**base_strategy_cfg, **dict(zip(keys, values, strict=False))}
        strategy = MomentumStrategy(strategy_cfg)
        returns_df, _ = run_cross_sectional_backtest_from_panel(
            panel=panel,
            strategy=strategy,
            backtest_cfg=backtest_cfg,
        )
        if returns_df.empty:
            continue
        summary_df = summarize_backtest(returns_df, initial_capital=float(backtest_cfg.get("initial_capital", 1_000_000)))
        if summary_df.empty:
            continue
        row = summary_df.iloc[0].to_dict()
        row.update(strategy.params())
        rows.append(row)

    result_df = pd.DataFrame(rows)
    if result_df.empty:
        raise RuntimeError("参数扫描没有生成结果")
    result_df = result_df.sort_values(["sharpe", "annual_return", "max_drawdown"], ascending=[False, False, False]).reset_index(drop=True)
    result_df.to_csv(ctx.storage.outputs_dir / "strategy_sweep.csv", index=False)
    return result_df


def generate_recommendations(ctx: AppContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    ranking = run_experiment(ctx)
    holdings = ctx.storage.read_holdings()
    actions = ctx.strategy.review_holdings(holdings, ranking)
    top_n = int(ctx.config.section("report").get("top_n", 10))
    ranking.head(top_n).to_csv(ctx.storage.outputs_dir / "daily_recommendations.csv", index=False)
    actions.to_csv(ctx.storage.outputs_dir / "holdings_review.csv", index=False)
    return ranking.head(top_n), actions


def update_sz_stock_data(ctx: AppContext, max_symbols: int = 200) -> dict:
    data_cfg = ctx.config.section("data")
    local_universe = ctx.storage.read_universe()
    try:
        live_universe = ctx.provider.fetch_universe()
    except Exception:
        live_universe = pd.DataFrame()
    frames = [df for df in [live_universe, local_universe] if not df.empty]
    if not frames:
        raise RuntimeError("没有可用 universe 数据，无法补深圳股票")
    universe = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["symbol"], keep="first")
    if "symbol" in universe.columns:
        universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    if "asset_type" not in universe.columns:
        universe["asset_type"] = universe["symbol"].map(_infer_asset_type)
    if "market" not in universe.columns:
        universe["market"] = universe["symbol"].map(_infer_market)
    sz_stock = universe[(universe["market"] == "SZ") & (universe["asset_type"] == "stock")].copy()
    if sz_stock.empty:
        raise RuntimeError("当前 universe 中没有深圳股票")

    existing = {path.stem for path in ctx.storage.daily_dir.glob("*.csv")}
    holdings = ctx.storage.read_holdings()
    held_symbols = set(holdings["symbol"].astype(str).str.zfill(6)) if not holdings.empty else set()

    sz_stock["priority"] = 2
    sz_stock.loc[sz_stock["symbol"].isin(held_symbols), "priority"] = 0
    sz_stock.loc[~sz_stock["symbol"].isin(existing), "priority"] = sz_stock["priority"] - 1
    target_df = sz_stock.sort_values(["priority", "symbol"]).head(max_symbols)

    lookback_days = int(data_cfg.get("initial_lookback_days", 500))
    start_date = date.today() - timedelta(days=lookback_days)
    rows_written = 0
    downloaded_symbols = 0
    failed_symbols = 0
    for row in target_df.itertuples(index=False):
        symbol = str(row.symbol).zfill(6)
        asset_type = str(getattr(row, "asset_type", _infer_asset_type(symbol)))
        try:
            history_df = _fetch_history_with_reliable_fallback(
                ctx=ctx,
                symbol=symbol,
                market="SZ",
                asset_type=asset_type,
                start_date=start_date,
            )
        except Exception:
            failed_symbols += 1
            continue
        if history_df.empty:
            failed_symbols += 1
            continue
        downloaded_symbols += 1
        rows_written += ctx.storage.write_symbol(symbol, history_df)

    ctx.storage.write_universe(universe)
    ctx.storage.log_update(
        run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scope="sz_stock_partial",
        symbols=downloaded_symbols,
        rows_written=rows_written,
        note=f"requested={len(target_df)}",
    )
    return {
        "scope": "sz_stock_partial",
        "requested_symbols": len(target_df),
        "downloaded_symbols": downloaded_symbols,
        "failed_symbols": failed_symbols,
        "rows_written": rows_written,
    }


def repair_recent_data(ctx: AppContext, start_date: date, max_symbols: int | None = None, workers: int = 4) -> dict:
    existing = {path.stem for path in ctx.storage.daily_dir.glob("*.csv")}
    universe = ctx.storage.read_universe()
    if universe.empty:
        target_df = pd.DataFrame({"symbol": sorted(existing)})
        target_df["market"] = target_df["symbol"].map(_infer_market)
        target_df["asset_type"] = target_df["symbol"].map(_infer_asset_type)
        target_df["name"] = ""
    else:
        if "symbol" in universe.columns:
            universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
        if "market" not in universe.columns:
            universe["market"] = universe["symbol"].map(_infer_market)
        if "asset_type" not in universe.columns:
            universe["asset_type"] = universe["symbol"].map(_infer_asset_type)
        target_df = universe[universe["symbol"].isin(existing)].copy()
        missing_symbols = sorted(existing - set(target_df["symbol"].astype(str)))
        if missing_symbols:
            fallback = pd.DataFrame({"symbol": missing_symbols})
            fallback["market"] = fallback["symbol"].map(_infer_market)
            fallback["asset_type"] = fallback["symbol"].map(_infer_asset_type)
            fallback["name"] = ""
            target_df = pd.concat([target_df, fallback], ignore_index=True)
    if max_symbols is not None and max_symbols > 0:
        target_df = target_df.head(max_symbols).copy()
    if target_df.empty:
        return {
            "scope": "repair_recent",
            "requested_symbols": 0,
            "downloaded_symbols": 0,
            "failed_symbols": 0,
            "rows_written": 0,
        }
    return _run_targeted_update(
        ctx=ctx,
        target_df=target_df,
        start_date=start_date,
        workers=workers,
        scope="repair_recent",
    )


def plot_symbol_chart(ctx: AppContext, symbol: str, days: int = 120) -> Path:
    normalized = str(symbol).zfill(6)
    df = ctx.storage.read_symbol(normalized)
    output_path = ctx.storage.outputs_dir / "charts" / f"{normalized}_candles.png"
    return render_candlestick_chart(df=df, symbol=normalized, output_path=output_path, days=days)


def generate_data_inventory(ctx: AppContext) -> tuple[pd.DataFrame, pd.DataFrame]:
    universe = ctx.storage.read_universe()
    detail_df, summary_df = build_data_inventory(
        daily_dir=ctx.storage.daily_dir,
        universe_df=universe,
        metadata_db=ctx.storage.metadata_db,
    )
    detail_df.to_csv(ctx.storage.outputs_dir / "data_inventory_detail.csv", index=False)
    summary_df.to_csv(ctx.storage.outputs_dir / "data_inventory_summary.csv", index=False)
    return detail_df, summary_df


def generate_daily_status_report(
    ctx: AppContext,
    update_result: dict | None = None,
    ranking: pd.DataFrame | None = None,
    inventory_summary: pd.DataFrame | None = None,
    quality_summary: pd.DataFrame | None = None,
) -> Path:
    if inventory_summary is None:
        _, inventory_summary = generate_data_inventory(ctx)

    ranking_preview = ranking if ranking is not None else pd.DataFrame()
    if ranking_preview.empty:
        ranking_path = ctx.storage.outputs_dir / "daily_recommendations.csv"
        if ranking_path.exists():
            ranking_preview = pd.read_csv(ranking_path, dtype={"symbol": str})

    lines = [
        f"# Daily Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if update_result is not None:
        lines.extend(
            [
                "## Update",
                "",
                f"- scope: {update_result.get('scope', '')}",
                f"- universe_size: {update_result.get('universe_size', update_result.get('requested_symbols', ''))}",
                f"- downloaded_symbols: {update_result.get('downloaded_symbols', '')}",
                f"- failed_symbols: {update_result.get('failed_symbols', '')}",
                f"- rows_written: {update_result.get('rows_written', '')}",
                "",
            ]
        )
        failure_path = ctx.storage.outputs_dir / "fast_update_failures.csv"
        if failure_path.exists():
            failure_df = pd.read_csv(failure_path, dtype={"symbol": str})
            if not failure_df.empty:
                preview_cols = [col for col in ["symbol", "name", "asset_type", "market", "last_date_before", "error"] if col in failure_df.columns]
                lines.extend(
                    [
                        "## Update Failures",
                        "",
                        failure_df[preview_cols].head(20).to_string(index=False),
                        "",
                    ]
                )

    if inventory_summary is not None and not inventory_summary.empty:
        lines.extend(["## Inventory Summary", "", inventory_summary.to_string(index=False), ""])

    if quality_summary is None:
        quality_path = ctx.storage.outputs_dir / "data_quality_summary.csv"
        if quality_path.exists():
            quality_summary = pd.read_csv(quality_path)
    if quality_summary is not None and not quality_summary.empty:
        lines.extend(["## Data Quality", "", quality_summary.to_string(index=False), ""])

    if ranking_preview is not None and not ranking_preview.empty:
        preview_cols = [col for col in ["symbol", "name", "asset_type", "close", "ret_20", "ret_60", "score", "reason"] if col in ranking_preview.columns]
        lines.extend(["## Top Recommendations", "", ranking_preview[preview_cols].head(10).to_string(index=False), ""])

    report_path = ctx.storage.outputs_dir / "daily_status.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def backfill_stock_history(
    ctx: AppContext,
    start_date: date,
    max_symbols: int | None = None,
    only_missing: bool = False,
    workers: int = 4,
) -> dict:
    universe = _load_merged_universe(ctx)
    stocks = universe[universe["asset_type"].eq("stock")].copy()
    if stocks.empty:
        raise RuntimeError("当前 universe 中没有股票")

    existing = {path.stem for path in ctx.storage.daily_dir.glob("*.csv")}
    if only_missing:
        stocks = stocks[~stocks["symbol"].isin(existing)].copy()

    if max_symbols is not None and max_symbols > 0:
        stocks = stocks.head(max_symbols).copy()
    if stocks.empty:
        return {
            "scope": "stock_backfill",
            "requested_symbols": 0,
            "downloaded_symbols": 0,
            "failed_symbols": 0,
            "rows_written": 0,
        }

    rows_written = 0
    downloaded_symbols = 0
    failed_symbols = 0
    states: list[dict] = []
    stock_rows = list(stocks.itertuples(index=False))

    def _fetch_one(row) -> tuple[object, pd.DataFrame | None]:
        symbol = str(row.symbol).zfill(6)
        market = str(row.market)
        try:
            return row, _fetch_stock_history_via_akshare(symbol=symbol, market=market, start_date=start_date)
        except Exception:
            return row, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_fetch_one, row) for row in stock_rows]
        for index, future in enumerate(as_completed(futures), start=1):
            row, history_df = future.result()
            symbol = str(row.symbol).zfill(6)
            market = str(row.market)
            if history_df is None or history_df.empty:
                failed_symbols += 1
            else:
                downloaded_symbols += 1
                rows_written += ctx.storage.write_symbol(symbol, history_df)
                states.append(
                    {
                        "symbol": symbol,
                        "name": getattr(row, "name", ""),
                        "market": market,
                        "last_trade_date": history_df["date"].max().strftime("%Y-%m-%d"),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "list_date": pd.to_datetime(getattr(row, "list_date", None)).strftime("%Y-%m-%d")
                        if pd.notna(getattr(row, "list_date", None))
                        else "",
                    }
                )
            if index % 25 == 0 or index == len(stock_rows):
                print(
                    f"[backfill_stocks] {index}/{len(stock_rows)} done; "
                    f"success={downloaded_symbols}; failed={failed_symbols}"
                )
    ctx.storage.upsert_symbol_state(pd.DataFrame(states))
    ctx.storage.log_update(
        run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scope="stock_backfill",
        symbols=downloaded_symbols,
        rows_written=rows_written,
        note=f"start_date={start_date.strftime('%Y-%m-%d')};requested={len(stocks)};only_missing={int(only_missing)}",
    )
    generate_data_inventory(ctx)
    return {
        "scope": "stock_backfill",
        "requested_symbols": len(stocks),
        "downloaded_symbols": downloaded_symbols,
        "failed_symbols": failed_symbols,
        "rows_written": rows_written,
    }


def backfill_etf_history(
    ctx: AppContext,
    start_date: date,
    max_symbols: int | None = None,
    only_missing: bool = False,
    workers: int = 4,
) -> dict:
    universe = _load_merged_universe(ctx)
    etfs = universe[universe["asset_type"].eq("etf")].copy()
    if etfs.empty:
        raise RuntimeError("当前 universe 中没有 ETF")

    existing = {path.stem for path in ctx.storage.daily_dir.glob("*.csv")}
    if only_missing:
        etfs = etfs[~etfs["symbol"].isin(existing)].copy()
    if max_symbols is not None and max_symbols > 0:
        etfs = etfs.head(max_symbols).copy()
    if etfs.empty:
        return {
            "scope": "etf_backfill",
            "requested_symbols": 0,
            "downloaded_symbols": 0,
            "failed_symbols": 0,
            "rows_written": 0,
        }

    rows_written = 0
    downloaded_symbols = 0
    failed_symbols = 0
    states: list[dict] = []
    etf_rows = list(etfs.itertuples(index=False))

    def _fetch_one(row) -> tuple[object, pd.DataFrame | None]:
        symbol = str(row.symbol).zfill(6)
        market = str(row.market)
        try:
            return row, _fetch_etf_history_via_akshare(symbol=symbol, market=market, start_date=start_date)
        except Exception:
            return row, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(_fetch_one, row) for row in etf_rows]
        for index, future in enumerate(as_completed(futures), start=1):
            row, history_df = future.result()
            symbol = str(row.symbol).zfill(6)
            market = str(row.market)
            if history_df is None or history_df.empty:
                failed_symbols += 1
            else:
                downloaded_symbols += 1
                rows_written += ctx.storage.write_symbol(symbol, history_df)
                states.append(
                    {
                        "symbol": symbol,
                        "name": getattr(row, "name", ""),
                        "market": market,
                        "last_trade_date": history_df["date"].max().strftime("%Y-%m-%d"),
                        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "list_date": pd.to_datetime(getattr(row, "list_date", None)).strftime("%Y-%m-%d")
                        if pd.notna(getattr(row, "list_date", None))
                        else "",
                    }
                )
            if index % 25 == 0 or index == len(etf_rows):
                print(
                    f"[backfill_etfs] {index}/{len(etf_rows)} done; "
                    f"success={downloaded_symbols}; failed={failed_symbols}"
                )
    ctx.storage.upsert_symbol_state(pd.DataFrame(states))
    ctx.storage.log_update(
        run_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        scope="etf_backfill",
        symbols=downloaded_symbols,
        rows_written=rows_written,
        note=f"start_date={start_date.strftime('%Y-%m-%d')};requested={len(etf_rows)};only_missing={int(only_missing)}",
    )
    generate_data_inventory(ctx)
    return {
        "scope": "etf_backfill",
        "requested_symbols": len(etf_rows),
        "downloaded_symbols": downloaded_symbols,
        "failed_symbols": failed_symbols,
        "rows_written": rows_written,
    }
