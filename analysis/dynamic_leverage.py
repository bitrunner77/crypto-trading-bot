"""
analysis/dynamic_leverage.py — Calculate optimal leverage dynamically.

Factors applied as multipliers to base_leverage:
  1. Volatility  — ATR ratio vs 20-candle average ATR (high vol → reduce)
  2. Regime      — trending=1.0, mean_reverting=0.8, choppy=0.6, high_vol=0.5
  3. Streak      — 3+ consecutive losses → ×0.7; 3+ wins → ×1.1 (capped)
  4. Protection  — equity protection level scales lever down further

Result clamped to [1, max_leverage].
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.dyn_leverage")

_REGIME_MULT: Dict[str, float] = {
    "trending_up":    1.0,
    "trending_down":  1.0,
    "mean_reverting": 0.9,
    "high_vol":       0.7,
    "low_vol":        0.95,
    "choppy":         0.8,    # was 0.6 — less aggressive cut
    "neutral":        0.95,
    "neutral_off":    0.95,
}

_PROTECT_MULT: Dict[str, float] = {
    "GREEN":  1.0,
    "YELLOW": 0.8,
    "ORANGE": 0.4,
    "RED":    0.2,
    "BLACK":  0.1,
}


def calculate_dynamic_leverage(
    base_leverage:  int,
    indicators:     Dict,
    regime:         str,
    recent_trades:  List[Dict],
    protection_level: str = "GREEN",
    max_leverage:   int = 20,
) -> int:
    """Return the recommended leverage as an integer in [1, max_leverage]."""

    mult = 1.0

    # ── Volatility factor ─────────────────────────────────────────────────────
    atr = indicators.get("atr_14")
    vol_sma = indicators.get("volume_sma_20")
    # Use price-normalised ATR as volatility proxy
    price = indicators.get("price") or 1.0
    if atr and price > 0:
        atr_pct = atr / price           # ATR as % of price
        if atr_pct > 0.04:              # >4% daily range → very volatile
            mult *= 0.5
        elif atr_pct > 0.025:
            mult *= 0.7
        elif atr_pct < 0.01:            # very tight range
            mult *= 1.1

    # ── Regime factor ─────────────────────────────────────────────────────────
    mult *= _REGIME_MULT.get(regime, 0.8)

    # ── Win/loss streak factor ────────────────────────────────────────────────
    closed = [t for t in recent_trades if t.get("pnl") is not None][-5:]
    if len(closed) >= 3:
        pnls = [(t.get("pnl") or 0.0) for t in closed[-3:]]
        if all(p < 0 for p in pnls):
            mult *= 0.7
            logger.debug("[DYN_LEV] 3 consecutive losses → leverage reduced")
        elif all(p > 0 for p in pnls):
            mult *= 1.1

    # ── Equity protection cap ─────────────────────────────────────────────────
    mult *= _PROTECT_MULT.get(protection_level, 1.0)

    # Floor at 40% of base so aggressive growth mode is never choked below ~4x
    floor  = max(1, round(base_leverage * 0.4))
    result = max(floor, min(max_leverage, round(base_leverage * mult)))
    logger.debug(
        f"[DYN_LEV] base={base_leverage}x × mult={mult:.2f} → {result}x "
        f"(regime={regime}, protect={protection_level})"
    )
    return result
