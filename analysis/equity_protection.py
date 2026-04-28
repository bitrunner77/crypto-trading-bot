"""
analysis/equity_protection.py — Tiered equity curve AI protection.

Protection levels based on rolling drawdown from session peak:
  GREEN   (< 5%)  — full trading, no restriction
  YELLOW  (5–10%) — reduce position size to 60%
  ORANGE  (10–15%)— reduce size to 30%, cap leverage at 2x
  RED     (15–20%)— pause all new trades (existing may stay)
  BLACK   (> 20%) — close all positions and halt until reset

Designed to prevent account blow-ups in adverse market conditions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("cryptobot.eq_protection")


@dataclass
class ProtectionState:
    level:         str    # "GREEN" | "YELLOW" | "ORANGE" | "RED" | "BLACK"
    drawdown:      float  # current drawdown from session peak (0–1)
    size_cap:      float  # max position size fraction (0–1)
    leverage_cap:  int    # max leverage allowed
    pause_new:     bool   # block new trades
    close_all:     bool   # close existing positions
    message:       str


_THRESHOLDS = [
    # (max_drawdown, level, size_cap, lev_cap, pause, close, msg)
    (0.05, "GREEN",  1.00, 20, False, False, "Equity healthy — full trading"),
    (0.10, "YELLOW", 0.60, 10, False, False, "Drawdown >5% — size capped 60%"),
    (0.15, "ORANGE", 0.30,  2, False, False, "Drawdown >10% — size 30%, leverage ≤2x"),
    (0.20, "RED",    0.10,  1,  True, False, "Drawdown >15% — new trades PAUSED"),
    (1.00, "BLACK",  0.00,  1,  True,  True, "Drawdown >20% — CLOSE ALL, halt trading"),
]


class EquityProtector:
    """
    Tracks peak equity and current drawdown.
    Call update() each round to get current ProtectionState.
    """

    def __init__(self) -> None:
        self._peak:    float = 0.0
        self._current: float = 0.0
        self._state    = ProtectionState("GREEN", 0.0, 1.0, 20, False, False,
                                         "Equity healthy — full trading")
        self._alerted: set = set()

    def update(self, current_equity: float) -> ProtectionState:
        if current_equity > self._peak:
            self._peak = current_equity
        if self._current == 0.0:
            self._current = current_equity
        self._current = current_equity

        if self._peak <= 0:
            return self._state

        drawdown = (self._peak - self._current) / self._peak

        for (max_dd, level, size_cap, lev_cap, pause, close, msg) in _THRESHOLDS:
            if drawdown <= max_dd:
                new_state = ProtectionState(level, drawdown, size_cap, lev_cap, pause, close, msg)
                if level != self._state.level and level not in self._alerted:
                    logger.warning(f"[PROTECT] {self._state.level} → {level}: {msg}")
                    self._alerted.add(level)
                self._state = new_state
                return self._state

        return self._state

    def reset(self) -> None:
        """Call after equity recovers or on manual reset."""
        self._peak    = self._current
        self._alerted.clear()
        self._state   = ProtectionState("GREEN", 0.0, 1.0, 20, False, False,
                                        "Reset — equity healthy")

    @property
    def current_state(self) -> ProtectionState:
        return self._state
