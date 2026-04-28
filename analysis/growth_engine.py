"""
analysis/growth_engine.py — Monthly growth target and compounding engine.

Tracks rolling monthly P&L vs a target (default +10%/month).
Adjusts position-size multiplier based on progress:

  Behind target  → slight size boost (catch up, max 1.3×)
  On track       → normal (1.0×)
  Target hit     → reduce size to lock in gains (0.7×)
  Way ahead      → defensive (0.5×) — don't give back the month

At month-end the engine compounds: new starting balance = ending balance.
Stores state in data/.growth_state.json so it survives restarts.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("cryptobot.growth")

STATE_FILE   = Path(__file__).resolve().parents[1] / "data" / ".growth_state.json"
MONTH_TARGET = 0.10   # 10% per month default


@dataclass
class GrowthState:
    month:          str    # "YYYY-MM"
    start_balance:  float
    target_balance: float
    current:        float
    compounded:     float  # cumulative since first use


class GrowthEngine:
    """
    Call update(current_balance) each round.
    Returns a size multiplier (0.5–1.3) based on monthly progress.
    """

    def __init__(self, target_pct: float = MONTH_TARGET) -> None:
        self._target_pct = target_pct
        self._state      = self._load()

    def update(self, current_balance: float) -> float:
        """Returns size multiplier for this round."""
        now   = datetime.now(timezone.utc)
        month = now.strftime("%Y-%m")

        # New month → compound and reset
        if self._state.month != month:
            logger.info(
                f"[GROWTH] New month {month} — "
                f"compounding from ${self._state.start_balance:,.2f} "
                f"to ${current_balance:,.2f}"
            )
            self._state = GrowthState(
                month          = month,
                start_balance  = current_balance,
                target_balance = current_balance * (1 + self._target_pct),
                current        = current_balance,
                compounded     = self._state.compounded + (current_balance - self._state.start_balance),
            )
            self._save()
            return 1.0

        self._state.current = current_balance

        progress = self._progress()
        mult     = self._multiplier(progress)

        self._save()
        logger.debug(
            f"[GROWTH] {month}: ${current_balance:,.2f} / "
            f"${self._state.target_balance:,.2f} "
            f"({progress:.0%} of target) → size ×{mult}"
        )
        return mult

    def _progress(self) -> float:
        """0.0 = at start, 1.0 = target hit, >1.0 = ahead of target."""
        gain   = self._state.current - self._state.start_balance
        needed = self._state.target_balance - self._state.start_balance
        return gain / needed if needed > 0 else 0.0

    def _multiplier(self, progress: float) -> float:
        if progress >= 1.5:    # Way ahead — lock in extra gains
            return 0.7
        if progress >= 1.0:    # Target hit — protect but keep compounding
            return 0.85
        if progress >= 0.6:    # On track — normal
            return 1.0
        if progress >= 0.3:    # Behind — push harder
            return 1.2
        return 1.4             # Well behind — maximum aggression (capped)

    @property
    def monthly_pnl(self) -> float:
        return self._state.current - self._state.start_balance

    @property
    def monthly_progress_pct(self) -> float:
        return self._progress() * 100

    @property
    def target_balance(self) -> float:
        return self._state.target_balance

    @property
    def compounded_total(self) -> float:
        return self._state.compounded

    def status_line(self) -> str:
        p = self._progress()
        return (
            f"Month {self._state.month}: "
            f"${self._state.current:,.2f} / ${self._state.target_balance:,.2f} "
            f"({p:.0%} of {self._target_pct:.0%} target) | "
            f"Total compounded: ${self._state.compounded:+,.2f}"
        )

    def _load(self) -> GrowthState:
        try:
            if STATE_FILE.exists():
                d = json.loads(STATE_FILE.read_text())
                return GrowthState(**d)
        except Exception:
            pass
        now = datetime.now(timezone.utc)
        return GrowthState(
            month          = now.strftime("%Y-%m"),
            start_balance  = 0.0,
            target_balance = 0.0,
            current        = 0.0,
            compounded     = 0.0,
        )

    def _save(self) -> None:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(asdict(self._state), indent=2))
        except Exception:
            pass
