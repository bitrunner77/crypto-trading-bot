"""
exchange/base.py — Abstract base class that all exchange clients must implement.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class ExchangeClient(ABC):
    """Unified interface for exchange operations (live or paper)."""

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
        price: Optional[float] = None, leverage: int = 1, order_type: str = "market",
        stop_loss: Optional[float] = None, take_profit: Optional[float] = None,
    ) -> Dict:
        """Place an order. Returns order dict with id, status, filled, etc."""

    @abstractmethod
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        """Cancel an open order."""

    @abstractmethod
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        """Return list of open orders."""

    @abstractmethod
    async def close(self) -> None:
        """Clean up connections."""
