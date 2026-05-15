"""
exchange/polymarket_client.py — Read-only Polymarket client + paper-bet ledger
focused on MLB markets.

Polymarket exposes two public HTTP APIs that need no auth for reads:
  • Gamma  (https://gamma-api.polymarket.com)  — market metadata + tags
  • CLOB   (https://clob.polymarket.com)       — order books, midpoints

Live order placement requires an EOA signature flow that we deliberately do
NOT implement here; the live path is left as a stub so paper mode is safe to
ship and live trading can be wired up later behind explicit credentials.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp

from utils.helpers import retry_async

logger = logging.getLogger("cryptobot.polymarket")

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

MLB_TAG_SLUGS = ("mlb", "baseball", "mlb-baseball")
MLB_KEYWORDS = ("mlb", "yankees", "red sox", "dodgers", "astros", "cubs",
                "mets", "braves", "phillies", "giants", "padres", "rangers",
                "blue jays", "orioles", "guardians", "mariners", "rays",
                "twins", "tigers", "white sox", "royals", "athletics",
                "angels", "diamondbacks", "rockies", "marlins", "nationals",
                "pirates", "reds", "brewers", "cardinals")


# ───────────────────────────── Data classes ────────────────────────────────

@dataclass
class PolymarketMarket:
    """A simplified, normalized view of a Polymarket binary market."""
    id: str
    question: str
    slug: str
    end_date: Optional[str]
    outcomes: List[str]                 # e.g. ["Yes", "No"] or team names
    token_ids: List[str]                # CLOB token id per outcome
    last_prices: List[float]            # last traded prices per outcome (0-1)
    volume: float = 0.0
    liquidity: float = 0.0
    category: str = ""

    @classmethod
    def from_gamma(cls, raw: Dict) -> Optional["PolymarketMarket"]:
        try:
            outcomes = _maybe_json(raw.get("outcomes")) or []
            token_ids = _maybe_json(raw.get("clobTokenIds")) or []
            prices = _maybe_json(raw.get("outcomePrices")) or []
            if not outcomes or not token_ids or len(outcomes) != len(token_ids):
                return None
            return cls(
                id=str(raw.get("id") or raw.get("conditionId") or ""),
                question=raw.get("question", ""),
                slug=raw.get("slug", ""),
                end_date=raw.get("endDate"),
                outcomes=[str(o) for o in outcomes],
                token_ids=[str(t) for t in token_ids],
                last_prices=[float(p) for p in prices] if prices else [0.0] * len(outcomes),
                volume=float(raw.get("volumeNum") or raw.get("volume") or 0.0),
                liquidity=float(raw.get("liquidityNum") or raw.get("liquidity") or 0.0),
                category=raw.get("category", ""),
            )
        except (ValueError, TypeError) as exc:
            logger.debug(f"Skipping malformed market {raw.get('id')}: {exc}")
            return None


@dataclass
class PaperBet:
    id: str
    market_id: str
    question: str
    outcome: str
    token_id: str
    stake_usd: float
    limit_price: float            # what we paid per share (0-1)
    shares: float                 # stake / price
    placed_at: str
    settled: bool = False
    payout_usd: float = 0.0       # 0 if loss, shares*1.0 if win


# ───────────────────────────── HTTP client ─────────────────────────────────

class PolymarketClient:
    """Async read-only client for Polymarket. Optionally backs a paper book."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None,
                 timeout: float = 10.0):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "PolymarketClient":
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    @retry_async(attempts=3)
    async def _get(self, url: str, params: Optional[Dict] = None) -> Dict | List:
        session = self._ensure_session()
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ── Markets ────────────────────────────────────────────────────────────

    async def fetch_mlb_markets(self, limit: int = 100,
                                active_only: bool = True) -> List[PolymarketMarket]:
        """Return active MLB markets via Gamma, falling back to keyword search."""
        params = {
            "limit": str(limit),
            "active": "true" if active_only else "false",
            "closed": "false",
            "order": "volumeNum",
            "ascending": "false",
        }
        # Gamma supports filtering by tag slug, but slugs change across seasons.
        # Try each known slug and merge results.
        markets: Dict[str, PolymarketMarket] = {}
        for slug in MLB_TAG_SLUGS:
            try:
                data = await self._get(f"{GAMMA_URL}/markets",
                                       params={**params, "tag_slug": slug})
            except Exception as exc:
                logger.debug(f"tag_slug={slug} failed: {exc}")
                continue
            for raw in (data if isinstance(data, list) else []):
                m = PolymarketMarket.from_gamma(raw)
                if m and self._looks_mlb(m):
                    markets[m.id] = m

        # Fallback: keyword filter over a generic query if tag lookups returned nothing
        if not markets:
            try:
                data = await self._get(f"{GAMMA_URL}/markets", params=params)
                for raw in (data if isinstance(data, list) else []):
                    m = PolymarketMarket.from_gamma(raw)
                    if m and self._looks_mlb(m):
                        markets[m.id] = m
            except Exception as exc:
                logger.warning(f"MLB market keyword fallback failed: {exc}")

        return list(markets.values())

    async def fetch_orderbook(self, token_id: str) -> Dict:
        """Return CLOB order book: {bids: [{price, size}], asks: [...]}."""
        return await self._get(f"{CLOB_URL}/book", params={"token_id": token_id})

    async def fetch_midpoint(self, token_id: str) -> Optional[float]:
        try:
            data = await self._get(f"{CLOB_URL}/midpoint", params={"token_id": token_id})
            return float(data["mid"]) if isinstance(data, dict) and "mid" in data else None
        except Exception as exc:
            logger.debug(f"midpoint fetch failed for {token_id}: {exc}")
            return None

    @staticmethod
    def _looks_mlb(m: PolymarketMarket) -> bool:
        text = f"{m.question} {m.slug} {m.category}".lower()
        return any(k in text for k in MLB_KEYWORDS)


