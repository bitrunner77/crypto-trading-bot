"""
analysis/decision_engine.py — Phase 3: Decision Engine.

BASE VERDICT rules (from config thresholds):
  WR < 45%           → PAUSE
  drawdown > 20%     → PAUSE
  profit_factor < 1.05 → PAUSE
  consecutive_losses > 6 → PAUSE
  Paused bots need WR ≥ 52% to REACTIVATE (asymmetric)

LEARNING OVERRIDE:
  If adaptation_score ≥ 70 AND regret_rate is high → PAUSE → HOLD

FINAL VERDICTS: PAUSE | HOLD | REACTIVATE | INSUFFICIENT
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from analysis.analytics import StrategyMetrics


# ── Verdict constants ─────────────────────────────────────────────────────────
PAUSE        = "PAUSE"
HOLD         = "HOLD"
REACTIVATE   = "REACTIVATE"
INSUFFICIENT = "INSUFFICIENT"


@dataclass
class VerdictResult:
    verdict: str             # PAUSE | HOLD | REACTIVATE | INSUFFICIENT
    base_verdict: str        # pre-override verdict
    override_applied: bool
    override_reason: str
    reasons: list[str]       # human-readable list of triggered rules


def evaluate_bot(
    metrics: StrategyMetrics,
    is_paused: bool,
    has_sufficient_data: bool,
    wr_reactivate_threshold: float = 0.52,
    wr_pause_threshold: float = 0.45,
    dd_pause_threshold: float = 0.20,
    pf_pause_threshold: float = 1.05,
    consec_loss_limit: int = 6,
) -> str:
    """
    Evaluate base verdict from threshold rules.
    Returns one of: PAUSE | HOLD | REACTIVATE | INSUFFICIENT
    """
    if not has_sufficient_data:
        return INSUFFICIENT

    triggered: list[str] = []

    if metrics.win_rate < wr_pause_threshold:
        triggered.append(f"WR {metrics.win_rate:.1%} < {wr_pause_threshold:.0%}")
    if metrics.max_drawdown > dd_pause_threshold:
        triggered.append(f"DD {metrics.max_drawdown:.1%} > {dd_pause_threshold:.0%}")
    if metrics.profit_factor < pf_pause_threshold:
        triggered.append(f"PF {metrics.profit_factor:.2f} < {pf_pause_threshold}")
    if metrics.consecutive_losses > consec_loss_limit:
        triggered.append(f"Consec. losses {metrics.consecutive_losses} > {consec_loss_limit}")

    if triggered:
        return PAUSE

    # Asymmetric reactivation: paused bots need higher WR to come back
    if is_paused:
        if metrics.win_rate >= wr_reactivate_threshold:
            return REACTIVATE
        return PAUSE  # still paused — not recovered enough

    return HOLD


def enhanced_verdict(
    base_verdict: str,
    adaptation_score: float,
    regret_rate: float,
    adapt_override_threshold: float = 70.0,
    regret_high_threshold: float = 0.30,
) -> VerdictResult:
    """
    Apply learning override:
      If base_verdict == PAUSE and adaptation_score ≥ 70 and regret_rate is high
      → downgrade PAUSE to HOLD (the strategy may still be viable).

    Returns VerdictResult with final verdict + metadata.
    """
    override_applied = False
    override_reason = ""
    final = base_verdict

    if (
        base_verdict == PAUSE
        and adaptation_score >= adapt_override_threshold
        and regret_rate >= regret_high_threshold
    ):
        final = HOLD
        override_applied = True
        override_reason = (
            f"Adaptation score {adaptation_score:.1f} ≥ {adapt_override_threshold} "
            f"and regret rate {regret_rate:.1%} is high — PAUSE → HOLD"
        )

    return VerdictResult(
        verdict=final,
        base_verdict=base_verdict,
        override_applied=override_applied,
        override_reason=override_reason,
        reasons=[override_reason] if override_reason else [],
    )
