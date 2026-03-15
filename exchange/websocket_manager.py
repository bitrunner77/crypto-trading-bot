"""
exchange/websocket_manager.py — Real-time price feed manager using CCXT Pro WebSocket.
Falls back to REST polling if WebSocket unavailable.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("cryptobot.ws")


class PriceFeed:
    """
    Maintains a live price cache updated by WebSocket or REST polling.
    Consumers call get_price(symbol) to get the latest price.
    """

    def __init__(self, exchange_client, symbols: List[str], poll_interval: float = 5.0):
        self._exchange = exchange_client
        self._symbols = symbols
        self._poll_interval = poll_interval
        self._prices: Dict[str, float] = {}
        self._callbacks: List[Callable] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def on_price_update(self, callback: Callable) -> None:
        """Register a callback that fires on every price update."""
        self._callbacks.append(callback)

    def get_price(self, symbol: str) -> Optional[float]:
        return self._prices.get(symbol)

    def get_all_prices(self) -> Dict[str, float]:
        return dict(self._prices)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Price feed started for {self._symbols}")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Price feed stopped.")

    async def _poll_loop(self) -> None:
        """Poll REST API for ticker prices at regular intervals."""
        while self._running:
            try:
                tickers = await self._exchange.fetch_tickers(self._symbols)
                updated = False
                for symbol, ticker in tickers.items():
                    price = ticker.get("last") or ticker.get("close")
                    if price and price != self._prices.get(symbol):
                        self._prices[symbol] = price
                        updated = True
                if updated:
                    for cb in self._callbacks:
                        try:
                            await cb(self._prices)
                        except Exception as e:
                            logger.warning(f"Price callback error: {e}")
            except Exception as e:
                logger.warning(f"Price feed poll error: {e}")
            await asyncio.sleep(self._poll_interval)
