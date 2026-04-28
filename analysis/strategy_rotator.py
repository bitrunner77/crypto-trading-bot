"""
analysis/strategy_rotator.py — Auto-rotate strategy based on regime + rolling P&L.

Regime → preferred strategy mapping:
  trending_up / trending_down  → regime_momentum
  mean_reverting               → mean_reversion
  high_vol                     → regime_scalping
  low_vol                      → dca
  choppy                       → mean_reversion
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger("cryptobot.rotator")

REGIME_MAP: Dict[str, str] = {
    "trending_up":    "regime_momentum",
    "trending_down":  "regime_momentum",
    "mean_reverting": "mean_reversion",
    "high_vol":       "regime_scalping",
    "low_vol":        "dca",
    "choppy":         "mean_reversion",
}
MIN_ROUNDS_BETWEEN_ROTATIONS = 5


@dataclass
class RotationAdvice:
    current_strategy:   str
    suggested_strategy: str
    should_rotate:      bool
    reason:             str


class StrategyRotator:
    """
    Tracks per-round P&L and current regime.
    Call record_round_pnl() after each round, should_rotate() to check.
    """

    def __init__(self, initial_strategy: str, window: int = 10) -> None:
        self._current  = initial_strategy
        self._window   = window
        self._pnl_buf: Dict[str, List[float]] = {}
        self._rounds_since_rotation = 0

    def record_round_pnl(self, strategy: str, pnl: float) -> None:
        buf = self._pnl_buf.setdefault(strategy, [])
        buf.append(pnl)
        if len(buf) > self._window:
            buf.pop(0)

    def should_rotate(
        self, regime: str, equity_health: str
    ) -> RotationAdvice:
        self._rounds_since_rotation += 1

        if self._rounds_since_rotation < MIN_ROUNDS_BETWEEN_ROTATIONS:
            return RotationAdvice(
                self._current, self._current, False,
                f"Too soon (round {self._rounds_since_rotation}/{MIN_ROUNDS_BETWEEN_ROTATIONS})",
            )

        regime_pick = REGIME_MAP.get(regime, self._current)

        # Rotate if current strategy has lost money last 5 rounds
        buf = self._pnl_buf.get(self._current, [])
        if len(buf) >= 5 and sum(buf[-5:]) < 0 and regime_pick != self._current:
            reason = (f"Regime={regime}, {self._current} lost "
                      f"${abs(sum(buf[-5:])):.2f} last 5 rounds")
            return RotationAdvice(self._current, regime_pick, True, reason)

        # Rotate in critical equity state to a safer strategy
        if equity_health == "critical" and self._current not in ("dca", "mean_reversion"):
            return RotationAdvice(
                self._current, "mean_reversion", True,
                "Equity critical — rotating to conservative strategy",
            )

        if regime_pick != self._current:
            return RotationAdvice(
                self._current, regime_pick, False,
                f"Regime suggests {regime_pick} but current performance ok",
            )

        return RotationAdvice(self._current, self._current, False, "Strategy performing well")

    def apply_rotation(self, new_strategy: str) -> None:
        logger.info(f"[ROTATOR] {self._current} → {new_strategy}")
        self._current = new_strategy
        self._rounds_since_rotation = 0

    @property
    def current_strategy(self) -> str:
        return self._current
