"""
analysis/trailing_exit.py — Smart trailing exits for the live trading loop.

Per position:
  - Initial SL = entry ± 1.5×ATR
  - Breakeven lock when unrealised profit hits +2%
  - TP1 at entry ± 3×ATR → close 50%, lock SL to breakeven
  - Dynamic trail after TP1: SL trails 1.5×ATR behind price
  - TP2 at entry ± 6×ATR → close remainder
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.trailing_exit")

TRAIL_ATR_MULT = 1.5
TP1_ATR_MULT   = 3.0
TP2_ATR_MULT   = 6.0
BE_TRIGGER_PCT = 0.02


@dataclass
class _Plan:
    symbol:    str
    side:      str    # "long" | "short"
    entry:     float
    sl:        float
    tp1:       float
    tp2:       float
    atr:       float
    tp1_hit:   bool = False
    be_locked: bool = False


@dataclass
class ExitDecision:
    symbol:    str
    action:    str    # "close_full" | "close_partial" | "update_sl" | "hold"
    reason:    str
    new_sl:    Optional[float] = None
    close_pct: float = 1.0


class TrailingExitManager:
    """Call register_position() when a trade opens, evaluate() each loop."""

    def __init__(self) -> None:
        self._plans: Dict[str, _Plan] = {}

    def register_position(
        self, symbol: str, side: str, entry: float, atr: Optional[float]
    ) -> None:
        if symbol in self._plans:
            return
        a = atr or entry * 0.01
        if side == "long":
            sl  = entry - a * TRAIL_ATR_MULT
            tp1 = entry + a * TP1_ATR_MULT
            tp2 = entry + a * TP2_ATR_MULT
        else:
            sl  = entry + a * TRAIL_ATR_MULT
            tp1 = entry - a * TP1_ATR_MULT
            tp2 = entry - a * TP2_ATR_MULT
        self._plans[symbol] = _Plan(symbol, side, entry, sl, tp1, tp2, a)
        logger.info(f"[TRAIL] {symbol} {side} registered — sl={sl:.4f} tp1={tp1:.4f} tp2={tp2:.4f}")

    def remove_position(self, symbol: str) -> None:
        self._plans.pop(symbol, None)

    def evaluate(
        self, symbol: str, price: float, current_atr: Optional[float] = None
    ) -> ExitDecision:
        plan = self._plans.get(symbol)
        if plan is None:
            return ExitDecision(symbol, "hold", "Not tracked")
        atr = current_atr or plan.atr
        return self._eval_long(plan, price, atr) if plan.side == "long" else self._eval_short(plan, price, atr)

    def _eval_long(self, p: _Plan, price: float, atr: float) -> ExitDecision:
        if price <= p.sl:
            return ExitDecision(p.symbol, "close_full",
                                f"SL hit ${price:.4f} <= ${p.sl:.4f}")

        if not p.be_locked and price >= p.entry * (1 + BE_TRIGGER_PCT):
            p.sl, p.be_locked = p.entry, True
            logger.info(f"[TRAIL] {p.symbol} BE locked")

        if not p.tp1_hit and price >= p.tp1:
            p.tp1_hit, p.sl, p.be_locked = True, p.entry, True
            logger.info(f"[TRAIL] {p.symbol} TP1 hit")
            return ExitDecision(p.symbol, "close_partial",
                                f"TP1 hit ${price:.4f}", close_pct=0.5)

        if p.tp1_hit:
            new_sl = max(p.entry, price - atr * TRAIL_ATR_MULT)
            if new_sl > p.sl:
                p.sl = new_sl
                return ExitDecision(p.symbol, "update_sl",
                                    f"Trail SL → ${new_sl:.4f}", new_sl=new_sl)
            if price >= p.tp2:
                return ExitDecision(p.symbol, "close_full", f"TP2 hit ${price:.4f}")

        return ExitDecision(p.symbol, "hold", f"${price:.4f} | SL=${p.sl:.4f}")

    def _eval_short(self, p: _Plan, price: float, atr: float) -> ExitDecision:
        if price >= p.sl:
            return ExitDecision(p.symbol, "close_full",
                                f"SL hit ${price:.4f} >= ${p.sl:.4f}")

        if not p.be_locked and price <= p.entry * (1 - BE_TRIGGER_PCT):
            p.sl, p.be_locked = p.entry, True

        if not p.tp1_hit and price <= p.tp1:
            p.tp1_hit, p.sl, p.be_locked = True, p.entry, True
            return ExitDecision(p.symbol, "close_partial",
                                f"TP1 hit ${price:.4f}", close_pct=0.5)

        if p.tp1_hit:
            new_sl = min(p.entry, price + atr * TRAIL_ATR_MULT)
            if new_sl < p.sl:
                p.sl = new_sl
                return ExitDecision(p.symbol, "update_sl",
                                    f"Trail SL → ${new_sl:.4f}", new_sl=new_sl)
            if price <= p.tp2:
                return ExitDecision(p.symbol, "close_full", f"TP2 hit ${price:.4f}")

        return ExitDecision(p.symbol, "hold", f"${price:.4f} | SL=${p.sl:.4f}")

    @property
    def tracked_symbols(self) -> List[str]:
        return list(self._plans.keys())
