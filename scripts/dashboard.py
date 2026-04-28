"""
scripts/dashboard.py — HTML backtesting dashboard from trades_log.json.

Usage:
  python scripts/dashboard.py           # generate reports/dashboard.html
  python scripts/dashboard.py --open    # generate and open in browser
"""
from __future__ import annotations

import argparse
import json
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

TRADES_LOG = Path(__file__).resolve().parents[1] / "data" / "trades_log.json"
OUT_FILE   = Path(__file__).resolve().parents[1] / "reports" / "dashboard.html"


def load_trades() -> list:
    return json.loads(TRADES_LOG.read_text(encoding="utf-8")) if TRADES_LOG.exists() else []


def _wr(wins: int, losses: int) -> str:
    return f"{wins/(wins+losses)*100:.1f}%" if (wins + losses) else "—"


def build_html(trades: list) -> str:
    total  = len(trades)
    wins   = sum(1 for t in trades if t.get("outcome") == "WIN")
    losses = sum(1 for t in trades if t.get("outcome") == "LOSS")
    open_  = sum(1 for t in trades if t.get("outcome") == "OPEN")
    pnl    = sum(t.get("pnl", 0.0) for t in trades)
    wr_pct = wins / (wins + losses) * 100 if (wins + losses) else 0

    # ── Per-regime stats ──────────────────────────────────────────────────────
    regimes: dict = {}
    for t in trades:
        r = t.get("regime", "unknown")
        regimes.setdefault(r, {"wins": 0, "losses": 0, "pnl": 0.0})
        if t.get("outcome") == "WIN":   regimes[r]["wins"]  += 1
        elif t.get("outcome") == "LOSS": regimes[r]["losses"] += 1
        regimes[r]["pnl"] += t.get("pnl", 0.0)

    # ── Per-score-bucket stats ─────────────────────────────────────────────────
    buckets = {"60–70": [0, 0], "70–80": [0, 0], "80+": [0, 0]}
    for t in trades:
        s   = t.get("score", 0)
        key = "80+" if s >= 80 else ("70–80" if s >= 70 else "60–70")
        if t.get("outcome") == "WIN":   buckets[key][0] += 1
        elif t.get("outcome") == "LOSS": buckets[key][1] += 1

    # ── Trade rows ────────────────────────────────────────────────────────────
    rows = ""
    for t in sorted(trades, key=lambda x: x.get("ts", 0), reverse=True):
        ts  = datetime.fromtimestamp(t.get("ts", 0), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        out = t.get("outcome", "?")
        col = "#2ecc71" if out == "WIN" else ("#e74c3c" if out == "LOSS" else "#e67e22")
        rows += (
            f"<tr><td>{ts}</td><td>{t.get('symbol','')}</td>"
            f"<td>{t.get('regime','')}</td><td>{t.get('score',0):.1f}</td>"
            f"<td>${t.get('entry',0):.6f}</td>"
            f"<td style='color:{col};font-weight:bold'>{out}</td>"
            f"<td>${t.get('pnl',0.0):.2f}</td></tr>"
        )

    regime_rows = "".join(
        f"<tr><td>{r}</td><td>{d['wins']}</td><td>{d['losses']}</td>"
        f"<td>{_wr(d['wins'],d['losses'])}</td><td>${d['pnl']:.2f}</td></tr>"
        for r, d in sorted(regimes.items())
    )
    bucket_rows = "".join(
        f"<tr><td>{b}</td><td>{w}</td><td>{l}</td><td>{_wr(w,l)}</td></tr>"
        for b, (w, l) in buckets.items()
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    wr_col = "#2ecc71" if wr_pct >= 50 else "#e74c3c"
    pnl_col = "#2ecc71" if pnl >= 0 else "#e74c3c"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Trading Bot Dashboard</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Courier New',monospace;background:#0d1117;color:#c9d1d9;padding:24px}}
  h1{{color:#58a6ff;margin-bottom:4px}}
  h2{{color:#8b949e;margin:28px 0 10px;font-size:1em;text-transform:uppercase;letter-spacing:.1em}}
  .sub{{color:#8b949e;font-size:.85em;margin-bottom:20px}}
  .cards{{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:8px}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 22px;min-width:110px;text-align:center}}
  .val{{font-size:1.9em;font-weight:700}}
  .lbl{{font-size:.75em;color:#8b949e;margin-top:3px}}
  table{{width:100%;border-collapse:collapse;margin-top:6px}}
  th{{background:#161b22;color:#8b949e;padding:8px 12px;text-align:left;font-size:.85em}}
  td{{padding:6px 12px;border-bottom:1px solid #21262d;font-size:.9em}}
  tr:hover td{{background:#161b22}}
</style>
</head>
<body>
<h1>Trading Bot — Backtesting Dashboard</h1>
<p class="sub">Generated: {now}</p>

<div class="cards">
  <div class="card"><div class="val">{total}</div><div class="lbl">Total Trades</div></div>
  <div class="card"><div class="val" style="color:#2ecc71">{wins}</div><div class="lbl">Wins</div></div>
  <div class="card"><div class="val" style="color:#e74c3c">{losses}</div><div class="lbl">Losses</div></div>
  <div class="card"><div class="val" style="color:#e67e22">{open_}</div><div class="lbl">Open</div></div>
  <div class="card"><div class="val" style="color:{wr_col}">{wr_pct:.1f}%</div><div class="lbl">Win Rate</div></div>
  <div class="card"><div class="val" style="color:{pnl_col}">${pnl:.2f}</div><div class="lbl">Total PnL</div></div>
</div>

<h2>Performance by Regime</h2>
<table>
  <tr><th>Regime</th><th>Wins</th><th>Losses</th><th>Win Rate</th><th>PnL</th></tr>
  {regime_rows}
</table>

<h2>Performance by Score Range</h2>
<table>
  <tr><th>Score</th><th>Wins</th><th>Losses</th><th>Win Rate</th></tr>
  {bucket_rows}
</table>

<h2>Trade History</h2>
<table>
  <tr><th>Time (UTC)</th><th>Symbol</th><th>Regime</th><th>Score</th><th>Entry</th><th>Outcome</th><th>PnL</th></tr>
  {rows if rows else '<tr><td colspan="7" style="color:#8b949e;text-align:center">No trades yet</td></tr>'}
</table>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true", help="Open in browser after generating")
    args = parser.parse_args()

    trades = load_trades()
    if not trades:
        print("No trades in data/trades_log.json — run the bot first.")
        return

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(build_html(trades), encoding="utf-8")
    print(f"Dashboard written to: {OUT_FILE}")
    if args.open:
        webbrowser.open(str(OUT_FILE))


if __name__ == "__main__":
    main()
