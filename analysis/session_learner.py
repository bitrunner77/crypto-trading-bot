"""
analysis/session_learner.py — Learn best trading hours from historical results.

Stores per-UTC-hour win/loss stats in data/.session_stats.json.
After 20+ trades in a given hour the multiplier is learned from data.
Before that, falls back to the static session_timer multipliers.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

logger = logging.getLogger("cryptobot.session_learner")

DATA_FILE    = Path(__file__).resolve().parents[1] / "data" / ".session_stats.json"
MIN_TRADES   = 20    # minimum trades before using learned multiplier
BOOST_FLOOR  = 0.5   # never go below 0.5× even if hour is terrible
BOOST_CEIL   = 1.8   # never go above 1.8× even if hour is great


class SessionLearner:
    """
    Persists hourly trade stats and provides a size multiplier per UTC hour.
    Call record_trade() after every closed trade.
    Call get_multiplier(hour) before placing an order.
    """

    def __init__(self) -> None:
        self._stats: Dict[str, Dict] = self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_trade(self, pnl: float, dt: datetime | None = None) -> None:
        if dt is None:
            dt = datetime.now(timezone.utc)
        hour = str(dt.hour)
        bucket = self._stats.setdefault(hour, {"wins": 0, "losses": 0, "total_pnl": 0.0})
        if pnl > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
        bucket["total_pnl"] = round(bucket["total_pnl"] + pnl, 4)
        self._save()

    def get_multiplier(self, hour: int | None = None) -> float:
        if hour is None:
            hour = datetime.now(timezone.utc).hour
        bucket = self._stats.get(str(hour))
        if bucket is None:
            return 1.0   # no data → neutral

        total = bucket["wins"] + bucket["losses"]
        if total < MIN_TRADES:
            return 1.0   # insufficient data → neutral

        win_rate = bucket["wins"] / total
        avg_pnl  = bucket["total_pnl"] / total

        # Base multiplier from win rate
        if win_rate >= 0.65:
            mult = 1.5
        elif win_rate >= 0.55:
            mult = 1.2
        elif win_rate >= 0.45:
            mult = 1.0
        elif win_rate >= 0.35:
            mult = 0.7
        else:
            mult = 0.5

        # Nudge by avg P&L sign
        if avg_pnl < 0 and mult > 0.5:
            mult *= 0.9

        mult = round(max(BOOST_FLOOR, min(BOOST_CEIL, mult)), 2)
        logger.debug(f"[SESS_LEARN] hour={hour} WR={win_rate:.0%} n={total} → mult={mult}×")
        return mult

    def best_hours(self, top_n: int = 5) -> list[int]:
        """Return top N hours by learned multiplier (sufficient data only)."""
        ranked = []
        for h in range(24):
            b = self._stats.get(str(h))
            if b and (b["wins"] + b["losses"]) >= MIN_TRADES:
                ranked.append((h, self.get_multiplier(h)))
        ranked.sort(key=lambda x: x[1], reverse=True)
        return [h for h, _ in ranked[:top_n]]

    def summary(self) -> str:
        lines = []
        for h in range(24):
            b = self._stats.get(str(h))
            if not b:
                continue
            total = b["wins"] + b["losses"]
            if total == 0:
                continue
            wr  = b["wins"] / total
            m   = self.get_multiplier(h)
            lines.append(f"  {h:02d}:00 UTC — {total} trades, WR {wr:.0%}, mult {m}×")
        return "\n".join(lines) if lines else "No session data yet"

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> Dict:
        try:
            if DATA_FILE.exists():
                return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
        return {}

    def _save(self) -> None:
        try:
            DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            DATA_FILE.write_text(json.dumps(self._stats, indent=2))
        except Exception as exc:
            logger.debug(f"[SESS_LEARN] Save failed: {exc}")
