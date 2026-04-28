"""
analysis/hindsight.py — Hindsight analysis: regret rate and calibration updates.

Tracks whether past PAUSE decisions missed profitable runs.
Updates calibration thresholds in learning_state.json accordingly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class HindsightRecord:
    timestamp: str
    bot_id: str
    decision: str           # "RUN" | "PAUSE" | "SWITCH"
    regime_at_decision: str
    adaptation_score: float
    pnl_since: float        # PnL realised after the decision (updated later)
    was_regret: bool = False  # True if PAUSE but market was profitable


@dataclass
class HindsightResult:
    regret_rate: float                     # fraction of PAUSE decisions that were wrong
    missed_pnl: float                      # total PnL missed by incorrect PAUSEs
    calibration_delta: float               # suggested change to PAUSE threshold
    new_pause_threshold: float             # updated threshold for future decisions
    records_evaluated: int


def evaluate_hindsight(learning_state: Dict[str, Any]) -> HindsightResult:
    """
    Evaluate all PAUSE decisions in hindsight_records.
    A PAUSE is a "regret" if the market gained >2% while the bot was paused.
    Adjusts the PAUSE threshold downward if regret rate is high.
    """
    records: List[Dict] = learning_state.get("hindsight_records", [])
    current_threshold: float = learning_state.get("calibration", {}).get("pause_threshold", 40.0)

    if not records:
        return HindsightResult(
            regret_rate=0.0,
            missed_pnl=0.0,
            calibration_delta=0.0,
            new_pause_threshold=current_threshold,
            records_evaluated=0,
        )

    pause_records = [r for r in records if r.get("decision") == "PAUSE"]
    regrets = [r for r in pause_records if r.get("pnl_since", 0.0) > 2.0]

    regret_rate = len(regrets) / len(pause_records) if pause_records else 0.0
    missed_pnl = sum(r.get("pnl_since", 0.0) for r in regrets)

    # If >40% of PAUSEs were regrets → lower the pause threshold by up to 5 pts
    calibration_delta = 0.0
    if regret_rate > 0.40:
        calibration_delta = -min(regret_rate * 10, 5.0)
    elif regret_rate < 0.10 and len(pause_records) >= 5:
        # Very few regrets → threshold might be too loose, tighten slightly
        calibration_delta = min((0.10 - regret_rate) * 10, 2.0)

    new_threshold = max(20.0, min(60.0, current_threshold + calibration_delta))

    return HindsightResult(
        regret_rate=round(regret_rate, 4),
        missed_pnl=round(missed_pnl, 2),
        calibration_delta=round(calibration_delta, 2),
        new_pause_threshold=round(new_threshold, 2),
        records_evaluated=len(records),
    )


def record_decision(
    learning_state: Dict[str, Any],
    bot_id: str,
    decision: str,
    regime: str,
    adaptation_score: float,
) -> Dict[str, Any]:
    """Append a new decision record to hindsight_records in learning_state."""
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "bot_id": bot_id,
        "decision": decision,
        "regime_at_decision": regime,
        "adaptation_score": adaptation_score,
        "pnl_since": 0.0,  # filled in on next run
        "was_regret": False,
    }
    learning_state.setdefault("hindsight_records", []).append(record)

    # Keep only the last 500 records
    learning_state["hindsight_records"] = learning_state["hindsight_records"][-500:]
    return record


def update_pnl_since(
    learning_state: Dict[str, Any],
    bot_id: str,
    pnl_since: float,
) -> None:
    """Update the most recent record for bot_id with realised PnL since decision."""
    records = learning_state.get("hindsight_records", [])
    for rec in reversed(records):
        if rec["bot_id"] == bot_id and rec["pnl_since"] == 0.0:
            rec["pnl_since"] = pnl_since
            rec["was_regret"] = rec["decision"] == "PAUSE" and pnl_since > 2.0
            break
