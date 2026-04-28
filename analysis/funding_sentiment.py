"""
analysis/funding_sentiment.py — Perpetual funding rate as a contrarian edge.

High positive funding → longs overcrowded → SHORT bias.
High negative funding → shorts overcrowded → LONG bias.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("cryptobot.funding")

NEUTRAL_BAND  = 0.0001   # ±0.01% per 8h period = noise
FULL_STRENGTH = 0.0005   # 0.05% per 8h = strong signal


@dataclass
class FundingSignal:
    rate: Optional[float]   # raw rate per 8h (e.g. 0.0001)
    annualized_pct: float   # rate × 1095 × 100
    bias: str               # "long" | "short" | "neutral"
    strength: float         # 0.0–1.0
    description: str


def fetch_funding_sentiment(exchange, symbol: str) -> FundingSignal:
    """Fetch latest funding rate from exchange and derive a contrarian signal."""
    try:
        info = exchange.fetch_funding_rate(symbol)
        rate = float(info.get("fundingRate") or info.get("funding_rate") or 0.0)
    except Exception as exc:
        logger.debug(f"Funding fetch failed for {symbol}: {exc}")
        return FundingSignal(None, 0.0, "neutral", 0.0, "Funding rate unavailable")

    ann_pct = rate * 1095 * 100  # 3 settlements/day × 365 days

    if rate > NEUTRAL_BAND:
        strength = min(rate / FULL_STRENGTH, 1.0)
        bias = "short"
        desc = (f"Funding +{rate*100:.4f}%/8h ({ann_pct:+.1f}% ann) "
                f"— longs overcrowded → SHORT edge")
    elif rate < -NEUTRAL_BAND:
        strength = min(abs(rate) / FULL_STRENGTH, 1.0)
        bias = "long"
        desc = (f"Funding {rate*100:.4f}%/8h ({ann_pct:+.1f}% ann) "
                f"— shorts overcrowded → LONG edge")
    else:
        strength = 0.0
        bias = "neutral"
        desc = f"Funding {rate*100:.4f}%/8h — neutral sentiment"

    return FundingSignal(rate, ann_pct, bias, strength, desc)
