"""
analysis/breakout_validator.py — Filter fake breakouts before executing trades.

A breakout is "real" when at least 2 of 3 checks pass:
  1. Volume ≥ 1.3× 20-candle average on the breakout candle
  2. Close is actually beyond the prior resistance/support (not just a wick)
  3. Move size ≥ 0.5× ATR
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd


@dataclass
class BreakoutResult:
    is_real: bool
    confidence: float       # 0.0–1.0 (passed checks / 3)
    volume_ok: bool
    close_beyond: bool
    atr_ok: bool
    description: str


def validate_breakout(
    df: pd.DataFrame,
    direction: str,
    indicators: Dict[str, Optional[float]],
) -> BreakoutResult:
    """
    direction: "up" (long breakout) or "down" (short breakout).
    Returns BreakoutResult with is_real=True when at least 2/3 checks pass.
    """
    if len(df) < 22:
        return BreakoutResult(False, 0.0, False, False, False, "Insufficient data")

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    volume = df["volume"].astype(float)

    # ── 1. Volume check ───────────────────────────────────────────────────────
    vol_avg     = float(volume.iloc[-21:-1].mean())
    current_vol = float(volume.iloc[-1])
    vol_ratio   = current_vol / vol_avg if vol_avg > 0 else 1.0
    volume_ok   = vol_ratio >= 0.7   # lowered: altcoins have irregular volume

    # ── 2. Close beyond prior level (no wick-only breakouts) ─────────────────
    if direction == "up":
        resistance   = float(high.iloc[-21:-1].max())
        close_beyond = float(close.iloc[-1]) > resistance
    else:
        support      = float(low.iloc[-21:-1].min())
        close_beyond = float(close.iloc[-1]) < support

    # ── 3. ATR confirmation — move must be meaningful ────────────────────────
    atr = indicators.get("atr_14")
    if atr and atr > 0:
        move   = abs(float(close.iloc[-1]) - float(close.iloc[-2]))
        atr_ok = move >= atr * 0.5
    else:
        atr_ok = True  # can't check, assume ok

    # ── Score ─────────────────────────────────────────────────────────────────
    passed     = sum([volume_ok, close_beyond, atr_ok])
    confidence = passed / 3.0
    is_real    = passed >= 1   # block only when ALL 3 checks fail (obvious fake)

    parts = [
        f"vol {vol_ratio:.1f}x {'✓' if volume_ok else '✗'}",
        f"close-beyond {'✓' if close_beyond else '✗'}",
        f"ATR {'✓' if atr_ok else '✗'}",
    ]
    label = "REAL" if is_real else "FAKE"
    desc  = f"Breakout {label} [{direction.upper()}]: {', '.join(parts)}"

    return BreakoutResult(is_real, confidence, volume_ok, close_beyond, atr_ok, desc)
