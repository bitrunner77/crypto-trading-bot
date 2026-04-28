"""
analysis/session_timer.py — Trading session detector and sniper mode.

Sessions (UTC):
  Asia:    00:00–09:00  (low liquidity → reduce size)
  London:  08:00–17:00  (high volatility → +20%)
  New York: 13:00–22:00  (high volatility → +20%)
  Overlap:  13:00–17:00  (SNIPER ZONE: max liquidity → +50%)
  Off-hours: 22:00–00:00  (very low → reduce 50%)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List


@dataclass
class SessionInfo:
    sessions: List[str]
    is_sniper_zone: bool
    size_multiplier: float  # apply to signal.size_pct before placing order
    description: str


def get_session_info(dt: datetime | None = None) -> SessionInfo:
    """Return current session state and the position-size multiplier to apply."""
    if dt is None:
        dt = datetime.now(timezone.utc)

    hour = dt.hour + dt.minute / 60.0

    in_asia    = 0.0  <= hour < 9.0
    in_london  = 8.0  <= hour < 17.0
    in_ny      = 13.0 <= hour < 22.0
    in_overlap = 13.0 <= hour < 17.0

    sessions: List[str] = []
    if in_asia:
        sessions.append("Asia")
    if in_london:
        sessions.append("London")
    if in_ny:
        sessions.append("NY")
    if not sessions:
        sessions = ["Off-hours"]

    if in_overlap:
        multiplier = 1.5
        desc = "SNIPER ZONE (London+NY) — +50% size"
    elif in_london or in_ny:
        multiplier = 1.2
        desc = f"{'/'.join(sessions)} session — +20% size"
    elif in_asia:
        multiplier = 0.7
        desc = "Asia session — -30% size (low liquidity)"
    else:
        multiplier = 0.5
        desc = "Off-hours — -50% size"

    return SessionInfo(
        sessions=sessions,
        is_sniper_zone=in_overlap,
        size_multiplier=multiplier,
        description=desc,
    )
