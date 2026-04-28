"""
analysis/adaptation_score.py — 0–100 adaptation score from 5 components.

Components:
  1. regime_fit        — does strategy type match current regime?
  2. transition_probs  — how stable is the current regime?
  3. streak_health     — consecutive loss penalty
  4. vol_alignment     — strategy vol tolerance vs current vol
  5. trend_capture     — did the strategy profit during trending periods?
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from analysis.analytics import StrategyMetrics
from analysis.regime_detector import RegimeResult


# Maps strategy "type" hints (from name/metadata) to regime affinities
_REGIME_AFFINITY: Dict[str, Dict[str, float]] = {
    "momentum":       {"trending_up": 1.0, "trending_down": 0.8, "choppy": 0.2, "mean_reverting": 0.2, "high_vol": 0.6, "low_vol": 0.4},
    "mean_reversion": {"mean_reverting": 1.0, "low_vol": 0.8, "choppy": 0.5, "trending_up": 0.1, "trending_down": 0.1, "high_vol": 0.3},
    "scalping":       {"high_vol": 0.9, "trending_up": 0.7, "trending_down": 0.7, "choppy": 0.6, "mean_reverting": 0.4, "low_vol": 0.2},
    "grid":           {"mean_reverting": 0.9, "choppy": 0.8, "low_vol": 0.7, "trending_up": 0.2, "trending_down": 0.2, "high_vol": 0.4},
    "dca":            {"trending_up": 0.8, "low_vol": 0.7, "mean_reverting": 0.6, "choppy": 0.5, "trending_down": 0.3, "high_vol": 0.4},
    "ai_driven":      {"trending_up": 0.7, "trending_down": 0.7, "mean_reverting": 0.6, "high_vol": 0.6, "low_vol": 0.5, "choppy": 0.5},
}

_DEFAULT_AFFINITY = {r: 0.5 for r in ["trending_up", "trending_down", "mean_reverting", "high_vol", "low_vol", "choppy"]}


@dataclass
class AdaptationBreakdown:
    regime_fit: float        # 0–20
    transition_probs: float  # 0–20
    streak_health: float     # 0–20
    vol_alignment: float     # 0–20
    trend_capture: float     # 0–20
    total: float             # 0–100

    def to_dict(self):
        return {
            "regime_fit": round(self.regime_fit, 2),
            "transition_probs": round(self.transition_probs, 2),
            "streak_health": round(self.streak_health, 2),
            "vol_alignment": round(self.vol_alignment, 2),
            "trend_capture": round(self.trend_capture, 2),
            "total": round(self.total, 2),
        }


def compute_adaptation_score(
    metrics: StrategyMetrics,
    regime: RegimeResult,
    strategy_type: str = "ai_driven",
) -> AdaptationBreakdown:
    """
    Score how well the strategy is adapted to the current market regime.
    Returns AdaptationBreakdown with each component (max 20) and total (max 100).
    """
    affinity = _REGIME_AFFINITY.get(strategy_type.lower(), _DEFAULT_AFFINITY)

    # 1. regime_fit — affinity of strategy type to current regime (0–20)
    fit_raw = affinity.get(regime.regime, 0.5) * regime.confidence
    regime_fit = fit_raw * 20

    # 2. transition_probs — stability of current regime (0–20)
    # High self-transition prob → stable → good. Use the current regime's own transition prob.
    self_prob = regime.transition_probs.get(regime.regime, 0.33)
    transition_score = self_prob * 20

    # 3. streak_health — penalise consecutive losses (0–20)
    # 0 losses → 20 pts, each loss costs 3 pts, floor at 0
    streak_score = max(0.0, 20.0 - metrics.consecutive_losses * 3)

    # 4. vol_alignment — strategy vol preference vs actual vol (0–20)
    # Momentum/scalping prefer high vol; mean_reversion/grid prefer low vol
    high_vol_strategies = {"momentum", "scalping", "ai_driven"}
    low_vol_strategies = {"mean_reversion", "grid", "dca"}
    if strategy_type.lower() in high_vol_strategies:
        vol_score = regime.vol_percentile * 20
    else:
        vol_score = (1.0 - regime.vol_percentile) * 20

    # 5. trend_capture — is EV positive and profit factor healthy? (0–20)
    pf_score = min(metrics.profit_factor / 2.0, 1.0)  # PF of 2.0 → full score
    ev_score = min(max(metrics.ev_per_trade / 100.0, 0.0), 1.0)  # $100 EV → full score
    trend_capture = (pf_score * 0.6 + ev_score * 0.4) * 20

    total = regime_fit + transition_score + streak_score + vol_score + trend_capture

    return AdaptationBreakdown(
        regime_fit=regime_fit,
        transition_probs=transition_score,
        streak_health=streak_score,
        vol_alignment=vol_score,
        trend_capture=trend_capture,
        total=total,
    )
