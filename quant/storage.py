from __future__ import annotations

import sqlite3
from pathlib import Path
import os

import pandas as pd
from pandas.errors import EmptyDataError, ParserError

from quant.config import AppConfig
from quant.data_quality import normalize_daily_frame


DAILY_CSV_DTYPES = {"symbol": str, "market": str}


class Storage:
    def __init__(self, config: AppConfig) -> None:
        storage_cfg = config.section("storage")
        self.root_dir = config.root / storage_cfg.get("root_dir", ".")
        self.data_dir = self.root_dir / storage_cfg.get("data_dir", "data")
        self.raw_dir = self.root_dir / storage_cfg.get("raw_dir", "data/raw")
        self.daily_dir = self.root_dir / storage_cfg.get("daily_dir", "data/daily")
        self.outputs_dir = self.root_dir / storage_cfg.get("outputs_dir", "outputs")
        self.metadata_db = self.root_dir / storage_cfg.get("metadata_db", "data/metadata.sqlite3")
        self.holdings_file = self.root_dir / storage_cfg.get("holdings_file", "data/holdings.csv")
        self.universe_file = self.root_dir / storage_cfg.get("universe_file", "data/universe.csv")

    def ensure_dirs(self) -> None:
        for path in [self.data_dir, self.raw_dir, self.daily_dir, self.outputs_dir]:
            path.mkdir(parents=True, exist_ok=True)
        if not self.holdings_file.exists():
            self.holdings_file.write_text(
                "symbol,name,asset_type,shares,cost_basis,last_action_date,notes\n",
                encoding="utf-8",
            )
        self._init_db()

    def _init_db(self) -> None:
        self.metadata_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.metadata_db) as conn:
            conn.execute(
                """
                create table if not exists update_log (
                    run_at text not null,
                    scope text not null,
                    symbols integer not null,
                    rows_written integer not null,
                    note text
                )
                """
            )
            conn.execute(
                """
                create table if not exists symbol_state (
                    symbol text primary key,
                    name text,
                    market text,
                    last_trade_date text,
                    updated_at text,
                    list_date text
                )
                """
            )

    def symbol_path(self, symbol: str) -> Path:
        return self.daily_dir / f"{symbol}.csv"

    def read_symbol(self, symbol: str) -> pd.DataFrame:
        path = self.symbol_path(symbol)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path, dtype=DAILY_CSV_DTYPES)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def write_symbol(self, symbol: str, df: pd.DataFrame) -> int:
        path = self.symbol_path(symbol)
        market = ""
        if path.exists():
            try:
                old = pd.read_csv(path, dtype=DAILY_CSV_DTYPES)
            except (EmptyDataError, ParserError):
                old = pd.DataFrame()
            if not old.empty:
                if "date" in old.columns:
                    old["date"] = pd.to_datetime(old["date"])
                if "market" in old.columns and old["market"].notna().any():
                    market = str(old["market"].dropna().iloc[-1])
                df = pd.concat([old, df], ignore_index=True)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        if not market and "market" in df.columns and df["market"].notna().any():
            market = str(df["market"].dropna().iloc[-1])
        df = normalize_daily_frame(df, symbol=str(symbol).zfill(6), market=market)
        tmp_path = path.with_suffix(f"{path.suffix}.tmp")
        df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
        return len(df)

    def read_universe(self) -> pd.DataFrame:
        if not self.universe_file.exists():
            return pd.DataFrame()
        df = pd.read_csv(self.universe_file, dtype={"symbol": str})
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df

    def write_universe(self, df: pd.DataFrame) -> None:
        if "symbol" in df.columns:
            df = df.copy()
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df.to_csv(self.universe_file, index=False)

    def read_holdings(self) -> pd.DataFrame:
        if not self.holdings_file.exists():
            return pd.DataFrame(
                columns=["symbol", "name", "asset_type", "shares", "cost_basis", "last_action_date", "notes"]
            )
        df = pd.read_csv(self.holdings_file, dtype={"symbol": str})
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        if "asset_type" not in df.columns:
            df["asset_type"] = ""
        return df

    def log_update(self, run_at: str, scope: str, symbols: int, rows_written: int, note: str = "") -> None:
        with sqlite3.connect(self.metadata_db) as conn:
            conn.execute(
                "insert into update_log (run_at, scope, symbols, rows_written, note) values (?, ?, ?, ?, ?)",
                (run_at, scope, symbols, rows_written, note),
            )

    def upsert_symbol_state(self, df: pd.DataFrame) -> None:
        if df.empty:
            return
        with sqlite3.connect(self.metadata_db) as conn:
            conn.executemany(
                """
                insert into symbol_state (symbol, name, market, last_trade_date, updated_at, list_date)
                values (?, ?, ?, ?, ?, ?)
                on conflict(symbol) do update set
                    name = excluded.name,
                    market = excluded.market,
                    last_trade_date = excluded.last_trade_date,
                    updated_at = excluded.updated_at,
                    list_date = excluded.list_date
                """,
                df[["symbol", "name", "market", "last_trade_date", "updated_at", "list_date"]].itertuples(index=False, name=None),
            )

    def read_symbol_state(self) -> pd.DataFrame:
        with sqlite3.connect(self.metadata_db) as conn:
            df = pd.read_sql_query("select symbol, name, market, last_trade_date, updated_at, list_date from symbol_state", conn)
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        return df
