"""
strategies/mlb_polymarket.py — Edge-finder for Polymarket MLB markets.

Two complementary signals on each binary market:
  1. ARBITRAGE — best ask on outcome A + best ask on outcome B < 1.0 (minus fee).
                 Stake is split so the payout is identical on either outcome.
  2. VALUE     — best ask < model fair value by at least the configured edge.
                 Default model is the market's own last traded price (a weak
                 prior that still catches stale asks against fresher prints);
                 callers may inject a stronger model via `fair_value_fn`.

The strategy is intentionally model-light. Stake sizing uses a fractional Kelly
clamped by `max_stake_pct` of the paper bankroll.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from exchange.polymarket_client import PolymarketMarket

logger = logging.getLogger("cryptobot.strategy.mlb_polymarket")

FairValueFn = Callable[[PolymarketMarket, int], Optional[float]]


@dataclass
class BettingSignal:
    market_id: str
    question: str
    outcome: str
    token_id: str
    outcome_index: int
    stake_usd: float
    limit_price: float           # max price we'll pay per share (0-1)
    edge: float                  # expected profit per $1 staked
    confidence: float            # 0-1
    kind: str                    # "arbitrage" | "value"
    reasoning: str


class MLBPolymarketStrategy:
    """Scans MLB markets and emits BettingSignals."""

    name = "mlb_polymarket"

    def __init__(self, config, fair_value_fn: Optional[FairValueFn] = None):
        self._config = config
        self._fair_value_fn = fair_value_fn or self._default_fair_value
        self._min_edge = float(getattr(config, "polymarket_min_edge", 0.03))
        self._max_stake_pct = float(getattr(config, "polymarket_max_stake_pct", 0.05))
        self._fee_pct = float(getattr(config, "polymarket_fee_pct", 0.0))
        self._min_liquidity = float(getattr(config, "polymarket_min_liquidity", 500.0))

    # ── Public API ─────────────────────────────────────────────────────────

    def evaluate(
        self,
        market: PolymarketMarket,
        order_books: Dict[str, Dict],
        bankroll_usd: float,
    ) -> List[BettingSignal]:
        """Return zero or more signals for one market."""
        if len(market.token_ids) != 2:
            return []  # MLB markets are binary; skip multi-outcome edge cases
        if market.liquidity and market.liquidity < self._min_liquidity:
            return []

        asks = [self._best_ask(order_books.get(tid)) for tid in market.token_ids]
        if any(a is None for a in asks):
            return []

        signals: List[BettingSignal] = []
        signals.extend(self._arbitrage(market, asks, bankroll_usd))
        if not signals:
            signals.extend(self._value(market, asks, bankroll_usd))
        return signals

    # ── Arbitrage ──────────────────────────────────────────────────────────

    def _arbitrage(
        self,
        market: PolymarketMarket,
        asks: List[Dict],
        bankroll_usd: float,
    ) -> List[BettingSignal]:
        a, b = asks
        cost = a["price"] + b["price"]
        # Each $1 of total stake split inversely to price returns $1/cost on
        # whichever side wins. Net profit per $1 = 1/cost - 1 - fees.
        edge = (1.0 / cost) - 1.0 - self._fee_pct
        if edge < self._min_edge:
            return []

        budget = min(bankroll_usd * self._max_stake_pct,
                     a["price"] * a["size"] + b["price"] * b["size"])
        if budget <= 0:
            return []

        # Stake[i] / price[i] equal across sides → equal payout shares.
        shares = budget / cost
        signals = []
        for i, ask in enumerate(asks):
            stake = shares * ask["price"]
            signals.append(BettingSignal(
                market_id=market.id,
                question=market.question,
                outcome=market.outcomes[i],
                token_id=market.token_ids[i],
                outcome_index=i,
                stake_usd=stake,
                limit_price=ask["price"],
                edge=edge,
                confidence=min(0.99, 0.5 + edge * 5),
                kind="arbitrage",
                reasoning=(f"ASK_a+ASK_b={cost:.3f} → guaranteed "
                           f"{edge*100:.2f}% edge after fees"),
            ))
        return signals

    # ── Value betting ──────────────────────────────────────────────────────

    def _value(
        self,
        market: PolymarketMarket,
        asks: List[Dict],
        bankroll_usd: float,
    ) -> List[BettingSignal]:
        signals: List[BettingSignal] = []
        for i, ask in enumerate(asks):
            fair = self._fair_value_fn(market, i)
            if fair is None or not 0 < fair < 1:
                continue
            ask_price = ask["price"]
            edge = fair - ask_price - self._fee_pct
            if edge < self._min_edge:
                continue
            # Fractional Kelly: f* = edge / (1 - ask_price), then quartered.
            kelly = edge / max(1.0 - ask_price, 1e-6)
            stake_pct = min(self._max_stake_pct, max(0.0, kelly * 0.25))
            stake = min(bankroll_usd * stake_pct, ask_price * ask["size"])
            if stake <= 0:
                continue
            signals.append(BettingSignal(
                market_id=market.id,
                question=market.question,
                outcome=market.outcomes[i],
                token_id=market.token_ids[i],
                outcome_index=i,
                stake_usd=stake,
                limit_price=ask_price,
                edge=edge,
                confidence=min(0.95, 0.4 + edge * 4),
                kind="value",
                reasoning=(f"fair={fair:.3f} vs ask={ask_price:.3f} → "
                           f"{edge*100:.2f}% edge (Kelly/4 sized)"),
            ))
        return signals

    # ── Defaults ───────────────────────────────────────────────────────────

    @staticmethod
    def _default_fair_value(market: PolymarketMarket, outcome_index: int) -> Optional[float]:
        if outcome_index >= len(market.last_prices):
            return None
        return market.last_prices[outcome_index] or None

    @staticmethod
    def _best_ask(book: Optional[Dict]) -> Optional[Dict]:
        if not book:
            return None
        asks = book.get("asks") or []
        if not asks:
            return None
        # Polymarket CLOB returns asks sorted ascending; take cheapest.
        # Each level is {price, size} as strings.
        try:
            best = min(asks, key=lambda x: float(x["price"]))
            return {"price": float(best["price"]), "size": float(best["size"])}
        except (KeyError, ValueError, TypeError):
            return None
