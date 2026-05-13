from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import akshare as ak
import pandas as pd
import requests

from quant.config import AppConfig


EASTMONEY_QUOTE_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SSE_FUND_LIST_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SZSE_ETF_LIST_URL = "https://www.szse.cn/api/report/ShowReport/data"
SSE_STOCK_LIST_URL = "https://query.sse.com.cn/sseQuery/commonQuery.do"
SZSE_STOCK_LIST_URL = "https://www.szse.cn/api/report/ShowReport/data"


@dataclass
class FetchResult:
    symbol: str
    rows: pd.DataFrame
    source: str = ""


class EastMoneyDataProvider:
    def __init__(self, config: AppConfig) -> None:
        data_cfg = config.section("data")
        storage_cfg = config.section("storage")
        self.adjust = data_cfg.get("adjust", "qfq")
        self.fallback_provider = str(data_cfg.get("fallback_provider", "none")).lower()
        self.timeout = int(data_cfg.get("request_timeout", 20))
        self.sleep_seconds = float(data_cfg.get("sleep_seconds", 0.1))
        self.max_retries = int(data_cfg.get("max_retries", 3))
        self.cache_dir = config.root / storage_cfg.get("raw_dir", "data/raw") / "universe_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._thread_local = threading.local()
        self._base_headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        }

    def fetch_universe(self) -> pd.DataFrame:
        frames = [
            self._fetch_sse_stock_universe(),
            self._fetch_sh_kcb_universe(),
            self._fetch_szse_stock_universe(),
            self._fetch_etf_universe(),
        ]
        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame(
                columns=["symbol", "name", "market", "asset_type", "market_group", "list_date", "close", "pct_chg", "amount"]
            )
        df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["symbol"])
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
        return df[["symbol", "name", "market", "asset_type", "market_group", "list_date", "close", "pct_chg", "amount"]]

    def _fetch_etf_universe(self) -> pd.DataFrame:
        cache_name = "etf_universe"
        try:
            df = ak.fund_name_em()
        except Exception:
            return self._read_universe_cache(cache_name)
        code = df["基金代码"].astype(str).str.zfill(6)
        name = df["基金简称"].astype(str)
        etf_prefixes = (
            "159",
            "510",
            "511",
            "512",
            "513",
            "515",
            "516",
            "517",
            "518",
            "519",
            "520",
            "530",
            "560",
            "561",
            "562",
            "563",
            "588",
            "589",
        )
        filtered = df[code.str.startswith(etf_prefixes) & name.str.contains("ETF", case=False, na=False)].copy()
        if filtered.empty:
            return self._read_universe_cache(cache_name)
        filtered["symbol"] = filtered["基金代码"].astype(str).str.zfill(6)
        filtered["name"] = filtered["基金简称"].astype(str).str.strip()
        filtered["market"] = filtered["symbol"].map(lambda s: "SZ" if s.startswith("159") else "SH")
        filtered["asset_type"] = "etf"
        filtered["market_group"] = filtered["market"].map({"SH": "etf_sh", "SZ": "etf_sz"})
        filtered["list_date"] = pd.NaT
        filtered["close"] = None
        filtered["pct_chg"] = None
        filtered["amount"] = None
        out = filtered[["symbol", "name", "market", "asset_type", "market_group", "list_date", "close", "pct_chg", "amount"]]
        out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
        self._write_universe_cache(cache_name, out, {"rows": len(out)})
        return out

    def _fetch_sh_kcb_universe(self) -> pd.DataFrame:
        cache_name = "sse_kcb_stock"
        try:
            df = ak.stock_info_sh_name_code(symbol="科创板")
        except Exception:
            return self._read_universe_cache(cache_name)
        if df.empty:
            return self._read_universe_cache(cache_name)
        out = pd.DataFrame(
            {
                "symbol": df["证券代码"].astype(str).str.zfill(6),
                "name": df["证券简称"].astype(str).str.strip(),
                "market": "SH",
                "asset_type": "stock",
                "market_group": "stock_sh",
                "list_date": pd.to_datetime(df["上市日期"], errors="coerce"),
                "close": None,
                "pct_chg": None,
                "amount": None,
            }
        )
        out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
        self._write_universe_cache(cache_name, out, {"rows": len(out)})
        return out

    def _fetch_sse_stock_universe(self) -> pd.DataFrame:
        cache_name = "sse_stock"
        rows: list[dict] = []
        page = 1
        page_count = None
        while True:
            try:
                payload = self._get_json(
                    SSE_STOCK_LIST_URL,
                    {
                        "STOCK_TYPE": "1",
                        "REG_PROVINCE": "",
                        "CSRC_CODE": "",
                        "STOCK_CODE": "",
                        "sqlId": "COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L",
                        "COMPANY_STATUS": "2,4,5,7,8",
                        "type": "inParams",
                        "isPagination": "true",
                        "pageHelp.cacheSize": "1",
                        "pageHelp.beginPage": str(page),
                        "pageHelp.pageSize": "100",
                        "pageHelp.pageNo": str(page),
                    },
                    headers={"Referer": "https://www.sse.com.cn/assortment/stock/list/share/"},
                )
            except requests.RequestException:
                break
            page_help = payload.get("pageHelp") or {}
            items = payload.get("result") or page_help.get("data") or []
            if not items:
                break
            for item in items:
                rows.append(
                    {
                        "symbol": str(item.get("A_STOCK_CODE", "")).strip(),
                        "name": item.get("SEC_NAME_CN", ""),
                        "market": "SH",
                        "asset_type": "stock",
                        "market_group": "stock_sh",
                        "list_date": item.get("LIST_DATE"),
                        "close": None,
                        "pct_chg": None,
                        "amount": None,
                    }
                )
            self._write_universe_cache(cache_name, pd.DataFrame(rows), {"last_page": page, "page_count": page_help.get("pageCount")})
            page_count = int(page_help.get("pageCount") or 0)
            if page >= page_count:
                break
            page += 1
            time.sleep(self.sleep_seconds)
        return self._finalize_cached_universe(cache_name, rows, expected_pages=page_count)

    def _fetch_szse_stock_universe(self) -> pd.DataFrame:
        cache_name = "szse_stock"
        try:
            df = ak.stock_info_sz_name_code(symbol="A股列表")
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            out = pd.DataFrame(
                {
                    "symbol": df["A股代码"].astype(str).str.zfill(6),
                    "name": df["A股简称"].astype(str).str.strip(),
                    "market": "SZ",
                    "asset_type": "stock",
                    "market_group": "stock_sz",
                    "list_date": pd.to_datetime(df["A股上市日期"], errors="coerce"),
                    "close": None,
                    "pct_chg": None,
                    "amount": None,
                }
            )
            out = out.drop_duplicates(subset=["symbol"], keep="first").reset_index(drop=True)
            self._write_universe_cache(cache_name, out, {"rows": len(out), "source": "akshare.stock_info_sz_name_code"})
            return out

        rows: list[dict] = []
        try:
            first_payload = self._get_json(
                SZSE_STOCK_LIST_URL,
                {"SHOWTYPE": "JSON", "CATALOGID": "1110", "PAGENO": "1"},
                headers={"Referer": "https://www.szse.cn/market/product/stock/list/index.html"},
            )
        except requests.RequestException:
            return self._read_universe_cache(cache_name)
        if not first_payload or not isinstance(first_payload, list):
            return self._read_universe_cache(cache_name)
        page_count = int(((first_payload[0].get("metadata") or {}).get("pagecount")) or 0)
        fetched_pages: set[int] = set()
        for _ in range(3):
            for page in range(1, page_count + 1):
                if page in fetched_pages:
                    continue
                try:
                    payload = self._get_json(
                        SZSE_STOCK_LIST_URL,
                        {"SHOWTYPE": "JSON", "CATALOGID": "1110", "PAGENO": str(page)},
                        headers={"Referer": "https://www.szse.cn/market/product/stock/list/index.html"},
                    )
                except requests.RequestException:
                    continue
                items = (payload[0].get("data") or []) if payload else []
                if not items:
                    continue
                fetched_pages.add(page)
                for item in items:
                    rows.append(
                        {
                            "symbol": str(item.get("agdm", "")).strip(),
                            "name": pd.Series([item.get("agjc", "")]).str.replace(r"<[^>]+>", "", regex=True).iloc[0].strip(),
                            "market": "SZ",
                            "asset_type": "stock",
                            "market_group": "stock_sz",
                            "list_date": item.get("agssrq"),
                            "close": None,
                            "pct_chg": None,
                            "amount": None,
                        }
                    )
                self._write_universe_cache(
                    cache_name,
                    pd.DataFrame(rows).drop_duplicates(subset=["symbol"]) if rows else pd.DataFrame(),
                    {"fetched_pages": len(fetched_pages), "page_count": page_count},
                )
                time.sleep(self.sleep_seconds)
            if len(fetched_pages) == page_count:
                break
        return self._finalize_cached_universe(cache_name, rows, expected_pages=page_count, dedupe=True)

    def _fetch_sse_etf_universe(self) -> pd.DataFrame:
        cache_name = "sse_etf"
        rows: list[dict] = []
        page = 1
        page_count = None
        while True:
            params = {
                "isPagination": "true",
                "pageHelp.pageSize": "50",
                "pageHelp.pageNo": str(page),
                "pageHelp.beginPage": str(page),
                "pageHelp.cacheSize": "1",
                "pageHelp.endPage": str(page),
                "pagecache": "false",
                "sqlId": "FUND_LIST",
                "fundType": "00",
                "subClass": "01,02,03,04,06,08,09,31,32,33,34,35,36,37,38",
                "order": "",
            }
            try:
                payload = self._get_json(
                    SSE_FUND_LIST_URL,
                    params,
                    headers={"Referer": "https://www.sse.com.cn/assortment/fund/etf/list/"},
                )
            except requests.RequestException:
                break
            page_help = payload.get("pageHelp") or {}
            items = page_help.get("data") or []
            if not items:
                break
            for item in items:
                rows.append(
                    {
                        "symbol": str(item.get("fundCode", "")).strip(),
                        "name": item.get("fundAbbr", ""),
                        "market": "SH",
                        "asset_type": "etf",
                        "market_group": "etf_sh",
                        "list_date": item.get("listingDate"),
                        "close": None,
                        "pct_chg": None,
                        "amount": None,
                    }
                )
            self._write_universe_cache(cache_name, pd.DataFrame(rows), {"last_page": page, "page_count": page_help.get("pageCount")})
            page_count = int(page_help.get("pageCount") or 0)
            if page >= page_count:
                break
            page += 1
            time.sleep(self.sleep_seconds)
        return self._finalize_cached_universe(cache_name, rows, expected_pages=page_count)

    def _fetch_szse_etf_universe(self) -> pd.DataFrame:
        cache_name = "szse_etf"
        base_params = {
            "SHOWTYPE": "JSON",
            "CATALOGID": "scsj_fund_jjgm",
            "jjlb": "ETF",
        }
        rows: list[dict] = []
        for offset in range(0, 10):
            target_date = date.today() - timedelta(days=offset)
            params = {
                **base_params,
                "txtStart": target_date.strftime("%Y-%m-%d"),
                "txtEnd": target_date.strftime("%Y-%m-%d"),
                "PAGENO": "1",
            }
            try:
                payload = self._get_json(
                    SZSE_ETF_LIST_URL,
                    params,
                    headers={"Referer": "https://www.szse.cn/market/fund/volume/etf/index.html"},
                )
            except requests.RequestException:
                continue
            if not payload or not isinstance(payload, list):
                continue
            block = payload[0]
            page_count = int(((block.get("metadata") or {}).get("pagecount")) or 0)
            if page_count <= 0:
                continue
            for page in range(1, page_count + 1):
                try:
                    page_payload = self._get_json(
                        SZSE_ETF_LIST_URL,
                        {**params, "PAGENO": str(page)},
                        headers={"Referer": "https://www.szse.cn/market/fund/volume/etf/index.html"},
                    )
                except requests.RequestException:
                    break
                for item in (page_payload[0].get("data") or []):
                    rows.append(
                        {
                            "symbol": str(item.get("fund_code", "")).strip(),
                            "name": item.get("security_short_name", ""),
                            "market": "SZ",
                            "asset_type": "etf",
                            "market_group": "etf_sz",
                            "list_date": None,
                            "close": None,
                            "pct_chg": None,
                            "amount": None,
                        }
                    )
                self._write_universe_cache(
                    cache_name,
                    pd.DataFrame(rows).drop_duplicates(subset=["symbol"]) if rows else pd.DataFrame(),
                    {"last_page": page, "page_count": page_count, "as_of_date": target_date.strftime("%Y-%m-%d")},
                )
                time.sleep(self.sleep_seconds)
            if rows:
                break
        return self._finalize_cached_universe(cache_name, rows, expected_pages=page_count if rows else None, dedupe=True)

    def fetch_daily_history(self, symbol: str, market: str, start_date: date | None = None) -> FetchResult:
        start_date = start_date or (date.today() - timedelta(days=800))
        try:
            df = self._fetch_daily_history_eastmoney(symbol=symbol, market=market, start_date=start_date)
            source = "eastmoney"
        except requests.RequestException:
            if self.fallback_provider != "tencent":
                raise
            df = self._fetch_daily_history_tencent(symbol=symbol, market=market, start_date=start_date)
            source = "tencent"
        time.sleep(self.sleep_seconds)
        return FetchResult(symbol=symbol, rows=df, source=source)

    def batch_fetch_daily_history(
        self, symbols: Iterable[tuple[str, str]], start_date: date | None = None
    ) -> list[FetchResult]:
        return [self.fetch_daily_history(symbol=s, market=m, start_date=start_date) for s, m in symbols]

    def _get_json(self, url: str, params: dict, headers: dict | None = None):
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                session = self._session()
                response = session.get(url, params=params, timeout=self.timeout, headers=headers)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == self.max_retries:
                    raise
                time.sleep(self.sleep_seconds * attempt * 5)
        raise RuntimeError(f"request failed: {last_error}")

    def _cache_data_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.csv"

    def _session(self) -> requests.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.headers.update(self._base_headers)
            self._thread_local.session = session
        return session

    def _cache_meta_path(self, name: str) -> Path:
        return self.cache_dir / f"{name}.json"

    def _read_universe_cache(self, name: str) -> pd.DataFrame:
        path = self._cache_data_path(name)
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path, dtype={"symbol": str})
        if "symbol" in df.columns:
            df["symbol"] = df["symbol"].astype(str).str.zfill(6)
        if "list_date" in df.columns:
            df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce")
        return df

    def _write_universe_cache(self, name: str, df: pd.DataFrame, meta: dict | None = None) -> None:
        if df.empty:
            return
        out = df.copy()
        if "symbol" in out.columns:
            out["symbol"] = out["symbol"].astype(str).str.zfill(6)
        out.to_csv(self._cache_data_path(name), index=False)
        if meta is not None:
            payload = {"updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **meta}
            self._cache_meta_path(name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _finalize_cached_universe(
        self,
        cache_name: str,
        rows: list[dict],
        expected_pages: int | None = None,
        dedupe: bool = False,
    ) -> pd.DataFrame:
        live = pd.DataFrame(rows)
        if not live.empty:
            if dedupe:
                live = live.drop_duplicates(subset=["symbol"])
            self._write_universe_cache(cache_name, live, {"page_count": expected_pages})
        cached = self._read_universe_cache(cache_name)
        if live.empty:
            return cached
        if cached.empty:
            return live
        merged = pd.concat([live, cached], ignore_index=True).drop_duplicates(subset=["symbol"], keep="first")
        if "list_date" in merged.columns:
            merged["list_date"] = pd.to_datetime(merged["list_date"], errors="coerce")
        return merged

    def _fetch_daily_history_eastmoney(self, symbol: str, market: str, start_date: date) -> pd.DataFrame:
        secid = self._secid(symbol, market)
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "klt": 101,
            "fqt": {"none": 0, "qfq": 1, "hfq": 2}.get(self.adjust, 1),
            "secid": secid,
            "beg": self._fmt_date(start_date),
            "end": self._fmt_date(date.today()),
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        }
        payload = self._get_json(EASTMONEY_KLINE_URL, params)
        klines = ((payload or {}).get("data") or {}).get("klines") or []
        rows = []
        for item in klines:
            parts = item.split(",")
            if len(parts) < 11:
                continue
            rows.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5]) * 100.0,
                    "amount": float(parts[6]),
                    "amplitude": float(parts[7]),
                    "pct_chg": float(parts[8]),
                    "chg": float(parts[9]),
                    "turnover": float(parts[10]) / 100.0,
                }
            )
        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df["symbol"] = symbol
            df["market"] = market
        return df

    def _fetch_daily_history_tencent(self, symbol: str, market: str, start_date: date) -> pd.DataFrame:
        code = f"{market.lower()}{symbol}"
        params = {
            "param": f"{code},day,{start_date.strftime('%Y-%m-%d')},{date.today().strftime('%Y-%m-%d')},1000,{self.adjust}"
        }
        payload = self._get_json(TENCENT_KLINE_URL, params)
        symbol_data = ((payload or {}).get("data") or {}).get(code) or {}
        key = f"{self.adjust}day"
        raw_rows = symbol_data.get(key) or symbol_data.get("day") or []
        rows = []
        prev_close = None
        for item in raw_rows:
            if len(item) < 6:
                continue
            dt = pd.to_datetime(item[0])
            open_price = float(item[1])
            close_price = float(item[2])
            high_price = float(item[3])
            low_price = float(item[4])
            volume_lots = float(item[5])
            volume_shares = volume_lots * 100.0
            amount = close_price * volume_shares
            pct_chg = ((close_price / prev_close) - 1) * 100 if prev_close else 0.0
            rows.append(
                {
                    "date": dt,
                    "open": open_price,
                    "close": close_price,
                    "high": high_price,
                    "low": low_price,
                    "volume": volume_shares,
                    "amount": amount,
                    "amplitude": ((high_price - low_price) / prev_close * 100) if prev_close else 0.0,
                    "pct_chg": pct_chg,
                    "chg": close_price - prev_close if prev_close else 0.0,
                    "turnover": 0.0,
                    "symbol": symbol,
                    "market": market,
                }
            )
            prev_close = close_price
        return pd.DataFrame(rows)

    @staticmethod
    def _fmt_date(value: date) -> str:
        return value.strftime("%Y%m%d")

    @staticmethod
    def _secid(symbol: str, market: str) -> str:
        return f"{1 if market == 'SH' else 0}.{symbol}"
