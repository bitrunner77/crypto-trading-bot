"""
analysis/coin_rotator.py — Rank configured trading pairs by composite score.

Scoring (weighted):
  40% — 24h momentum (price_change_24h)
  30% — trend alignment (EMA9 > EMA21 > EMA50)
  20% — volume activity (current / 20-candle average)
  10% — RSI proximity to actionable zone (40–60 = neutral, penalised)

Returns sorted list; top N are recommended for active trading.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from analysis.indicators import compute_indicators

logger = logging.getLogger("cryptobot.coin_rotator")


@dataclass
class CoinScore:
    symbol:       str
    momentum:     float   # 24h price change %
    trend:        float   # 0.0–1.0
    volume:       float   # volume ratio vs avg
    composite:    float   # final weighted score
    recommended:  bool


class CoinRotator:
    """Score all configured pairs and return the top N to trade."""

    def __init__(self, max_active: int = 2) -> None:
        self._max = max_active
        self._last_scores: List[CoinScore] = []

    async def rank(self, fetcher, timeframe: str = "1h") -> List[CoinScore]:
        """Fetch recent data for every configured pair and rank them."""
        from config import settings
        scores: List[CoinScore] = []

        for sym in settings.trading_pairs:
            try:
                df = await fetcher.fetch_ohlcv(sym, timeframe, limit=50, use_cache=True)
                if df is None or len(df) < 26:
                    continue
                ind = compute_indicators(df)
                score = self._score(sym, df, ind)
                scores.append(score)
            except Exception as exc:
                logger.debug(f"[ROTATOR] Could not score {sym}: {exc}")

        scores.sort(key=lambda s: s.composite, reverse=True)
        for i, s in enumerate(scores):
            s.recommended = i < self._max
        self._last_scores = scores
        return scores

    @property
    def top_pairs(self) -> List[str]:
        return [s.symbol for s in self._last_scores if s.recommended]

    @property
    def last_scores(self) -> List[CoinScore]:
        return self._last_scores

    # ── internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _score(symbol: str, df: pd.DataFrame, ind: Dict) -> CoinScore:
        # Momentum: normalise 24h change to 0–1
        mom_raw = ind.get("price_change_24h") or 0.0
        mom_score = min(max((mom_raw + 0.10) / 0.20, 0.0), 1.0)  # ±10% range → 0–1

        # Trend alignment
        ema9, ema21, ema50 = ind.get("ema_9"), ind.get("ema_21"), ind.get("ema_50")
        if ema9 and ema21 and ema50:
            if ema9 > ema21 > ema50:
                trend_score = 1.0
            elif ema9 < ema21 < ema50:
                trend_score = 0.0
            else:
                trend_score = 0.5
        else:
            trend_score = 0.5

        # Volume ratio (clamp at 3× for scoring)
        vol_ratio  = ind.get("volume_ratio") or 1.0
        vol_score  = min(vol_ratio / 3.0, 1.0)

        # RSI actionability — extremes (overbought/oversold) score higher
        rsi = ind.get("rsi_14") or 50.0
        rsi_score = abs(rsi - 50.0) / 50.0   # 0 at RSI=50, 1 at RSI=0 or 100

        composite = (
            0.40 * mom_score
            + 0.30 * trend_score
            + 0.20 * vol_score
            + 0.10 * rsi_score
        )

        return CoinScore(
            symbol=symbol,
            momentum=round(mom_raw * 100, 2),
            trend=round(trend_score, 3),
            volume=round(vol_ratio, 2),
            composite=round(composite, 4),
            recommended=False,
        )
