"""
analysis/hedging.py — Smart hedging engine.

Opens an opposing position when an existing trade is underwater by
HEDGE_TRIGGER_PCT of its margin, limiting further downside.
Closes the hedge automatically when the main position recovers
to breakeven or hits its TP.

Hedge size = main_margin × HEDGE_RATIO (default 0.5 = 50% hedge).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.hedging")

HEDGE_TRIGGER_PCT = 0.05   # open hedge when unrealised loss > 5% of margin
HEDGE_RATIO       = 0.50   # hedge size = 50% of main position margin
CLOSE_HEDGE_PCT   = 0.01   # close hedge when main recovers to within 1% of entry


@dataclass
class HedgeSignal:
    symbol:     str
    action:     str          # "open_hedge" | "close_hedge" | "hold"
    side:       str          # "long" | "short" (side of the hedge order, opposite of main)
    size_pct:   float        # fraction of cash to use for hedge margin
    reason:     str


class HedgeManager:
    """Checks whether to open or close hedge positions each round."""

    def __init__(self, trigger_pct: float = HEDGE_TRIGGER_PCT,
                 ratio: float = HEDGE_RATIO) -> None:
        self._trigger  = trigger_pct
        self._ratio    = ratio
        self._hedges:  Dict[str, Dict] = {}  # symbol → hedge info

    def evaluate(
        self,
        positions:  List[Dict],
        prices:     Dict[str, float],
        cash:       float,
    ) -> List[HedgeSignal]:
        """
        Evaluate all open positions and return hedge signals.
        positions: list of dicts with keys: symbol, side, entry_price, amount
        """
        signals: List[HedgeSignal] = []

        for pos in positions:
            sym        = pos.get("symbol", "")
            side       = pos.get("side", "")
            entry      = float(pos.get("entry_price") or 0)
            margin     = float(pos.get("amount") or 0)
            current_px = float(prices.get(sym) or entry)

            if not sym or not side or entry == 0:
                continue

            # Unrealised P&L as fraction of margin
            pnl_pct = ((current_px - entry) / entry) * (1 if side == "long" else -1)

            hedge_exists = sym in self._hedges

            # ── Close hedge if main position recovers ─────────────────────────
            if hedge_exists and pnl_pct >= -CLOSE_HEDGE_PCT:
                signals.append(HedgeSignal(
                    symbol=sym, action="close_hedge",
                    side="",    # determined by hedge side stored below
                    size_pct=0.0,
                    reason=f"Main position recovered to {pnl_pct:+.2%}",
                ))
                del self._hedges[sym]
                continue

            # ── Open hedge if loss exceeds trigger and no hedge exists ─────────
            if not hedge_exists and pnl_pct < -self._trigger:
                hedge_side = "short" if side == "long" else "long"
                hedge_size = min(self._ratio * (margin / cash) if cash > 0 else 0.05, 0.25)
                self._hedges[sym] = {"side": hedge_side, "opened_at_pnl": pnl_pct}
                signals.append(HedgeSignal(
                    symbol=sym, action="open_hedge",
                    side=hedge_side,
                    size_pct=hedge_size,
                    reason=(
                        f"Main {side} @ {entry:.2f} down {pnl_pct:.2%} "
                        f"— opening {hedge_side} hedge ({hedge_size:.0%} margin)"
                    ),
                ))
                logger.info(f"[HEDGE] Opening {hedge_side} hedge on {sym}: {signals[-1].reason}")

        return signals

    def mark_hedge_closed(self, symbol: str) -> None:
        self._hedges.pop(symbol, None)

    @property
    def active_hedges(self) -> List[str]:
        return list(self._hedges.keys())
