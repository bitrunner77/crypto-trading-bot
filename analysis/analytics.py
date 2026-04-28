"""
analysis/analytics.py — Normalize raw strategy JSON into StrategyMetrics.

Computes: win_rate, profit_factor, sharpe, max_drawdown,
          consecutive_losses, ev_per_trade.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class StrategyMetrics:
    win_rate: float          # 0.0–1.0
    profit_factor: float     # gross_profit / gross_loss (>1 = profitable)
    sharpe: float            # annualised Sharpe ratio
    max_drawdown: float      # 0.0–1.0 (fraction of peak)
    consecutive_losses: int  # current streak of consecutive losses
    ev_per_trade: float      # expected value per trade in USD

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "sharpe": round(self.sharpe, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "consecutive_losses": self.consecutive_losses,
            "ev_per_trade": round(self.ev_per_trade, 4),
        }


def build_metrics(raw: Dict[str, Any]) -> StrategyMetrics:
    """
    Build StrategyMetrics from the raw dict returned by GET /api/strategies/{id}.
    Falls back to computing from the trades list if aggregated stats are missing.
    """
    perf = raw.get("performance", {})
    trades: List[Dict[str, Any]] = raw.get("trades", [])

    win_rate = _resolve_win_rate(perf, trades)
    profit_factor = _resolve_profit_factor(perf, trades)
    sharpe = _resolve_sharpe(perf, raw.get("pnl_curve", []))
    max_drawdown = _resolve_drawdown(perf, raw.get("pnl_curve", []))
    consecutive_losses = _count_consecutive_losses(trades)
    ev_per_trade = _resolve_ev(perf, trades, win_rate)

    return StrategyMetrics(
        win_rate=win_rate,
        profit_factor=profit_factor,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        consecutive_losses=consecutive_losses,
        ev_per_trade=ev_per_trade,
    )


# ── internal helpers ──────────────────────────────────────────────────────────

def _resolve_win_rate(perf: Dict, trades: List[Dict]) -> float:
    # Accept snake_case (internal) or camelCase (SF API)
    for key in ("win_rate", "winRate", "winRateLive"):
        if key in perf:
            v = perf[key]
            rate = float(v) if v is not None else 0.0
            return rate / 100.0 if rate > 1.0 else rate
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if _pnl(t) > 0)
    return wins / len(trades)


def _resolve_profit_factor(perf: Dict, trades: List[Dict]) -> float:
    for key in ("profit_factor", "profitFactor", "profitFactorLive"):
        if key in perf:
            v = perf[key]
            return float(v) if v is not None else 0.0
    gross_profit = sum(_pnl(t) for t in trades if _pnl(t) > 0)
    gross_loss = abs(sum(_pnl(t) for t in trades if _pnl(t) < 0))
    return gross_profit / gross_loss if gross_loss > 0 else float("inf")


def _resolve_sharpe(perf: Dict, pnl_curve: List[float]) -> float:
    for key in ("sharpe", "sharpeRatio", "sharpe_ratio"):
        if key in perf:
            v = perf[key]
            return float(v) if v is not None else 0.0
    if len(pnl_curve) < 2:
        return 0.0
    returns = [pnl_curve[i] - pnl_curve[i - 1] for i in range(1, len(pnl_curve))]
    mean_r = sum(returns) / len(returns)
    variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
    std_r = math.sqrt(variance) if variance > 0 else 0
    if std_r == 0:
        return 0.0
    return (mean_r / std_r) * math.sqrt(252)  # annualised


def _resolve_drawdown(perf: Dict, pnl_curve: List[float]) -> float:
    for key in ("max_drawdown", "maxDrawdown", "maxDrawdownLive"):
        if key in perf:
            v = perf[key]
            dd = float(v) if v is not None else 0.0
            return dd / 100.0 if dd > 1.0 else dd
    if len(pnl_curve) < 2:
        return 0.0
    peak = pnl_curve[0]
    max_dd = 0.0
    for val in pnl_curve:
        peak = max(peak, val)
        dd = (peak - val) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    return max_dd


def _count_consecutive_losses(trades: List[Dict]) -> int:
    streak = 0
    for t in reversed(trades):
        if _pnl(t) < 0:
            streak += 1
        else:
            break
    return streak


def _resolve_ev(perf: Dict, trades: List[Dict], win_rate: float) -> float:
    for key in ("ev_per_trade", "evPerTrade", "avgPnlPerTrade"):
        if key in perf:
            v = perf[key]
            return float(v) if v is not None else 0.0
    if not trades:
        return 0.0
    total = sum(_pnl(t) for t in trades)
    return total / len(trades)


def _pnl(trade: Dict) -> float:
    return float(trade.get("pnl", trade.get("profit", trade.get("realized_pnl", 0))))
