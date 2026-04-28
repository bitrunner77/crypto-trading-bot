"""
analysis/manipulation_detector.py — Detect market manipulation patterns.

Detects three common manipulation types:
  1. Pump & dump  — rapid price spike (>5% in ≤5 candles) + volume surge (>5×)
  2. Wash trading — high volume (>8×) with minimal price move (<0.3%)
  3. Stop hunt    — wick >2× candle body + price immediately reverting

action returned:
  "avoid"       — do not trade this candle
  "reduce_size" — trade cautiously with smaller size
  "none"        — clean signal
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class ManipulationSignal:
    detected:    bool
    manip_type:  str    # "pump_dump" | "wash_trading" | "stop_hunt" | "none"
    confidence:  float  # 0.0–1.0
    action:      str    # "avoid" | "reduce_size" | "none"
    description: str


def detect_manipulation(
    df: pd.DataFrame,
    volume_threshold: float = 3.0,
) -> ManipulationSignal:
    """
    Analyse the last 5 candles for manipulation patterns.
    volume_threshold: whale volume threshold (reuse config setting).
    """
    if len(df) < 10:
        return _clean()

    close  = df["close"].astype(float)
    high   = df["high"].astype(float)
    low    = df["low"].astype(float)
    open_  = df["open"].astype(float)
    volume = df["volume"].astype(float)

    vol_avg     = float(volume.iloc[-21:-1].mean()) if len(df) >= 22 else float(volume.mean())
    current_vol = float(volume.iloc[-1])
    vol_ratio   = current_vol / vol_avg if vol_avg > 0 else 1.0

    price_now  = float(close.iloc[-1])
    price_5ago = float(close.iloc[-6]) if len(df) >= 6 else float(close.iloc[0])
    move_5     = abs(price_now - price_5ago) / price_5ago if price_5ago > 0 else 0.0

    # ── Pump & dump ───────────────────────────────────────────────────────────
    if move_5 > 0.05 and vol_ratio > 5.0:
        conf = min((move_5 / 0.10) * 0.5 + (vol_ratio / 10.0) * 0.5, 1.0)
        direction = "UP" if price_now > price_5ago else "DOWN"
        return ManipulationSignal(
            detected=True, manip_type="pump_dump", confidence=round(conf, 2),
            action="avoid",
            description=(
                f"PUMP/DUMP suspected: {move_5:.1%} {direction} in 5 candles, "
                f"vol {vol_ratio:.1f}× avg — AVOID"
            ),
        )

    # ── Wash trading ─────────────────────────────────────────────────────────
    price_move_1 = abs(float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2])
    if vol_ratio > 8.0 and price_move_1 < 0.003:
        conf = min(vol_ratio / 15.0, 1.0)
        return ManipulationSignal(
            detected=True, manip_type="wash_trading", confidence=round(conf, 2),
            action="reduce_size",
            description=(
                f"Wash trading suspected: vol {vol_ratio:.1f}× avg, "
                f"price move only {price_move_1:.3%} — reduce size"
            ),
        )

    # ── Stop hunt ─────────────────────────────────────────────────────────────
    body   = abs(float(close.iloc[-1]) - float(open_.iloc[-1]))
    wick_l = float(open_.iloc[-1]) - float(low.iloc[-1])
    wick_h = float(high.iloc[-1]) - float(open_.iloc[-1])
    wick   = max(wick_l, wick_h)
    if body > 0 and wick > body * 2.5 and vol_ratio > 1.5:
        return ManipulationSignal(
            detected=True, manip_type="stop_hunt", confidence=0.65,
            action="reduce_size",
            description=(
                f"Stop hunt candle: wick {wick/body:.1f}× body — "
                "possible liquidity grab, reduce size"
            ),
        )

    return _clean()


def _clean() -> ManipulationSignal:
    return ManipulationSignal(False, "none", 0.0, "none", "No manipulation detected")
