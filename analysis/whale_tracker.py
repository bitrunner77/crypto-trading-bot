"""
analysis/whale_tracker.py — Detect whale volume activity from OHLCV data.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class WhaleSignal:
    detected: bool
    direction: str          # "buy" | "sell" | "neutral"
    volume_ratio: float     # current / 20-candle average
    strength: str           # "mild" | "strong" | "extreme"
    description: str


def detect_whale_activity(df: pd.DataFrame, threshold: float = 3.0) -> WhaleSignal:
    """
    Returns a WhaleSignal if latest candle volume exceeds threshold × 20-candle average.
    Direction inferred from candle body (green = buy pressure, red = sell pressure).
    """
    if len(df) < 22:
        return WhaleSignal(False, "neutral", 1.0, "mild", "Insufficient data")

    vol   = df["volume"].astype(float)
    close = df["close"].astype(float)
    open_ = df["open"].astype(float)

    # Average excludes the current candle to avoid self-reference
    vol_avg     = float(vol.iloc[-21:-1].mean())
    current_vol = float(vol.iloc[-1])
    ratio       = current_vol / vol_avg if vol_avg > 0 else 1.0

    if ratio < threshold:
        return WhaleSignal(False, "neutral", ratio, "mild",
                           f"Normal volume ({ratio:.1f}x avg)")

    body      = float(close.iloc[-1]) - float(open_.iloc[-1])
    direction = "buy" if body > 0 else ("sell" if body < 0 else "neutral")
    strength  = "extreme" if ratio >= 10.0 else ("strong" if ratio >= 5.0 else "mild")
    desc      = f"WHALE {direction.upper()} — {ratio:.1f}x avg volume ({strength})"
    return WhaleSignal(True, direction, ratio, strength, desc)
