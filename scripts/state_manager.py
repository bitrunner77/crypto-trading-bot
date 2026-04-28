"""
scripts/state_manager.py — Persistent bot state: daily lockout, revenge prevention,
equity curve, ML win-probability scoring, backtested parameter optimizer.

All data stored under data/*.json — survives restarts.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

_DATA = Path(__file__).resolve().parents[1] / "data"
_DATA.mkdir(exist_ok=True)

DAILY_STATS_F  = _DATA / "daily_stats.json"
SL_EVENTS_F    = _DATA / "sl_events.json"
EQUITY_F       = _DATA / "equity_curve.json"
BOT_CONTROL_F  = _DATA / "bot_control.json"
TRADES_LOG_F   = _DATA / "trades_log.json"

DAILY_LOSS_LIMIT_USD  = float(os.getenv("DAILY_LOSS_LIMIT_USD", "15.0"))
DAILY_LOSS_MAX_TRADES = int(os.getenv("DAILY_LOSS_MAX_TRADES", "3"))
REVENGE_COOLDOWN_MIN  = int(os.getenv("REVENGE_COOLDOWN_MIN", "60"))


# ── File helpers ──────────────────────────────────────────────────────────────
def _r(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except Exception:
        return default

def _w(path: Path, data) -> None:
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Daily loss lockout ────────────────────────────────────────────────────────
def _daily() -> dict:
    today = date.today().isoformat()
    d = _r(DAILY_STATS_F, {})
    if d.get("date") != today:
        d = {"date": today, "losses": 0, "loss_usd": 0.0, "wins": 0, "locked": False}
        _w(DAILY_STATS_F, d)
    return d

def is_daily_locked() -> tuple[bool, str]:
    d = _daily()
    if d.get("locked"):
        return True, f"{d['losses']} losses / ${d['loss_usd']:.2f} today — locked until tomorrow"
    return False, ""

def record_daily_loss(amount_usd: float) -> None:
    d = _daily()
    d["losses"] += 1
    d["loss_usd"] = round(d["loss_usd"] + abs(amount_usd), 2)
    if d["loss_usd"] >= DAILY_LOSS_LIMIT_USD or d["losses"] >= DAILY_LOSS_MAX_TRADES:
        d["locked"] = True
    _w(DAILY_STATS_F, d)

def record_daily_win() -> None:
    d = _daily()
    d["wins"] += 1
    _w(DAILY_STATS_F, d)

def unlock_daily() -> None:
    d = _daily()
    d["locked"] = False
    _w(DAILY_STATS_F, d)

def get_daily_stats() -> dict:
    return _daily()


# ── Revenge trade prevention ──────────────────────────────────────────────────
def record_sl_hit(symbol: str) -> None:
    """Call when a stop loss is triggered for a symbol."""
    events = _r(SL_EVENTS_F, {})
    events[symbol] = time.time()
    _w(SL_EVENTS_F, events)

def is_revenge_cooldown(symbol: str) -> tuple[bool, float]:
    """Returns (True, minutes_remaining) if symbol is still in SL cooldown."""
    events  = _r(SL_EVENTS_F, {})
    last_sl = events.get(symbol)
    if not last_sl:
        return False, 0.0
    remaining = REVENGE_COOLDOWN_MIN - (time.time() - last_sl) / 60
    return remaining > 0, max(0.0, remaining)


# ── Equity curve (live balance tracking) ─────────────────────────────────────
def track_equity(balance: float) -> None:
    curve = _r(EQUITY_F, [])
    curve.append({"ts": time.time(), "bal": round(balance, 2)})
    if len(curve) > 720:   # ~30 days at hourly snapshots
        curve = curve[-720:]
    _w(EQUITY_F, curve)

def get_equity_curve() -> list:
    return _r(EQUITY_F, [])


# ── Bot control (Telegram /pause /resume) ─────────────────────────────────────
def is_bot_paused() -> bool:
    return bool(_r(BOT_CONTROL_F, {}).get("paused", False))

def set_bot_paused(paused: bool) -> None:
    ctrl = _r(BOT_CONTROL_F, {})
    ctrl["paused"] = paused
    _w(BOT_CONTROL_F, ctrl)


# ── ML win-probability scoring ────────────────────────────────────────────────
def ml_win_probability(regime: str, score: float) -> float:
    """
    Estimate win probability from similar historical trades.
    Returns 0.0–1.0.  Default 0.60 when data is sparse.
    """
    try:
        trades  = _r(TRADES_LOG_F, [])
        decided = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
        if len(decided) < 10:
            return 0.60

        # Same regime + score within ±15 pts
        similar = [
            t for t in decided
            if t.get("regime") == regime and abs(t.get("score", 0) - score) <= 15
        ]
        if len(similar) < 3:
            similar = [t for t in decided if t.get("regime") == regime] or decided

        return round(sum(1 for t in similar if t["outcome"] == "WIN") / len(similar), 3)
    except Exception:
        return 0.60


# ── Backtested parameter optimizer ───────────────────────────────────────────
def get_optimal_threshold() -> float:
    """
    Grid-search trade history to find the score threshold with the best win rate.
    Falls back to 70.0 when data is insufficient.
    """
    try:
        trades  = _r(TRADES_LOG_F, [])
        decided = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]
        if len(decided) < 15:
            return 70.0

        best_wr, best_t = 0.0, 70.0
        for thresh in [60, 65, 70, 72, 75, 78, 80, 85]:
            above = [x for x in decided if x.get("score", 0) >= thresh]
            if len(above) < 3:
                continue
            wr = sum(1 for x in above if x["outcome"] == "WIN") / len(above)
            if wr > best_wr:
                best_wr, best_t = wr, float(thresh)

        return best_t
    except Exception:
        return 70.0
