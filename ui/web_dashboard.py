"""
ui/web_dashboard.py - Web dashboard server for the crypto trading bot.
Run standalone: python -m ui.web_dashboard
Or import and call start_dashboard_server() from main.
"""
from __future__ import annotations

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import threading
from pathlib import Path
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

# ── Path setup so we can import project modules ─────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from data.database import (
    get_portfolio_history,
    get_positions,
    get_recent_trades,
    get_trade_stats,
    init_db,
)

app = FastAPI(title="Crypto Bot Dashboard")

# Shared state written by the bot, read by the dashboard
_state: Dict[str, Any] = {
    "prices": {},
    "portfolio": {},
    "last_decision": {},
    "risk_summary": {},
    "perf_metrics": {},
    "round": 0,
}
_clients: list[WebSocket] = []


def update_state(**kwargs) -> None:
    """Called by the bot to push fresh data to the dashboard."""
    _state.update(kwargs)


# ── WebSocket broadcast ──────────────────────────────────────────────────────

async def _broadcast(data: dict) -> None:
    dead = []
    for ws in _clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _clients.remove(ws)


async def _push_loop() -> None:
    """Push fresh data to all connected browsers every 2 s."""
    while True:
        await asyncio.sleep(2)
        try:
            history = get_portfolio_history(limit=200)
            trades = get_recent_trades(limit=50)
            positions = get_positions()
            stats = get_trade_stats()

            payload = {
                "type": "update",
                "prices": _state.get("prices", {}),
                "portfolio": _state.get("portfolio", {}),
                "last_decision": _state.get("last_decision", {}),
                "risk_summary": _state.get("risk_summary", {}),
                "perf_metrics": _state.get("perf_metrics", {}),
                "round": _state.get("round", 0),
                "history": list(reversed(history)),
                "trades": trades,
                "positions": positions,
                "stats": stats,
            }
            await _broadcast(payload)
        except Exception:
            pass


@app.on_event("startup")
async def startup() -> None:
    init_db()
    asyncio.create_task(_push_loop())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.append(ws)
    try:
        while True:
            await ws.receive_text()   # keep-alive ping
    except WebSocketDisconnect:
        if ws in _clients:
            _clients.remove(ws)


