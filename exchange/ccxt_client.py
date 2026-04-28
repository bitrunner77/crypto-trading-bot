"""
exchange/ccxt_client.py — Live exchange client wrapping CCXT async.
Supports Binance, Coinbase, Kraken, Bybit, and any other CCXT exchange.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import ccxt.async_support as ccxt

from exchange.base import ExchangeClient
from utils.helpers import retry_async

logger = logging.getLogger("cryptobot.exchange")


class CCXTClient(ExchangeClient):
    """Production exchange client via CCXT."""

    def __init__(self, config, exchange_id: str = ""):
        eid = exchange_id or config.exchange
        exchange_cls = getattr(ccxt, eid, None)
        if exchange_cls is None:
            raise ValueError(f"Unknown exchange '{eid}'. Check CCXT docs for supported exchanges.")

        creds = config.credentials_for(eid)
        opts: dict = {**creds, "enableRateLimit": True, "verify": False}

        if eid == "bitget":
            opts["options"] = {"defaultType": "swap"}
        elif eid == "toobit":
            opts["options"] = {"defaultType": "swap"}
        elif eid == "bybit":
            opts["options"] = {"defaultType": "linear"}
        elif eid != "hyperliquid":
            opts["options"] = {"defaultType": "swap"}

        self._exchange: ccxt.Exchange = exchange_cls(opts)
        self._exchange_id = eid
        self._config = config
        logger.info(f"Initialized CCXT client for [bold]{eid}[/bold]")

    @retry_async(attempts=3)
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str,
        since: Optional[int] = None, limit: int = 200,
    ) -> List[List]:
        return await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    @retry_async(attempts=3)
    async def fetch_ticker(self, symbol: str) -> Dict:
        return await self._exchange.fetch_ticker(symbol)

    @retry_async(attempts=3)
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Dict]:
        return await self._exchange.fetch_tickers(symbols)

    @retry_async(attempts=3)
    async def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict:
        return await self._exchange.fetch_order_book(symbol, limit)

    @retry_async(attempts=3)
    async def fetch_balance(self) -> Dict:
        return await self._exchange.fetch_balance()

    @retry_async(attempts=3)
    async def create_order(
        self, symbol: str, side: str, amount: float,
        price: Optional[float] = None, order_type: str = "market",
    ) -> Dict:
        logger.info(f"[LIVE] Placing {order_type} {side} order: {amount} {symbol} @ {price or 'market'}")
        if order_type == "market":
            return await self._exchange.create_market_order(symbol, side, amount)
        return await self._exchange.create_limit_order(symbol, side, amount, price)

    @retry_async(attempts=3)
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        return await self._exchange.cancel_order(order_id, symbol)

    @retry_async(attempts=3)
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return await self._exchange.fetch_open_orders(symbol)

    async def close(self) -> None:
        await self._exchange.close()
        logger.info("Exchange connection closed.")