# ───────────────────────────── Paper book ──────────────────────────────────

@dataclass
class PolymarketPaperBook:
    """In-memory paper betting ledger. Independent of the crypto PaperExchange."""
    balance: float
    bets: List[PaperBet] = field(default_factory=list)

    def place_bet(self, market: PolymarketMarket, outcome_index: int,
                  limit_price: float, stake_usd: float) -> PaperBet:
        if stake_usd <= 0:
            raise ValueError("stake must be positive")
        if not 0 < limit_price < 1:
            raise ValueError(f"limit_price must be in (0, 1), got {limit_price}")
        if stake_usd > self.balance:
            raise ValueError(f"insufficient paper balance "
                             f"(${self.balance:.2f} < ${stake_usd:.2f})")
        if not 0 <= outcome_index < len(market.outcomes):
            raise ValueError(f"invalid outcome index {outcome_index}")

        self.balance -= stake_usd
        bet = PaperBet(
            id=str(uuid.uuid4())[:8],
            market_id=market.id,
            question=market.question,
            outcome=market.outcomes[outcome_index],
            token_id=market.token_ids[outcome_index],
            stake_usd=stake_usd,
            limit_price=limit_price,
            shares=stake_usd / limit_price,
            placed_at=datetime.utcnow().isoformat(),
        )
        self.bets.append(bet)
        logger.info(
            f"[PAPER-BET] {market.question} → {bet.outcome} | "
            f"${stake_usd:.2f} @ {limit_price:.3f} | shares={bet.shares:.2f} | "
            f"balance=${self.balance:.2f}"
        )
        return bet

    def settle(self, bet_id: str, won: bool) -> PaperBet:
        for bet in self.bets:
            if bet.id == bet_id and not bet.settled:
                bet.settled = True
                bet.payout_usd = bet.shares if won else 0.0
                self.balance += bet.payout_usd
                logger.info(
                    f"[PAPER-BET] SETTLE {bet.id} ({bet.outcome}) → "
                    f"{'WIN' if won else 'LOSS'} | payout=${bet.payout_usd:.2f} | "
                    f"balance=${self.balance:.2f}"
                )
                return bet
        raise KeyError(f"bet {bet_id} not found or already settled")

    def open_bets(self) -> List[PaperBet]:
        return [b for b in self.bets if not b.settled]


# ───────────────────────────── Helpers ─────────────────────────────────────

def _maybe_json(value):
    """Polymarket returns several fields as JSON-encoded strings."""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None