# ── HTML page ────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crypto Trading Bot</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #0d1117; --surface: #161b22; --border: #30363d;
    --text: #e6edf3; --dim: #8b949e; --green: #3fb950;
    --red: #f85149; --yellow: #d29922; --blue: #58a6ff;
    --purple: #bc8cff; --orange: #ffa657;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', monospace; font-size: 13px; }
  a { color: var(--blue); }

  /* Layout */
  #app { display: grid; grid-template-rows: 56px 1fr; min-height: 100vh; }
  header {
    background: var(--surface); border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 16px; padding: 0 20px;
    position: sticky; top: 0; z-index: 10;
  }
  header h1 { font-size: 15px; font-weight: 700; letter-spacing: 1px; color: var(--blue); }
  .badge { padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge.paper { background: #2d333b; color: var(--yellow); border: 1px solid var(--yellow); }
  .badge.live  { background: #3d1a1a; color: var(--red); border: 1px solid var(--red); }
  .badge.active { background: #1a3a1a; color: var(--green); border: 1px solid var(--green); }
  .badge.halted { background: #3d1a1a; color: var(--red); border: 1px solid var(--red); }
  #clock { margin-left: auto; color: var(--dim); font-size: 12px; }
  #conn-status { width: 8px; height: 8px; border-radius: 50%; background: var(--red); }
  #conn-status.ok { background: var(--green); }

  main { padding: 16px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; }

  /* KPI bar */
  .kpi-bar { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
  .kpi {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 16px;
  }
  .kpi .label { color: var(--dim); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }
  .kpi .value { font-size: 22px; font-weight: 700; margin-top: 4px; }
  .kpi .sub   { color: var(--dim); font-size: 11px; margin-top: 2px; }
  .pos { color: var(--green); } .neg { color: var(--red); } .neu { color: var(--text); }

  /* Grid of panels */
  .panels { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .panels.three { grid-template-columns: 2fr 1fr; }
  @media (max-width: 900px) { .panels, .panels.three { grid-template-columns: 1fr; } }

  .panel {
    background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
    overflow: hidden;
  }
  .panel-title {
    padding: 10px 16px; font-size: 12px; font-weight: 600; text-transform: uppercase;
    letter-spacing: .5px; border-bottom: 1px solid var(--border); color: var(--dim);
    display: flex; align-items: center; gap: 8px;
  }
  .panel-body { padding: 0; }

  /* Chart */
  .chart-wrap { padding: 12px 16px; position: relative; height: 220px; }

  /* Tables */
  table { width: 100%; border-collapse: collapse; }
  th { padding: 8px 12px; text-align: left; font-size: 11px; color: var(--dim);
       text-transform: uppercase; border-bottom: 1px solid var(--border); }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1c2128; }
  .mono { font-family: monospace; }

  /* Reasoning box */
  .reasoning {
    padding: 12px 16px; font-size: 12px; line-height: 1.6;
    color: var(--dim); white-space: pre-wrap; max-height: 280px; overflow-y: auto;
    font-family: monospace;
  }
  .reasoning .label { color: var(--blue); font-weight: 600; display: block; margin-bottom: 6px; }

  /* Price cards */
  .price-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; }
  .price-card { padding: 14px 16px; border-right: 1px solid var(--border); }
  .price-card:last-child { border-right: none; }
  .price-card .sym { font-size: 11px; color: var(--dim); }
  .price-card .px  { font-size: 20px; font-weight: 700; margin-top: 2px; }

  /* Positions */
  .pos-side-long  { color: var(--green); font-weight: 600; }
  .pos-side-short { color: var(--red);   font-weight: 600; }

  /* Sparkline */
  .spark { display: inline-block; vertical-align: middle; }

  /* Scrollable trades */
  .scrollable { max-height: 300px; overflow-y: auto; }
  .scrollable::-webkit-scrollbar { width: 4px; }
  .scrollable::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  /* Round indicator */
  #round-badge { color: var(--purple); font-size: 12px; }
</style>
</head>
<body>
<div id="app">

<header>
  <div id="conn-status"></div>
  <h1>CRYPTO TRADING BOT</h1>
  <span id="mode-badge" class="badge paper">PAPER</span>
  <span id="status-badge" class="badge active">ACTIVE</span>
  <span id="strategy-label" style="color:var(--dim);font-size:12px">ai_driven</span>
  <span id="round-badge"></span>
  <span id="clock"></span>
</header>

<main>

  <!-- KPI bar -->
  <div class="kpi-bar">
    <div class="kpi">
      <div class="label">Portfolio Value</div>
      <div class="value neu" id="kpi-total">$10,000.00</div>
      <div class="sub" id="kpi-initial">Started: $10,000.00</div>
    </div>
    <div class="kpi">
      <div class="label">Total P&L</div>
      <div class="value neu" id="kpi-pnl">$0.00</div>
      <div class="sub" id="kpi-pnl-pct">0.00%</div>
    </div>
    <div class="kpi">
      <div class="label">Cash (USDT)</div>
      <div class="value neu" id="kpi-cash">$10,000.00</div>
      <div class="sub">Available margin</div>
    </div>
    <div class="kpi">
      <div class="label">Win Rate</div>
      <div class="value neu" id="kpi-winrate">0%</div>
      <div class="sub" id="kpi-trades">0 trades</div>
    </div>
    <div class="kpi">
      <div class="label">Max Drawdown</div>
      <div class="value neu" id="kpi-drawdown">0.00%</div>
      <div class="sub" id="kpi-sharpe">Sharpe: —</div>
    </div>
    <div class="kpi">
      <div class="label">Daily P&L</div>
      <div class="value neu" id="kpi-daily">$0.00</div>
      <div class="sub" id="kpi-peak">Peak: $10,000.00</div>
    </div>
  </div>

  <!-- Prices row -->
  <div class="panel">
    <div class="panel-title">Live Prices</div>
    <div class="price-grid" id="price-grid">
      <div class="price-card"><div class="sym">BTC/USDT</div><div class="px" id="px-BTC">—</div></div>
      <div class="price-card"><div class="sym">ETH/USDT</div><div class="px" id="px-ETH">—</div></div>
      <div class="price-card"><div class="sym">SOL/USDT</div><div class="px" id="px-SOL">—</div></div>
    </div>
  </div>

  <!-- Chart + AI Decision -->
  <div class="panels three">
    <div class="panel">
      <div class="panel-title">Portfolio Balance Over Time</div>
      <div class="chart-wrap"><canvas id="balanceChart"></canvas></div>
    </div>
    <div class="panel">
      <div class="panel-title">AI Reasoning</div>
      <div class="reasoning" id="reasoning-box">
        <span class="label">Waiting for first Opus decision...</span>
      </div>
    </div>
  </div>

  <!-- Positions + Stats -->
  <div class="panels">
    <div class="panel">
      <div class="panel-title">Open Positions</div>
      <div class="scrollable">
        <table>
          <thead><tr>
            <th>Symbol</th><th>Side</th><th>Entry</th>
            <th>Current</th><th>Amount</th><th>Unreal. P&L</th>
            <th>Stop Loss</th><th>Take Profit</th>
          </tr></thead>
          <tbody id="positions-body"><tr><td colspan="8" style="color:var(--dim);text-align:center;padding:20px">No open positions</td></tr></tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-title">Performance Stats</div>
      <div class="panel-body">
        <table>
          <tbody id="stats-body">
            <tr><td style="color:var(--dim)">Total Trades</td><td class="mono" id="stat-total">0</td></tr>
            <tr><td style="color:var(--dim)">Winning Trades</td><td class="mono" id="stat-wins">0</td></tr>
            <tr><td style="color:var(--dim)">Win Rate</td><td class="mono" id="stat-winrate">0%</td></tr>
            <tr><td style="color:var(--dim)">Avg P&L / Trade</td><td class="mono" id="stat-avg">$0.00</td></tr>
            <tr><td style="color:var(--dim)">Total Realised P&L</td><td class="mono" id="stat-total-pnl">$0.00</td></tr>
            <tr><td style="color:var(--dim)">Sharpe Ratio</td><td class="mono" id="stat-sharpe">—</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Trade log -->
  <div class="panel">
    <div class="panel-title">Trade Log (last 50)</div>
    <div class="scrollable">
      <table>
        <thead><tr>
          <th>Time (UTC)</th><th>Symbol</th><th>Side</th>
          <th>Price</th><th>Amount</th><th>Cost</th>
          <th>P&L</th><th>Confidence</th><th>Reasoning</th>
        </tr></thead>
        <tbody id="trades-body"><tr><td colspan="9" style="color:var(--dim);text-align:center;padding:20px">No trades yet</td></tr></tbody>
      </table>
    </div>
  </div>

</main>
</div>

<script>
// ── Clock ────────────────────────────────────────────────────────────────────
function updateClock() {
  document.getElementById('clock').textContent = new Date().toUTCString().replace(' GMT','') + ' UTC';
}
setInterval(updateClock, 1000); updateClock();

// ── Chart ────────────────────────────────────────────────────────────────────
const ctx = document.getElementById('balanceChart').getContext('2d');
const balanceChart = new Chart(ctx, {
  type: 'line',
  data: { labels: [], datasets: [{
    label: 'Balance (USDT)',
    data: [],
    borderColor: '#58a6ff',
    backgroundColor: 'rgba(88,166,255,0.08)',
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.3,
    fill: true,
  }]},
  options: {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: { legend: { display: false }, tooltip: {
      callbacks: { label: ctx => '$' + ctx.parsed.y.toLocaleString('en-US', {minimumFractionDigits:2,maximumFractionDigits:2}) }
    }},
    scales: {
      x: { ticks: { color:'#8b949e', maxTicksLimit: 8, maxRotation: 0 }, grid: { color:'#21262d' } },
      y: { ticks: { color:'#8b949e', callback: v => '$'+v.toLocaleString() }, grid: { color:'#21262d' } },
    }
  }
});

// ── Helpers ──────────────────────────────────────────────────────────────────
function fmt$(v) { return (v >= 0 ? '+' : '') + '$' + Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtPct(v) { return (v >= 0 ? '+' : '') + (v*100).toFixed(2) + '%'; }
function colorClass(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : 'neu'; }
function setKpi(id, val, cls) {
  const el = document.getElementById(id);
  el.textContent = val;
  el.className = 'value ' + (cls || 'neu');
}

// ── WebSocket ────────────────────────────────────────────────────────────────
let ws, reconnectTimer;

function connect() {
  ws = new WebSocket('ws://' + location.host + '/ws');

  ws.onopen = () => {
    document.getElementById('conn-status').className = 'ok';
    clearInterval(reconnectTimer);
    // send pings
    setInterval(() => ws.readyState === 1 && ws.send('ping'), 5000);
  };

  ws.onclose = () => {
    document.getElementById('conn-status').className = '';
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onmessage = (ev) => {
    const d = JSON.parse(ev.data);
    if (d.type === 'update') render(d);
  };
}

// ── Render ───────────────────────────────────────────────────────────────────
function render(d) {
  // --- Prices ---
  const prices = d.prices || {};
  for (const [sym, px] of Object.entries(prices)) {
    const key = sym.split('/')[0];
    const el = document.getElementById('px-' + key);
    if (el) el.textContent = '$' + parseFloat(px).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
  }

  // --- Portfolio KPIs ---
  const p = d.portfolio || {};
  const total = p.total_value || 10000;
  const initial = p.initial_value || 10000;
  const pnl = p.pnl_total || 0;
  const pnlPct = p.pnl_pct || 0;
  const cash = p.cash_balance || 0;
  const drawdown = p.drawdown || 0;
  const peak = p.peak_value || total;
  setKpi('kpi-total', '$' + total.toLocaleString('en-US',{minimumFractionDigits:2}), 'neu');
  document.getElementById('kpi-initial').textContent = 'Started: $' + initial.toLocaleString('en-US',{minimumFractionDigits:2});
  setKpi('kpi-pnl', fmt$(pnl), colorClass(pnl));
  document.getElementById('kpi-pnl-pct').textContent = fmtPct(pnlPct);
  setKpi('kpi-cash', '$' + cash.toLocaleString('en-US',{minimumFractionDigits:2}), 'neu');
  setKpi('kpi-drawdown', (drawdown*100).toFixed(2)+'%', drawdown > 0.05 ? 'neg' : 'pos');
  document.getElementById('kpi-peak').textContent = 'Peak: $' + peak.toLocaleString('en-US',{minimumFractionDigits:2});

  // --- Perf metrics ---
  const pm = d.perf_metrics || {};
  const wr = pm.win_rate || 0;
  setKpi('kpi-winrate', (wr*100).toFixed(1)+'%', wr >= 0.5 ? 'pos' : wr > 0 ? 'neu' : 'neg');
  document.getElementById('kpi-trades').textContent = (pm.total_trades||0) + ' trades';
  document.getElementById('kpi-sharpe').textContent = 'Sharpe: ' + (pm.sharpe_ratio != null ? pm.sharpe_ratio.toFixed(3) : '—');

  // --- Risk ---
  const rs = d.risk_summary || {};
  const daily = rs.daily_pnl || 0;
  setKpi('kpi-daily', fmt$(daily), colorClass(daily));
  const halted = rs.halted || false;
  const sb = document.getElementById('status-badge');
  sb.textContent = halted ? 'HALTED' : 'ACTIVE';
  sb.className = 'badge ' + (halted ? 'halted' : 'active');

  // --- Round ---
  const round = d.round || 0;
  if (round > 0) document.getElementById('round-badge').textContent = 'Round #' + round;

  // --- Balance chart ---
  const history = d.history || [];
  if (history.length > 0) {
    balanceChart.data.labels = history.map(h => h.timestamp ? h.timestamp.slice(11,19) : '');
    balanceChart.data.datasets[0].data = history.map(h => h.total_value);
    // Color line green/red vs start
    const last = history[history.length-1]?.total_value || 10000;
    const first = history[0]?.total_value || 10000;
    balanceChart.data.datasets[0].borderColor = last >= first ? '#3fb950' : '#f85149';
    balanceChart.data.datasets[0].backgroundColor = last >= first ? 'rgba(63,185,80,0.08)' : 'rgba(248,81,73,0.08)';
    balanceChart.update('none');
  }

  // --- Positions ---
  const positions = d.positions || [];
  const pb = document.getElementById('positions-body');
  if (positions.length === 0) {
    pb.innerHTML = '<tr><td colspan="8" style="color:var(--dim);text-align:center;padding:20px">No open positions</td></tr>';
  } else {
    pb.innerHTML = positions.map(pos => {
      const sym = pos.symbol || '';
      const side = (pos.side || '').toUpperCase();
      const entry = pos.entry_price || 0;
      const amt = pos.amount || 0;
      const currPx = prices[sym] || entry;
      const unreal = (currPx - entry) * amt * (side === 'SHORT' ? -1 : 1);
      const sl = pos.stop_loss ? '$' + pos.stop_loss.toLocaleString('en-US',{minimumFractionDigits:2}) : '—';
      const tp = pos.take_profit ? '$' + pos.take_profit.toLocaleString('en-US',{minimumFractionDigits:2}) : '—';
      return `<tr>
        <td class="mono">${sym}</td>
        <td class="${side==='LONG'?'pos-side-long':'pos-side-short'}">${side}</td>
        <td class="mono">$${entry.toLocaleString('en-US',{minimumFractionDigits:2})}</td>
        <td class="mono">$${parseFloat(currPx).toLocaleString('en-US',{minimumFractionDigits:2})}</td>
        <td class="mono">${amt.toFixed(4)}</td>
        <td class="mono ${colorClass(unreal)}">${fmt$(unreal)}</td>
        <td class="mono" style="color:var(--red)">${sl}</td>
        <td class="mono" style="color:var(--green)">${tp}</td>
      </tr>`;
    }).join('');
  }

  // --- Stats ---
  const st = d.stats || {};
  document.getElementById('stat-total').textContent = st.total || 0;
  document.getElementById('stat-wins').textContent = st.wins || 0;
  document.getElementById('stat-winrate').textContent = ((st.win_rate||0)*100).toFixed(1) + '%';
  document.getElementById('stat-avg').innerHTML = '<span class="' + colorClass(st.avg_pnl||0) + '">' + fmt$(st.avg_pnl||0) + '</span>';
  document.getElementById('stat-total-pnl').innerHTML = '<span class="' + colorClass(st.total_pnl||0) + '">' + fmt$(st.total_pnl||0) + '</span>';
  document.getElementById('stat-sharpe').textContent = pm.sharpe_ratio != null ? pm.sharpe_ratio.toFixed(3) : '—';

  // --- AI Reasoning ---
  const dec = d.last_decision || {};
  if (dec.reasoning) {
    const action = (dec.action || 'hold').toUpperCase();
    const conf = dec.confidence ? (dec.confidence*100).toFixed(0)+'%' : '';
    const sym2 = dec.symbol || '';
    const col = action === 'LONG' || action === 'BUY' ? '#3fb950' : action === 'SHORT' || action === 'SELL' ? '#f85149' : '#d29922';
    document.getElementById('reasoning-box').innerHTML =
      `<span class="label" style="color:${col}">${action} ${sym2} ${conf ? '('+conf+' confidence)' : ''}</span>${dec.reasoning}`;
  }

  // --- Trade log ---
  const trades = d.trades || [];
  const tb = document.getElementById('trades-body');
  if (trades.length === 0) {
    tb.innerHTML = '<tr><td colspan="9" style="color:var(--dim);text-align:center;padding:20px">No trades yet</td></tr>';
  } else {
    tb.innerHTML = trades.map(t => {
      const side = (t.side||'').toUpperCase();
      const sideColor = side === 'BUY' || side === 'LONG' ? 'pos' : 'neg';
      const pnl = t.pnl != null ? fmt$(t.pnl) : '—';
      const pnlCls = t.pnl != null ? colorClass(t.pnl) : 'neu';
      const conf = t.confidence ? (t.confidence*100).toFixed(0)+'%' : '—';
      const ts = (t.timestamp||'').slice(0,19).replace('T',' ');
      const reason = t.reasoning || '—';
      return `<tr>
        <td class="mono" style="white-space:nowrap">${ts}</td>
        <td class="mono">${t.symbol||''}</td>
        <td class="${sideColor}" style="font-weight:600">${side}</td>
        <td class="mono">$${(t.price||0).toLocaleString('en-US',{minimumFractionDigits:2})}</td>
        <td class="mono">${(t.amount||0).toFixed(4)}</td>
        <td class="mono">$${(t.cost||0).toLocaleString('en-US',{minimumFractionDigits:2})}</td>
        <td class="mono ${pnlCls}">${pnl}</td>
        <td class="mono">${conf}</td>
        <td style="color:var(--dim);font-size:11px;white-space:pre-wrap;min-width:300px">${reason}</td>
      </tr>`;
    }).join('');
  }
}

connect();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return HTML


# ── Standalone entry point ───────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning")


def start_in_thread(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start dashboard in a background thread (called from main bot)."""
    t = threading.Thread(target=run, args=(host, port), daemon=True)
    t.start()
    print(f"\n  Dashboard: http://localhost:{port}\n")


if __name__ == "__main__":
    init_db()
    print("Dashboard running at http://localhost:8080")
    run()
