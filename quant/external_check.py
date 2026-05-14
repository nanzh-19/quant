from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import time

import pandas as pd
import requests


EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CHECK_COLUMNS = ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_chg", "chg", "turnover"]


@dataclass(frozen=True)
class ExternalCheckConfig:
    sample_size: int = 30
    seed: int = 19
    price_tolerance: float = 0.01
    amount_tolerance: float = 1.0
    percent_tolerance: float = 0.02


def _infer_market(symbol: str) -> str:
    symbol = str(symbol).zfill(6)
    if symbol.startswith(("600", "601", "603", "605", "688", "510", "511", "512", "513", "515", "516", "517", "518", "519", "520", "560", "561", "562", "563", "588", "589")):
        return "SH"
    return "SZ"


def _secid(symbol: str, market: str) -> str:
    return f"{1 if market == 'SH' else 0}.{symbol}"


def _fetch_eastmoney_row(symbol: str, market: str, trade_date: str, adjust: str = "qfq") -> dict:
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101,
        "fqt": {"none": 0, "qfq": 1, "hfq": 2}.get(adjust, 1),
        "secid": _secid(symbol, market),
        "beg": trade_date.replace("-", ""),
        "end": trade_date.replace("-", ""),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    last_error: Exception | None = None
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    for attempt in range(1, 4):
        try:
            response = session.get(EASTMONEY_KLINE_URL, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                payload = _fetch_eastmoney_payload_with_curl(params)
                break
            time.sleep(0.5 * attempt)
    else:
        raise RuntimeError(f"request failed: {last_error}")
    klines = ((payload or {}).get("data") or {}).get("klines") or []
    if not klines:
        return {}
    parts = klines[0].split(",")
    if len(parts) < 11:
        return {}
    return {
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


def _fetch_eastmoney_payload_with_curl(params: dict) -> dict:
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = f"{EASTMONEY_KLINE_URL}?{query}"
    proc = subprocess.run(
        ["curl", "-s", url],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(proc.stdout)


def _load_latest_local_rows(daily_dir: Path) -> pd.DataFrame:
    rows = []
    for path in sorted(daily_dir.glob("*.csv")):
        symbol = path.stem.zfill(6)
        try:
            df = pd.read_csv(path, dtype={"symbol": str, "market": str})
        except Exception:
            continue
        if df.empty or "date" not in df.columns:
            continue
        last = df.sort_values("date").iloc[-1].to_dict()
        last["symbol"] = symbol
        last["market"] = str(last.get("market") or _infer_market(symbol))
        rows.append(last)
    return pd.DataFrame(rows)


def run_external_price_check(
    daily_dir: Path,
    outputs_dir: Path,
    adjust: str = "qfq",
    config: ExternalCheckConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cfg = config or ExternalCheckConfig()
    outputs_dir.mkdir(parents=True, exist_ok=True)
    latest = _load_latest_local_rows(daily_dir)
    if latest.empty:
        detail = pd.DataFrame()
        summary = pd.DataFrame([{"status": "no_local_data", "checked": 0, "passed": 0, "failed": 0}])
        return detail, summary

    latest = latest.dropna(subset=["date"]).copy()
    sample_size = min(cfg.sample_size, len(latest))
    candidate_size = min(max(sample_size * 5, sample_size), len(latest))
    sampled = latest.sample(n=candidate_size, random_state=cfg.seed).reset_index(drop=True)

    rows = []
    for item in sampled.itertuples(index=False):
        symbol = str(item.symbol).zfill(6)
        market = str(getattr(item, "market", "") or _infer_market(symbol))
        trade_date = str(getattr(item, "date"))[:10]
        row = {
            "symbol": symbol,
            "market": market,
            "date": trade_date,
            "status": "pass",
            "error": "",
        }
        try:
            remote = _fetch_eastmoney_row(symbol=symbol, market=market, trade_date=trade_date, adjust=adjust)
        except Exception as exc:
            remote = {}
            row["status"] = "error"
            row["error"] = str(exc)
        if not remote and row["status"] == "pass":
            row["status"] = "missing_remote"

        for column in CHECK_COLUMNS:
            local_value = float(getattr(item, column, 0.0))
            remote_value = float(remote.get(column, float("nan"))) if remote else float("nan")
            diff = abs(local_value - remote_value) if pd.notna(remote_value) else float("nan")
            row[f"local_{column}"] = local_value
            row[f"remote_{column}"] = remote_value
            row[f"diff_{column}"] = diff

        if row["status"] == "pass":
            checks = [
                row["diff_open"] <= cfg.price_tolerance,
                row["diff_close"] <= cfg.price_tolerance,
                row["diff_high"] <= cfg.price_tolerance,
                row["diff_low"] <= cfg.price_tolerance,
                row["diff_amount"] <= cfg.amount_tolerance,
                row["diff_volume"] <= max(1.0, abs(row["remote_volume"]) * 0.0001),
                row["diff_pct_chg"] <= cfg.percent_tolerance,
                row["diff_amplitude"] <= cfg.percent_tolerance,
            ]
            if not all(checks):
                row["status"] = "mismatch"
        rows.append(row)
        checked = sum(1 for item in rows if item["status"] in {"pass", "mismatch", "missing_remote"})
        if checked >= sample_size:
            break

    detail = pd.DataFrame(rows)
    comparable = detail[detail["status"].isin(["pass", "mismatch", "missing_remote"])].copy() if not detail.empty else pd.DataFrame()
    passed = int((comparable["status"] == "pass").sum()) if not comparable.empty else 0
    failed = int((comparable["status"] != "pass").sum()) if not comparable.empty else 0
    errors = int((detail["status"] == "error").sum()) if not detail.empty else 0
    summary = pd.DataFrame(
        [
            {
                "status": "pass" if len(comparable) >= sample_size and failed == 0 else "fail",
                "checked": len(comparable),
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "adjust": adjust,
                "sample_size": sample_size,
                "candidates": len(detail),
                "seed": cfg.seed,
            }
        ]
    )
    detail.to_csv(outputs_dir / "external_price_check.csv", index=False)
    summary.to_csv(outputs_dir / "external_price_check_summary.csv", index=False)
    return detail, summary
