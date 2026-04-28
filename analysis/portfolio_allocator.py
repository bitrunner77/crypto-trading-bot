"""
analysis/portfolio_allocator.py — Multi-slot capital allocation engine.

Divides available capital into N independent slots, each assigned to a
different coin ranked by the CoinRotator. Slots are rebalanced every
REBALANCE_ROUNDS trading rounds.

Each slot:
  - Has a fixed cash allocation (total_cash / n_slots)
  - Tracks its own open position + P&L
  - Is managed independently (own entry, stop, take-profit)

This turns the bot into a mini-fund running parallel positions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.allocator")


@dataclass
class Slot:
    id:         int
    symbol:     str
    allocated:  float    # USDT allocated to this slot
    in_trade:   bool = False
    entry_px:   float = 0.0
    side:       str   = ""    # "long" | "short"
    unrealised: float = 0.0
    realised:   float = 0.0


@dataclass
class AllocationPlan:
    slots:       List[Slot]
    total_alloc: float
    description: str


class PortfolioAllocator:
    """
    Manages N parallel trading slots across different coins.
    Call update_slots() when rotation scores change.
    Call slot_for(symbol) to find which slot owns a given symbol.
    """

    def __init__(self, n_slots: int = 3, rebalance_every: int = 6) -> None:
        self._n          = n_slots
        self._rebalance  = rebalance_every
        self._slots:     List[Slot] = []
        self._round      = 0

    def allocate(
        self,
        cash:       float,
        ranked_symbols: List[str],
    ) -> AllocationPlan:
        """
        Assign the top N ranked symbols to slots.
        Only reassigns slots that are not currently in a trade.
        """
        self._round += 1
        per_slot = cash / self._n if self._n > 0 else cash

        # Rebuild slots for symbols without open trades
        used_symbols = {s.symbol for s in self._slots if s.in_trade}
        available = [sym for sym in ranked_symbols if sym not in used_symbols]

        new_slots: List[Slot] = []
        assigned = 0

        for i in range(self._n):
            existing = next((s for s in self._slots if s.id == i), None)
            if existing and existing.in_trade:
                existing.allocated = per_slot
                new_slots.append(existing)
            elif assigned < len(available):
                new_slots.append(Slot(
                    id=i, symbol=available[assigned],
                    allocated=per_slot,
                ))
                assigned += 1
            elif existing:
                existing.allocated = per_slot
                new_slots.append(existing)
            else:
                sym = ranked_symbols[i % len(ranked_symbols)] if ranked_symbols else "BTC/USDT:USDT"
                new_slots.append(Slot(id=i, symbol=sym, allocated=per_slot))

        self._slots = new_slots
        desc = " | ".join(f"Slot{s.id}={s.symbol} ${s.allocated:,.0f}" for s in self._slots)
        logger.info(f"[ALLOC] {desc}")
        return AllocationPlan(self._slots, cash, desc)

    def mark_open(self, symbol: str, side: str, entry_px: float) -> None:
        for s in self._slots:
            if s.symbol == symbol and not s.in_trade:
                s.in_trade  = True
                s.side      = side
                s.entry_px  = entry_px
                return

    def mark_closed(self, symbol: str, pnl: float) -> None:
        for s in self._slots:
            if s.symbol == symbol and s.in_trade:
                s.in_trade   = False
                s.realised  += pnl
                s.unrealised = 0.0
                return

    def update_unrealised(self, symbol: str, current_px: float) -> None:
        for s in self._slots:
            if s.symbol == symbol and s.in_trade and s.entry_px > 0:
                pct = (current_px - s.entry_px) / s.entry_px
                if s.side == "short":
                    pct *= -1
                s.unrealised = s.allocated * pct

    @property
    def active_symbols(self) -> List[str]:
        return [s.symbol for s in self._slots]

    @property
    def slots(self) -> List[Slot]:
        return list(self._slots)

    def summary(self) -> str:
        lines = []
        for s in self._slots:
            status = f"LONG" if (s.in_trade and s.side == "long") else \
                     f"SHORT" if (s.in_trade and s.side == "short") else "flat"
            lines.append(
                f"  Slot {s.id}: {s.symbol} [{status}] "
                f"alloc=${s.allocated:,.0f} "
                f"PnL: realised=${s.realised:+.2f} unreal=${s.unrealised:+.2f}"
            )
        return "\n".join(lines)
