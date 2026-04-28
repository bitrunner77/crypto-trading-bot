"""
scripts/generate_dashboard.py — Animated neon HTML dashboard generator.

Produces:
  - reports/YYYY-MM-DD.json        (timestamped JSON report)
  - reports/dashboard.html         (neon animated dashboard)
  - data/dashboard_history.json    (appended snapshot for trend tracking)

Usage:
  python scripts/generate_dashboard.py --report reports/2026-04-20.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
DATA_DIR = ROOT / "data"
REPORTS_DIR.mkdir(exist_ok=True)


def save_json_report(results: list, hindsight: dict, learning_state: dict) -> Path:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "date": today,
        "bots": results,
        "hindsight": hindsight,
        "calibration": learning_state.get("calibration", {}),
    }
    path = REPORTS_DIR / f"{today}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def append_dashboard_history(results: list, hindsight: dict) -> None:
    history_path = DATA_DIR / "dashboard_history.json"
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        history = []

    history.append({
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "timestamp": datetime.utcnow().isoformat(),
        "bot_count": len(results),
        "verdicts": {r["bot_name"]: r.get("verdict", r.get("decision", "?")) for r in results},
        "avg_adaptation": round(
            sum(r["adaptation"]["total"] for r in results) / len(results), 2
        ) if results else 0,
        "regret_rate": hindsight.get("regret_rate", 0),
        "pause_threshold": hindsight.get("new_pause_threshold", 40.0),
    })
    history = history[-365:]  # keep 1 year
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")


def generate_html(results: list, hindsight: dict, report_path: Path) -> Path:
    today = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    verdict_color = {
        "PAUSE": "#ff4444", "HOLD": "#ffaa00",
        "REACTIVATE": "#00ff88", "INSUFFICIENT": "#8888aa",
        "RUN": "#00ff88", "SWITCH": "#ff8800",
    }

    bot_rows = ""
    for r in results:
        verdict = r.get("verdict", r.get("decision", "?"))
        color = verdict_color.get(verdict, "#ffffff")
        m = r.get("metrics", {})
        a = r.get("adaptation", {})
        score = a.get("total", 0)
        bar_width = int(score)
        bot_rows += f"""
        <tr>
          <td>{r['bot_name']}</td>
          <td>{r.get('symbol','')}</td>
          <td>{r.get('regime','')}</td>
          <td>{m.get('win_rate',0):.1%}</td>
          <td>{m.get('profit_factor',0):.2f}</td>
          <td>{m.get('max_drawdown',0):.1%}</td>
          <td>{m.get('consecutive_losses',0)}</td>
          <td>
            <div class="bar-wrap">
              <div class="bar" style="width:{bar_width}%;background:{color}"></div>
              <span>{score:.1f}</span>
            </div>
          </td>
          <td style="color:{color};font-weight:bold">{verdict}</td>
        </tr>"""

    # Equity sparkline data from adaptation history
    sparkline_labels = json.dumps([r["bot_name"] for r in results])
    sparkline_values = json.dumps([r["adaptation"]["total"] for r in results])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>StrategyFactory Bot Manager</title>
  <style>
    :root {{
      --bg: #060b14; --panel: #0d1a2a; --border: #1a3a5c;
      --cyan: #00e5ff; --green: #00ff88; --red: #ff4444;
      --yellow: #ffaa00; --purple: #aa44ff; --text: #b0c4d8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: var(--bg); color: var(--text); font-family: 'Courier New', monospace; }}

    header {{
      background: linear-gradient(90deg, #060b14 0%, #0a1e35 50%, #060b14 100%);
      border-bottom: 1px solid var(--cyan);
      padding: 18px 32px;
      display: flex; align-items: center; justify-content: space-between;
    }}
    header h1 {{ color: var(--cyan); font-size: 1.4rem; letter-spacing: 4px; text-transform: uppercase; }}
    header .timestamp {{ color: #4a7fa0; font-size: 0.75rem; }}

    .grid {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; padding: 24px 32px; }}
    .card {{
      background: var(--panel); border: 1px solid var(--border);
      border-radius: 8px; padding: 18px;
      position: relative; overflow: hidden;
    }}
    .card::before {{
      content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
      background: linear-gradient(90deg, transparent, var(--cyan), transparent);
      animation: scan 3s linear infinite;
    }}
    @keyframes scan {{ 0% {{ opacity: 0.3; }} 50% {{ opacity: 1; }} 100% {{ opacity: 0.3; }} }}
    .card .label {{ color: #4a7fa0; font-size: 0.65rem; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 8px; }}
    .card .value {{ font-size: 1.8rem; font-weight: bold; }}
    .card .sub {{ font-size: 0.7rem; color: #4a7fa0; margin-top: 4px; }}

    .section {{ padding: 0 32px 24px; }}
    .section h2 {{ color: var(--cyan); font-size: 0.8rem; letter-spacing: 3px; text-transform: uppercase; margin-bottom: 12px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }}

    table {{ width: 100%; border-collapse: collapse; font-size: 0.78rem; }}
    th {{ color: #4a7fa0; text-transform: uppercase; letter-spacing: 1px; font-size: 0.65rem; padding: 8px 12px; border-bottom: 1px solid var(--border); text-align: left; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid #0d1e30; }}
    tr:hover td {{ background: #0d1e30; }}

    .bar-wrap {{ display: flex; align-items: center; gap: 8px; }}
    .bar {{ height: 6px; border-radius: 3px; min-width: 2px; transition: width 0.5s; }}
    .bar-wrap span {{ font-size: 0.75rem; color: #b0c4d8; }}

    .hindsight {{
      display: grid; grid-template-columns: repeat(3,1fr); gap: 16px;
      padding: 0 32px 24px;
    }}
    .hint-card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }}
    .hint-card .label {{ color: #4a7fa0; font-size: 0.65rem; letter-spacing: 2px; text-transform: uppercase; margin-bottom: 6px; }}
    .hint-card .value {{ font-size: 1.3rem; font-weight: bold; color: var(--cyan); }}

    canvas {{ max-width: 100%; }}
    .chart-wrap {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin: 0 32px 24px; }}

    footer {{ text-align: center; color: #2a4a6a; font-size: 0.65rem; padding: 16px; border-top: 1px solid var(--border); }}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
</head>
<body>

<header>
  <h1>&#9881; StrategyFactory Bot Manager</h1>
  <div class="timestamp">Generated: {today}</div>
</header>

<div class="grid">
  <div class="card">
    <div class="label">Total Bots</div>
    <div class="value" style="color:var(--cyan)">{len(results)}</div>
    <div class="sub">monitored today</div>
  </div>
  <div class="card">
    <div class="label">Avg Adaptation</div>
    <div class="value" style="color:var(--green)">{round(sum(r['adaptation']['total'] for r in results)/len(results),1) if results else 0}/100</div>
    <div class="sub">score across all bots</div>
  </div>
  <div class="card">
    <div class="label">Regret Rate</div>
    <div class="value" style="color:var(--yellow)">{hindsight.get('regret_rate',0):.1%}</div>
    <div class="sub">past PAUSE decisions missed</div>
  </div>
  <div class="card">
    <div class="label">Pause Threshold</div>
    <div class="value" style="color:var(--purple)">{hindsight.get('new_pause_threshold',40.0):.1f}</div>
    <div class="sub">calibrated today</div>
  </div>
</div>

<div class="chart-wrap">
  <canvas id="adaptChart" height="80"></canvas>
</div>

<div class="section">
  <h2>&#9878; Bot Decisions</h2>
  <table>
    <thead>
      <tr>
        <th>Bot</th><th>Symbol</th><th>Regime</th><th>Win Rate</th>
        <th>Profit Factor</th><th>Max DD</th><th>Consec. Losses</th>
        <th>Adapt Score</th><th>Verdict</th>
      </tr>
    </thead>
    <tbody>{bot_rows}</tbody>
  </table>
</div>

<div class="section">
  <h2>&#9888; Hindsight Analysis</h2>
</div>
<div class="hindsight">
  <div class="hint-card">
    <div class="label">Regret Rate</div>
    <div class="value">{hindsight.get('regret_rate',0):.1%}</div>
    <div style="font-size:0.7rem;color:#4a7fa0;margin-top:4px">% of PAUSE decisions that missed profit</div>
  </div>
  <div class="hint-card">
    <div class="label">Missed PnL</div>
    <div class="value">${hindsight.get('missed_pnl',0):.2f}</div>
    <div style="font-size:0.7rem;color:#4a7fa0;margin-top:4px">total opportunity cost from incorrect PAUSEs</div>
  </div>
  <div class="hint-card">
    <div class="label">Threshold Delta</div>
    <div class="value">{hindsight.get('calibration_delta',0):+.2f}</div>
    <div style="font-size:0.7rem;color:#4a7fa0;margin-top:4px">adjustment applied to pause threshold</div>
  </div>
</div>

<footer>StrategyFactory Bot Manager &mdash; repeats daily at 10:00 AM &mdash; learning_state.json carries forward</footer>

<script>
  const ctx = document.getElementById('adaptChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {sparkline_labels},
      datasets: [{{
        label: 'Adaptation Score (0–100)',
        data: {sparkline_values},
        backgroundColor: {sparkline_values}.map(v =>
          v >= 70 ? '#00ff8844' : v >= 40 ? '#ffaa0044' : '#ff444444'
        ),
        borderColor: {sparkline_values}.map(v =>
          v >= 70 ? '#00ff88' : v >= 40 ? '#ffaa00' : '#ff4444'
        ),
        borderWidth: 1,
        borderRadius: 4,
      }}]
    }},
    options: {{
      responsive: true,
      plugins: {{
        legend: {{ labels: {{ color: '#b0c4d8', font: {{ family: 'Courier New' }} }} }},
      }},
      scales: {{
        x: {{ ticks: {{ color: '#4a7fa0' }}, grid: {{ color: '#0d1e30' }} }},
        y: {{ min: 0, max: 100, ticks: {{ color: '#4a7fa0' }}, grid: {{ color: '#0d1e30' }} }},
      }}
    }}
  }});
</script>
</body>
</html>"""

    dash_path = REPORTS_DIR / "dashboard.html"
    dash_path.write_text(html, encoding="utf-8")
    return dash_path


def generate_outputs(results: list, hindsight: dict, learning_state: dict) -> None:
    report_path = save_json_report(results, hindsight, learning_state)
    dash_path = generate_html(results, hindsight, report_path)
    append_dashboard_history(results, hindsight)
    print(f"  Report:    {report_path}")
    print(f"  Dashboard: {dash_path}")
    print(f"  History:   {DATA_DIR / 'dashboard_history.json'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate StrategyFactory dashboard from a JSON report")
    parser.add_argument("--report", required=True, help="Path to YYYY-MM-DD.json report")
    args = parser.parse_args()

    report_data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    results = report_data.get("bots", [])
    hindsight = report_data.get("hindsight", {})
    learning_state = {"calibration": report_data.get("calibration", {})}

    dash_path = generate_html(results, hindsight, Path(args.report))
    print(f"Dashboard: {dash_path}")
