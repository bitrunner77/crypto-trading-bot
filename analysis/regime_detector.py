"""
analysis/regime_detector.py — Markov-model market regime classifier.

Regimes: trending_up | trending_down | mean_reverting | high_vol | low_vol | choppy

detect_regime(df) returns a RegimeResult with current regime + transition matrix.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


REGIMES = ["trending_up", "trending_down", "mean_reverting", "high_vol", "low_vol", "choppy"]


@dataclass
class RegimeResult:
    regime: str                                # current regime label
    confidence: float                          # 0.0–1.0
    transition_probs: Dict[str, float]         # P(next regime | current)
    vol_percentile: float                      # 0.0–1.0 relative to history
    trend_strength: float                      # ADX-proxy 0–100
    mean_reversion_score: float               # Hurst exponent proxy 0–1


def detect_regime(df: pd.DataFrame, lookback: int = 20) -> RegimeResult:
    """
    Classify current market regime using a simplified Markov approach:
      1. Compute vol, trend strength, mean-reversion tendency
      2. Score each regime dimension
      3. Pick the dominant regime; build plausible transition probs
    """
    if len(df) < lookback + 5:
        return _unknown_regime()

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # ── Volatility ────────────────────────────────────────────────────────────
    returns = close.pct_change().dropna()
    vol_recent = returns.iloc[-lookback:].std()
    vol_history = returns.std()
    vol_pct = _safe_div(vol_recent, vol_history)  # >1 → high vol

    # ── Trend strength (ADX proxy via directional moves) ──────────────────────
    plus_dm = (high.diff()).clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(lookback).mean().iloc[-1]
    plus_di = _safe_div(plus_dm.rolling(lookback).mean().iloc[-1], atr) * 100
    minus_di = _safe_div(minus_dm.rolling(lookback).mean().iloc[-1], atr) * 100
    dx = _safe_div(abs(plus_di - minus_di), plus_di + minus_di) * 100
    adx = dx  # simplified single-period DX as ADX proxy

    # ── Trend direction ───────────────────────────────────────────────────────
    price_change = (close.iloc[-1] - close.iloc[-lookback]) / close.iloc[-lookback]

    # ── Mean-reversion score (Hurst exponent proxy via variance ratio) ────────
    hurst = _hurst_proxy(returns.iloc[-lookback * 2:].tolist())

    # ── Regime classification ─────────────────────────────────────────────────
    regime, confidence = _classify(adx, price_change, vol_pct, hurst)

    # ── Transition probabilities (heuristic Markov) ───────────────────────────
    transition_probs = _transition_probs(regime, adx, vol_pct, hurst)

    return RegimeResult(
        regime=regime,
        confidence=confidence,
        transition_probs=transition_probs,
        vol_percentile=min(vol_pct, 1.0),
        trend_strength=min(adx, 100.0),
        mean_reversion_score=hurst,
    )


# ── internals ─────────────────────────────────────────────────────────────────

def _classify(
    adx: float, price_change: float, vol_pct: float, hurst: float
) -> tuple[str, float]:
    """Return (regime, confidence) from indicator values."""
    if adx > 30 and vol_pct < 1.3:
        if price_change > 0.01:
            return "trending_up", min(adx / 60, 1.0)
        if price_change < -0.01:
            return "trending_down", min(adx / 60, 1.0)

    if hurst < 0.45:  # anti-persistent → mean-reverting
        return "mean_reverting", (0.45 - hurst) * 4

    if vol_pct > 1.5:
        return "high_vol", min((vol_pct - 1.0) / 1.5, 1.0)

    if vol_pct < 0.6:
        return "low_vol", (1.0 - vol_pct / 0.6)

    return "choppy", 0.5


def _hurst_proxy(returns: List[float]) -> float:
    """
    Simplified variance-ratio Hurst proxy.
    H < 0.5 → mean-reverting, H ≈ 0.5 → random walk, H > 0.5 → trending.
    """
    if len(returns) < 8:
        return 0.5
    var_1 = _variance(returns)
    var_2 = _variance([returns[i] + returns[i + 1] for i in range(0, len(returns) - 1, 2)])
    ratio = _safe_div(var_2, 2 * var_1) if var_1 > 0 else 1.0
    return max(0.0, min(1.0, ratio * 0.5 + 0.25))


def _variance(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mean = sum(xs) / len(xs)
    return sum((x - mean) ** 2 for x in xs) / len(xs)


def _transition_probs(regime: str, adx: float, vol_pct: float, hurst: float) -> Dict[str, float]:
    """
    Heuristic transition probability vector for the current regime.
    Probabilities sum to 1.0.
    """
    base = {r: 0.05 for r in REGIMES}  # small base probability for all

    if regime == "trending_up":
        base.update({"trending_up": 0.50, "choppy": 0.20, "trending_down": 0.10, "high_vol": 0.10})
    elif regime == "trending_down":
        base.update({"trending_down": 0.50, "choppy": 0.20, "trending_up": 0.10, "high_vol": 0.10})
    elif regime == "mean_reverting":
        base.update({"mean_reverting": 0.45, "choppy": 0.25, "low_vol": 0.15})
    elif regime == "high_vol":
        base.update({"high_vol": 0.35, "trending_up": 0.20, "trending_down": 0.20, "choppy": 0.15})
    elif regime == "low_vol":
        base.update({"low_vol": 0.40, "mean_reverting": 0.25, "choppy": 0.20})
    else:  # choppy
        base.update({"choppy": 0.35, "mean_reverting": 0.20, "trending_up": 0.15, "trending_down": 0.15})

    total = sum(base.values())
    return {k: round(v / total, 4) for k, v in base.items()}


def _safe_div(a: float, b: float) -> float:
    return a / b if b != 0 else 0.0


def _unknown_regime() -> RegimeResult:
    uniform = {r: round(1 / len(REGIMES), 4) for r in REGIMES}
    return RegimeResult(
        regime="choppy",
        confidence=0.0,
        transition_probs=uniform,
        vol_percentile=0.5,
        trend_strength=0.0,
        mean_reversion_score=0.5,
    )
