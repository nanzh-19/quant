from __future__ import annotations

import html
import json
import sqlite3
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pandas as pd

if TYPE_CHECKING:
    from quant.services.app import AppContext


def _read_csv(path: Path, **kwargs) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, **kwargs)
    except Exception:
        return pd.DataFrame()


def _json_response(handler: BaseHTTPRequestHandler, payload: dict | list, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text_response(handler: BaseHTTPRequestHandler, text: str, status: int = 200) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _load_symbol_catalog(ctx: "AppContext") -> pd.DataFrame:
    universe = ctx.storage.read_universe()
    if universe.empty:
        return pd.DataFrame(columns=["symbol", "name", "asset_type", "market"])
    universe["symbol"] = universe["symbol"].astype(str).str.zfill(6)
    inventory = _read_csv(ctx.storage.outputs_dir / "data_inventory_detail.csv", dtype={"symbol": str})
    if not inventory.empty:
        inventory["symbol"] = inventory["symbol"].astype(str).str.zfill(6)
    merged = universe.merge(
        inventory[[col for col in ["symbol", "rows", "start_date", "end_date", "updated_at"] if col in inventory.columns]],
        on="symbol",
        how="left",
    )
    merged["rows"] = merged["rows"].fillna(0).astype(int)
    merged["start_date"] = merged["start_date"].fillna("")
    merged["end_date"] = merged["end_date"].fillna("")
    merged["updated_at"] = merged["updated_at"].fillna("")
    return merged.sort_values(["asset_type", "market", "symbol"]).reset_index(drop=True)


def _latest_updates(metadata_db: Path) -> pd.DataFrame:
    if not metadata_db.exists():
        return pd.DataFrame()
    with sqlite3.connect(metadata_db) as conn:
        return pd.read_sql_query(
            "select run_at, scope, symbols, rows_written, note from update_log order by rowid desc limit 10",
            conn,
        )


def _build_index_html(ctx: "AppContext") -> str:
    outputs_dir = ctx.storage.outputs_dir
    inventory_df = _read_csv(outputs_dir / "data_inventory_summary.csv")
    backtest_df = _read_csv(outputs_dir / "backtest_summary.csv")
    ranking_df = _read_csv(outputs_dir / "latest_ranking.csv", dtype={"symbol": str})
    stale_df = _read_csv(outputs_dir / "stale_symbols.csv", dtype={"symbol": str})
    failures_df = _read_csv(outputs_dir / "fast_update_failures.csv", dtype={"symbol": str})
    updates_df = _latest_updates(ctx.storage.metadata_db)
    catalog = _load_symbol_catalog(ctx)

    total_symbols = len(catalog)
    stock_count = int((catalog["asset_type"] == "stock").sum()) if not catalog.empty else 0
    etf_count = int((catalog["asset_type"] == "etf").sum()) if not catalog.empty else 0
    latest_date = ""
    if not inventory_df.empty and "max_end_date" in inventory_df.columns:
        latest_date = str(inventory_df["max_end_date"].max())

    cards = [
        ("最新数据日期", latest_date or "-"),
        ("总标的数", str(total_symbols)),
        ("股票数", str(stock_count)),
        ("ETF数", str(etf_count)),
        ("更新失败", str(len(failures_df))),
        ("待补齐标的", str(len(stale_df))),
    ]
    cards_html = "".join(
        f"<div class='card'><div class='label'>{html.escape(label)}</div><div class='value'>{html.escape(value)}</div></div>"
        for label, value in cards
    )

    top_rank_rows = ""
    if not ranking_df.empty:
        preview = ranking_df.head(10)[[col for col in ["symbol", "name", "asset_type", "date", "score"] if col in ranking_df.columns]]
        top_rank_rows = preview.to_html(index=False, classes="table", border=0)

    update_rows = updates_df.to_html(index=False, classes="table", border=0) if not updates_df.empty else "<p class='muted'>No update logs.</p>"
    backtest_rows = backtest_df.to_html(index=False, classes="table", border=0) if not backtest_df.empty else "<p class='muted'>No backtest summary.</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A-Share Quant Dashboard</title>
  <style>
    :root {{
      --bg: #f5f1e8;
      --panel: #fffdf8;
      --ink: #1f2a30;
      --muted: #6d7478;
      --line: #ddd3c2;
      --accent: #9d311f;
      --accent-2: #314851;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      font-family: "Source Han Sans SC", "Noto Sans SC", "PingFang SC", sans-serif;
      background:
        radial-gradient(circle at top left, #fff6df 0, transparent 28%),
        linear-gradient(180deg, #efe7d7 0%, var(--bg) 44%, #ece6dc 100%);
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    .wrap {{ width: min(1360px, calc(100vw - 24px)); margin: 18px auto 40px; }}
    .hero {{
      padding: 26px 28px;
      border-radius: 24px;
      background: linear-gradient(135deg, rgba(157,49,31,.96), rgba(49,72,81,.96));
      color: #fff;
      box-shadow: 0 18px 48px rgba(40, 26, 10, 0.14);
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 34px; }}
    .hero p {{ margin: 0; line-height: 1.6; max-width: 960px; }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .card, .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 10px 24px rgba(46, 41, 31, 0.06);
    }}
    .card {{ padding: 16px 18px; }}
    .card .label {{ color: var(--muted); font-size: 13px; }}
    .card .value {{ margin-top: 8px; font-size: 30px; font-weight: 700; }}
    .grid {{
      display: grid;
      grid-template-columns: 1.1fr .9fr;
      gap: 14px;
    }}
    .panel {{ padding: 18px 20px; margin-bottom: 14px; }}
    .panel h2 {{ margin: 0 0 12px; font-size: 20px; }}
    .muted {{ color: var(--muted); }}
    .toolbar {{
      display: grid;
      grid-template-columns: 1.4fr 0.8fr 0.8fr 0.6fr;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .toolbar input, .toolbar select {{
      width: 100%;
      padding: 11px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: #fff;
      font-size: 14px;
    }}
    .list-wrap {{
      border: 1px solid var(--line);
      border-radius: 16px;
      overflow: hidden;
      background: #fff;
    }}
    .list-meta {{
      display: flex;
      justify-content: space-between;
      padding: 12px 14px;
      border-bottom: 1px solid #ece4d8;
      font-size: 13px;
      color: var(--muted);
      background: #faf6ee;
    }}
    .table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    .table th, .table td {{
      border-bottom: 1px solid #efe8db;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }}
    .table th {{ background: #f7f2e7; }}
    #symbol-list tr {{ cursor: pointer; }}
    #symbol-list tr:hover {{ background: #fff7ef; }}
    .detail-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: baseline;
      flex-wrap: wrap;
    }}
    .detail-title {{ font-size: 24px; font-weight: 700; }}
    .detail-sub {{ color: var(--muted); font-size: 14px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
      gap: 10px;
      margin: 14px 0;
    }}
    .stat {{
      padding: 10px 12px;
      border-radius: 14px;
      background: #fbf7ef;
      border: 1px solid #ece2d1;
    }}
    .stat .label {{ color: var(--muted); font-size: 12px; }}
    .stat .value {{ margin-top: 4px; font-weight: 700; font-size: 18px; }}
    .chart-toolbar {{
      display: flex;
      gap: 8px;
      margin: 12px 0 10px;
      flex-wrap: wrap;
    }}
    .chart-toolbar button {{
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid #d8ccb8;
      background: #fff;
      cursor: pointer;
      font-weight: 600;
    }}
    .chart-toolbar button.active {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
    .chart-box {{
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 10px;
    }}
    canvas {{
      width: 100%;
      height: 520px;
      display: block;
      border-radius: 12px;
      background:
        linear-gradient(180deg, rgba(255,255,255,.98), rgba(249,246,239,.98));
    }}
    .tips {{
      color: var(--muted);
      font-size: 13px;
      margin-top: 10px;
    }}
    @media (max-width: 980px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      canvas {{ height: 420px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>A-Share Quant Dashboard</h1>
      <p>现在这个页面不只是摘要页，还可以直接浏览股票和 ETF 数据。左侧是标的列表，右侧可以看单个标的的 K 线图，支持拖动和平移、滚轮缩放、切换显示区间。</p>
    </section>

    <section class="cards">{cards_html}</section>

    <section class="grid">
      <div>
        <div class="panel">
          <h2>标的列表</h2>
          <div class="toolbar">
            <input id="search-input" placeholder="输入代码或名称搜索，例如 510300 / 浦发银行">
            <select id="asset-filter">
              <option value="">全部资产</option>
              <option value="stock">股票</option>
              <option value="etf">ETF</option>
            </select>
            <select id="market-filter">
              <option value="">全部市场</option>
              <option value="SH">沪市</option>
              <option value="SZ">深市</option>
            </select>
            <select id="sort-filter">
              <option value="symbol">按代码</option>
              <option value="rows">按数据量</option>
              <option value="end_date">按最新日期</option>
            </select>
          </div>
          <div class="list-wrap">
            <div class="list-meta">
              <span id="list-summary">加载中...</span>
              <span>点击任一行查看详情</span>
            </div>
            <table class="table">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>资产</th>
                  <th>市场</th>
                  <th>记录数</th>
                  <th>起始</th>
                  <th>最新</th>
                </tr>
              </thead>
              <tbody id="symbol-list"></tbody>
            </table>
          </div>
        </div>
        <div class="panel">
          <h2>Update Log</h2>
          {update_rows}
        </div>
      </div>

      <div>
        <div class="panel">
          <div class="detail-head">
            <div>
              <div class="detail-title" id="detail-title">请选择一个标的</div>
              <div class="detail-sub" id="detail-sub">左侧点击代码或名称加载详情</div>
            </div>
            <div class="detail-sub" id="detail-asof"></div>
          </div>
          <div class="stats" id="detail-stats"></div>
          <div class="chart-toolbar">
            <button type="button" data-range="120">120日</button>
            <button type="button" data-range="250" class="active">250日</button>
            <button type="button" data-range="500">500日</button>
            <button type="button" data-range="0">全量</button>
          </div>
          <div class="chart-box">
            <canvas id="kline-canvas" width="900" height="520"></canvas>
          </div>
          <div class="tips">
            鼠标拖动可平移，滚轮可缩放；双击图表可重置显示窗口。
          </div>
        </div>
        <div class="panel">
          <h2>Top Ranking</h2>
          {top_rank_rows if top_rank_rows else "<p class='muted'>No ranking yet.</p>"}
        </div>
        <div class="panel">
          <h2>Backtest Summary</h2>
          {backtest_rows}
        </div>
      </div>
    </section>
  </div>

  <script>
    const state = {{
      catalog: [],
      filtered: [],
      selectedSymbol: '',
      selectedMeta: null,
      candles: [],
      visibleStart: 0,
      visibleEnd: 0,
      dragging: false,
      dragStartX: 0,
      dragStartStart: 0,
      rangeDays: 250,
    }};

    const canvas = document.getElementById('kline-canvas');
    const ctx = canvas.getContext('2d');
    const searchInput = document.getElementById('search-input');
    const assetFilter = document.getElementById('asset-filter');
    const marketFilter = document.getElementById('market-filter');
    const sortFilter = document.getElementById('sort-filter');
    const listBody = document.getElementById('symbol-list');
    const listSummary = document.getElementById('list-summary');
    const detailTitle = document.getElementById('detail-title');
    const detailSub = document.getElementById('detail-sub');
    const detailAsof = document.getElementById('detail-asof');
    const detailStats = document.getElementById('detail-stats');
    const rangeButtons = Array.from(document.querySelectorAll('[data-range]'));
    const pageParams = new URLSearchParams(window.location.search);

    function buildApiUrl(path, params = {{}}) {{
      const url = new URL(path, window.location.href);
      const search = new URLSearchParams();
      const token = pageParams.get('token');
      if (token) {{
        search.set('token', token);
      }}
      Object.entries(params).forEach(([key, value]) => {{
        if (value !== undefined && value !== null && value !== '') {{
          search.set(key, String(value));
        }}
      }});
      url.search = search.toString();
      return url.toString();
    }}

    async function fetchJson(path, params = {{}}) {{
      const resp = await fetch(buildApiUrl(path, params));
      if (!resp.ok) {{
        const text = await resp.text();
        throw new Error(text || ('HTTP ' + resp.status));
      }}
      return await resp.json();
    }}

    async function loadCatalog() {{
      try {{
        const payload = await fetchJson('./api/symbols');
        state.catalog = payload.items || [];
        applyFilters();
        if (state.filtered.length) {{
          selectSymbol(state.filtered[0].symbol);
        }} else {{
          listSummary.textContent = '没有可展示的标的';
        }}
      }} catch (error) {{
        listSummary.textContent = '列表加载失败';
        detailSub.textContent = String(error.message || error);
      }}
    }}

    function applyFilters() {{
      const keyword = searchInput.value.trim().toLowerCase();
      const asset = assetFilter.value;
      const market = marketFilter.value;
      const sortBy = sortFilter.value;
      let rows = state.catalog.filter(item => {{
        if (asset && item.asset_type !== asset) return false;
        if (market && item.market !== market) return false;
        if (!keyword) return true;
        return item.symbol.toLowerCase().includes(keyword) || item.name.toLowerCase().includes(keyword);
      }});
      if (sortBy === 'rows') {{
        rows = rows.sort((a, b) => (b.rows || 0) - (a.rows || 0) || a.symbol.localeCompare(b.symbol));
      }} else if (sortBy === 'end_date') {{
        rows = rows.sort((a, b) => (b.end_date || '').localeCompare(a.end_date || '') || a.symbol.localeCompare(b.symbol));
      }} else {{
        rows = rows.sort((a, b) => a.symbol.localeCompare(b.symbol));
      }}
      state.filtered = rows;
      renderList();
    }}

    function renderList() {{
      listSummary.textContent = '当前 ' + state.filtered.length + ' / ' + state.catalog.length + ' 个标的';
      listBody.innerHTML = state.filtered.slice(0, 300).map(item => (
        '<tr data-symbol="' + item.symbol + '" class="' + (item.symbol === state.selectedSymbol ? 'active' : '') + '">' +
          '<td>' + item.symbol + '</td>' +
          '<td>' + (item.name || '') + '</td>' +
          '<td>' + (item.asset_type || '') + '</td>' +
          '<td>' + (item.market || '') + '</td>' +
          '<td>' + (item.rows || 0) + '</td>' +
          '<td>' + (item.start_date || '') + '</td>' +
          '<td>' + (item.end_date || '') + '</td>' +
        '</tr>'
      )).join('');
      listBody.querySelectorAll('tr').forEach(row => {{
        row.addEventListener('click', () => selectSymbol(row.dataset.symbol));
      }});
    }}

    async function selectSymbol(symbol) {{
      state.selectedSymbol = symbol;
      state.selectedMeta = state.catalog.find(item => item.symbol === symbol) || null;
      renderList();
      const range = state.rangeDays;
      try {{
        const payload = await fetchJson('./api/candles', {{ symbol }});
        state.candles = payload.candles || [];
        state.rangeDays = range;
        setVisibleRange(range);
        renderDetail(payload.meta || {{}});
        drawChart();
      }} catch (error) {{
        state.candles = [];
        renderDetail({{}});
        detailAsof.textContent = '';
        detailStats.innerHTML = '<div class="stat"><div class="label">状态</div><div class="value">加载失败</div></div>';
        detailSub.textContent = String(error.message || error);
        drawChart();
      }}
    }}

    function setVisibleRange(days) {{
      state.rangeDays = days;
      rangeButtons.forEach(btn => btn.classList.toggle('active', Number(btn.dataset.range) === days));
      if (!state.candles.length) {{
        state.visibleStart = 0;
        state.visibleEnd = 0;
        return;
      }}
      const total = state.candles.length;
      const visible = days > 0 ? Math.min(days, total) : total;
      state.visibleEnd = total;
      state.visibleStart = Math.max(0, total - visible);
    }}

    function renderDetail(meta) {{
      if (!state.selectedMeta) return;
      detailTitle.textContent = (state.selectedMeta.name || '') + ' (' + state.selectedMeta.symbol + ')';
      detailSub.textContent = (state.selectedMeta.asset_type || '') + ' / ' + (state.selectedMeta.market || '');
      detailAsof.textContent = meta.end_date ? ('数据截止: ' + meta.end_date) : '';
      const items = [
        ['记录数', meta.rows || state.selectedMeta.rows || 0],
        ['起始日期', meta.start_date || state.selectedMeta.start_date || '-'],
        ['最新日期', meta.end_date || state.selectedMeta.end_date || '-'],
        ['最新收盘', meta.last_close ?? '-'],
        ['20日涨幅', meta.ret_20 ?? '-'],
        ['60日涨幅', meta.ret_60 ?? '-'],
      ];
      detailStats.innerHTML = items.map(([label, value]) => (
        '<div class="stat">' +
          '<div class="label">' + label + '</div>' +
          '<div class="value">' + value + '</div>' +
        '</div>'
      )).join('');
    }}

    function drawChart() {{
      const width = canvas.width;
      const height = canvas.height;
      ctx.clearRect(0, 0, width, height);

      if (!state.candles.length) {{
        ctx.fillStyle = '#6d7478';
        ctx.font = '16px sans-serif';
        ctx.fillText('No candle data', 20, 30);
        return;
      }}

      const candles = state.candles.slice(state.visibleStart, state.visibleEnd);
      if (!candles.length) return;

      const padding = {{ left: 56, right: 20, top: 20, bottom: 90 }};
      const priceHeight = height - padding.top - padding.bottom;
      const minPrice = Math.min(...candles.map(d => d.low));
      const maxPrice = Math.max(...candles.map(d => d.high));
      const priceRange = Math.max(maxPrice - minPrice, 0.01);
      const candleWidth = Math.max((width - padding.left - padding.right) / Math.max(candles.length, 1) * 0.65, 1);

      ctx.strokeStyle = '#d8cfbf';
      ctx.lineWidth = 1;
      for (let i = 0; i < 5; i++) {{
        const y = padding.top + (priceHeight / 4) * i;
        ctx.beginPath();
        ctx.moveTo(padding.left, y);
        ctx.lineTo(width - padding.right, y);
        ctx.stroke();
      }}

      function priceToY(price) {{
        return padding.top + (maxPrice - price) / priceRange * priceHeight;
      }}

      candles.forEach((d, idx) => {{
        const x = padding.left + ((width - padding.left - padding.right) / candles.length) * idx + 2;
        const color = d.close >= d.open ? '#c84a3c' : '#2f8f5b';
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(x + candleWidth / 2, priceToY(d.low));
        ctx.lineTo(x + candleWidth / 2, priceToY(d.high));
        ctx.stroke();
        const y = priceToY(Math.max(d.open, d.close));
        const bodyY = priceToY(Math.min(d.open, d.close));
        const bodyH = Math.max(Math.abs(y - bodyY), 1);
        ctx.fillRect(x, y, candleWidth, bodyH);
      }});

      const ma20 = candles.filter(d => d.ma20 != null);
      const ma60 = candles.filter(d => d.ma60 != null);
      drawLine(ma20, '#2b6cb0', candleWidth, priceToY, candles);
      drawLine(ma60, '#d97706', candleWidth, priceToY, candles);

      ctx.fillStyle = '#4a545b';
      ctx.font = '12px sans-serif';
      for (let i = 0; i < 5; i++) {{
        const price = maxPrice - priceRange / 4 * i;
        const y = padding.top + (priceHeight / 4) * i;
        ctx.fillText(price.toFixed(2), 6, y + 4);
      }}

      const step = Math.max(1, Math.floor(candles.length / 6));
      for (let i = 0; i < candles.length; i += step) {{
        const x = padding.left + ((width - padding.left - padding.right) / candles.length) * i;
        const label = candles[i].date;
        ctx.save();
        ctx.translate(x, height - 18);
        ctx.rotate(-Math.PI / 5);
        ctx.fillText(label, 0, 0);
        ctx.restore();
      }}
    }}

    function drawLine(rows, color, candleWidth, priceToY, visibleCandles) {{
      if (rows.length < 2) return;
      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      rows.forEach((row, idx) => {{
        const visibleIdx = visibleCandles.findIndex(item => item.date === row.date);
        if (visibleIdx < 0) return;
        const x = 56 + ((canvas.width - 56 - 20) / visibleCandles.length) * visibleIdx + candleWidth / 2 + 2;
        const y = priceToY(color === '#2b6cb0' ? row.ma20 : row.ma60);
        if (idx === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }});
      ctx.stroke();
    }}

    canvas.addEventListener('wheel', (event) => {{
      event.preventDefault();
      if (!state.candles.length) return;
      const total = state.candles.length;
      const current = state.visibleEnd - state.visibleStart;
      const delta = event.deltaY > 0 ? 20 : -20;
      const next = Math.min(total, Math.max(30, current + delta));
      const centerRatio = event.offsetX / canvas.width;
      const centerIndex = state.visibleStart + current * centerRatio;
      state.visibleStart = Math.max(0, Math.round(centerIndex - next * centerRatio));
      state.visibleEnd = Math.min(total, state.visibleStart + next);
      state.visibleStart = Math.max(0, state.visibleEnd - next);
      drawChart();
    }}, {{ passive: false }});

    canvas.addEventListener('mousedown', (event) => {{
      state.dragging = true;
      state.dragStartX = event.clientX;
      state.dragStartStart = state.visibleStart;
    }});

    window.addEventListener('mouseup', () => {{
      state.dragging = false;
    }});

    window.addEventListener('mousemove', (event) => {{
      if (!state.dragging || !state.candles.length) return;
      const visible = state.visibleEnd - state.visibleStart;
      const pxPerItem = canvas.width / Math.max(visible, 1);
      const deltaItems = Math.round((state.dragStartX - event.clientX) / pxPerItem);
      let start = state.dragStartStart + deltaItems;
      start = Math.max(0, Math.min(start, state.candles.length - visible));
      state.visibleStart = start;
      state.visibleEnd = start + visible;
      drawChart();
    }});

    canvas.addEventListener('dblclick', () => {{
      setVisibleRange(state.rangeDays);
      drawChart();
    }});

    rangeButtons.forEach(btn => {{
      btn.addEventListener('click', () => {{
        setVisibleRange(Number(btn.dataset.range));
        drawChart();
      }});
    }});

    [searchInput, assetFilter, marketFilter, sortFilter].forEach(el => {{
      el.addEventListener('input', applyFilters);
      el.addEventListener('change', applyFilters);
    }});

    loadCatalog();
  </script>
</body>
</html>"""


def build_dashboard(ctx: "AppContext") -> Path:
    dashboard_dir = ctx.storage.outputs_dir / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    output_path = dashboard_dir / "index.html"
    output_path.write_text(_build_index_html(ctx), encoding="utf-8")
    return output_path


class DashboardHandler(BaseHTTPRequestHandler):
    def _authorized(self) -> bool:
        if not self.server.access_token:
            return True
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query)
        token = query.get("token", [""])[0]
        return token == self.server.access_token

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            _text_response(self, "Not Found\n", status=404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if not self._authorized():
            _text_response(self, "Forbidden: missing or invalid dashboard token.\n", status=403)
            return

        parsed = urlsplit(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._send_file(self.server.dashboard_html_path, "text/html; charset=utf-8")
            return

        if parsed.path == "/api/symbols":
            catalog = _load_symbol_catalog(self.server.ctx)
            items = catalog[["symbol", "name", "asset_type", "market", "rows", "start_date", "end_date", "updated_at"]].to_dict(orient="records")
            _json_response(self, {"items": items})
            return

        if parsed.path == "/api/candles":
            query = parse_qs(parsed.query)
            symbol = str(query.get("symbol", [""])[0]).zfill(6)
            if not symbol.strip():
                _json_response(self, {"error": "missing symbol"}, status=400)
                return
            df = self.server.ctx.storage.read_symbol(symbol)
            if df.empty:
                _json_response(self, {"error": "symbol not found"}, status=404)
                return
            frame = df.copy().sort_values("date").reset_index(drop=True)
            frame["date"] = pd.to_datetime(frame["date"])
            frame["ma20"] = frame["close"].rolling(20).mean()
            frame["ma60"] = frame["close"].rolling(60).mean()
            frame["ret_20"] = frame["close"].pct_change(20)
            frame["ret_60"] = frame["close"].pct_change(60)
            meta = {
                "symbol": symbol,
                "rows": len(frame),
                "start_date": frame["date"].min().strftime("%Y-%m-%d"),
                "end_date": frame["date"].max().strftime("%Y-%m-%d"),
                "last_close": round(float(frame["close"].iloc[-1]), 3),
                "ret_20": round(float(frame["ret_20"].iloc[-1]), 4) if pd.notna(frame["ret_20"].iloc[-1]) else None,
                "ret_60": round(float(frame["ret_60"].iloc[-1]), 4) if pd.notna(frame["ret_60"].iloc[-1]) else None,
            }
            candles = [
                {
                    "date": row.date.strftime("%Y-%m-%d"),
                    "open": float(row.open),
                    "close": float(row.close),
                    "high": float(row.high),
                    "low": float(row.low),
                    "volume": float(row.volume),
                    "amount": float(row.amount),
                    "ma20": None if pd.isna(row.ma20) else float(row.ma20),
                    "ma60": None if pd.isna(row.ma60) else float(row.ma60),
                }
                for row in frame.itertuples(index=False)
            ]
            _json_response(self, {"meta": meta, "candles": candles})
            return

        if parsed.path.startswith("/files/"):
            relative = parsed.path.removeprefix("/files/")
            safe_path = (self.server.ctx.storage.outputs_dir / relative).resolve()
            if self.server.ctx.storage.outputs_dir.resolve() not in safe_path.parents and safe_path != self.server.ctx.storage.outputs_dir.resolve():
                _text_response(self, "Forbidden\n", status=403)
                return
            content_type = "text/plain; charset=utf-8"
            if safe_path.suffix == ".csv":
                content_type = "text/csv; charset=utf-8"
            elif safe_path.suffix == ".html":
                content_type = "text/html; charset=utf-8"
            self._send_file(safe_path, content_type)
            return

        _text_response(self, "Not Found\n", status=404)


def candidate_dashboard_urls(host: str, port: int, access_token: str = "") -> list[str]:
    path = "/index.html"
    suffix = f"?token={access_token}" if access_token else ""
    if host not in {"0.0.0.0", "::"}:
        return [f"http://{host}:{port}{path}{suffix}"]

    urls = [f"http://127.0.0.1:{port}{path}{suffix}"]
    try:
        import socket

        addresses = {
            addr[4][0]
            for addr in socket.getaddrinfo(socket.gethostname(), None, family=socket.AF_INET)
            if addr[4]
        }
    except Exception:
        addresses = set()
    for addr in sorted(addresses):
        try:
            ip = ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback:
            continue
        urls.append(f"http://{addr}:{port}{path}{suffix}")
    return urls


def serve_dashboard(ctx: "AppContext", directory: Path, host: str = "127.0.0.1", port: int = 8000, access_token: str = "") -> None:
    dashboard_html_path = build_dashboard(ctx)
    server = ThreadingHTTPServer((host, port), DashboardHandler)
    server.ctx = ctx
    server.access_token = access_token
    server.dashboard_html_path = dashboard_html_path
    for url in candidate_dashboard_urls(host=host, port=port, access_token=access_token):
        print(f"Dashboard serving at {url}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
