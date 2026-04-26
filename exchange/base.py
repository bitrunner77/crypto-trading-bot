"""
exchange/base.py — Abstract base class that all exchange clients must implement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ExchangeClient(ABC):
    """Unified interface for leveraged-perp exchange operations (live or paper).

    Side semantics for `create_order`:
        - "long"  → open or add to a long  perp position
        - "short" → open or add to a short perp position
        - "close" → fully reduce / close the existing position on `symbol`

    `amount` is denominated in *quote-currency margin* (USDT/USDC). Concrete
    clients are responsible for converting margin × leverage / price into
    contract amount when the exchange requires that, and for setting leverage
    before order submission.
    """

    @abstractmethod
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str,
        since: Optional[int] = None, limit: int = 200,
    ) -> List[List]:
        """Return raw OHLCV candles: [[ts, o, h, l, c, v], ...]"""

    @abstractmethod
    async def fetch_ticker(self, symbol: str) -> Dict:
        """Return current ticker: {last, bid, ask, volume, ...}"""

    @abstractmethod
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Dict]:
        """Return tickers for multiple symbols."""

    @abstractmethod
    async def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict:
        """Return order book: {bids: [[price, size], ...], asks: [...]}"""

    @abstractmethod
    async def fetch_balance(self) -> Dict:
        """Return account balance dict: {currency: {free, used, total}}"""

    @abstractmethod
    async def create_order(
        self, symbol: str, side: str, amount: float,
        price: Optional[float] = None, order_type: str = "market",
        leverage: int = 1,
    ) -> Dict:
        """Place an order. Returns order dict with at least:
              id, symbol, side, status, price, amount
        Status must be one of CCXT's statuses: 'open' | 'closed' | 'canceled',
        where 'closed' means fully filled. PaperExchange uses the same.
        For side='close', returns include 'pnl' (realized) and 'returned'.
        """

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Cancel an open order."""

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Return list of open orders."""

    async def check_liquidations(self, current_prices: Dict[str, float]) -> List[str]:
        """Return symbols whose positions have been (or should be considered)
        liquidated. Default no-op for clients that don't track positions
        locally; concrete clients override.
        """
        return []

    @abstractmethod
    async def close(self) -> None:
        """Clean up connections."""
