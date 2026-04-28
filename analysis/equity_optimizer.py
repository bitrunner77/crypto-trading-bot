"""
analysis/equity_optimizer.py — Analyze equity curve health and suggest size adjustments.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List

logger = logging.getLogger("cryptobot.equity_opt")


@dataclass
class EquityAdvice:
    health:           str    # "good" | "warning" | "critical"
    rolling_win_rate: float
    rolling_sharpe:   float
    rolling_drawdown: float
    suggestions:      List[str]
    size_adjustment:  float  # multiply signal.size_pct by this (0.5–1.5)


def analyze_equity(
    equity_curve: List[float],
    recent_trades: List[Dict],
    window: int = 20,
) -> EquityAdvice:
    """
    equity_curve: list of portfolio values (appended each round).
    recent_trades: dicts with at least {"pnl": float | None}.
    """
    if len(equity_curve) < max(window // 2, 5):
        return EquityAdvice("good", 0.5, 0.0, 0.0, ["Insufficient history"], 1.0)

    w = equity_curve[-window:]

    # ── Rolling drawdown ──────────────────────────────────────────────────────
    peak     = max(w)
    current  = w[-1]
    drawdown = (peak - current) / peak if peak > 0 else 0.0

    # ── Rolling Sharpe ────────────────────────────────────────────────────────
    rets = [(w[i] - w[i-1]) / w[i-1] for i in range(1, len(w)) if w[i-1] > 0]
    if len(rets) > 2:
        mean_r = sum(rets) / len(rets)
        std_r  = math.sqrt(sum((r - mean_r)**2 for r in rets) / len(rets))
        sharpe = mean_r / std_r * math.sqrt(365) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # ── Rolling win rate (last 10 closed trades) ──────────────────────────────
    closed   = [t for t in recent_trades if t.get("pnl") is not None][-10:]
    win_rate = (sum(1 for t in closed if (t.get("pnl") or 0) > 0) / len(closed)
                if closed else 0.5)

    # ── Assess and advise ─────────────────────────────────────────────────────
    suggestions: List[str] = []
    size_adj = 1.0

    if drawdown > 0.20:
        health   = "critical"
        size_adj = 0.5
        suggestions.append(f"Drawdown {drawdown:.1%} — cutting size 50%")
    elif drawdown > 0.10:
        health   = "warning"
        size_adj = 0.75
        suggestions.append(f"Drawdown {drawdown:.1%} — reducing size 25%")
    else:
        health = "good"

    if win_rate < 0.35:
        size_adj = min(size_adj, 0.6)
        suggestions.append(f"Win rate {win_rate:.1%} — reducing aggression")
    elif win_rate > 0.65 and sharpe > 1.0:
        size_adj = min(size_adj * 1.3, 1.5)
        suggestions.append(f"Win rate {win_rate:.1%}, Sharpe {sharpe:.2f} — scaling up")

    if sharpe < -0.5:
        suggestions.append("Negative Sharpe — consider strategy rotation")

    if not suggestions:
        suggestions.append(
            f"Equity healthy: drawdown {drawdown:.1%}, WR {win_rate:.1%}, Sharpe {sharpe:.2f}"
        )

    logger.debug(f"[EQUITY] {health} | dd={drawdown:.2%} wr={win_rate:.1%} adj={size_adj:.2f}")
    return EquityAdvice(health, win_rate, sharpe, drawdown, suggestions, size_adj)
