"""
data/fetcher.py — OHLCV and ticker fetching with SQLite caching.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from data.database import cache_ohlcv, get_cached_ohlcv

logger = logging.getLogger("cryptobot.fetcher")


class DataFetcher:
    """Fetches OHLCV data via a CCXT exchange client and caches results locally."""

    def __init__(self, exchange_client, config):
        self.exchange = exchange_client
        self.config = config
        self._exchange_id = config.exchange

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 200,
        since_ms: Optional[int] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """Return a DataFrame with columns: timestamp, open, high, low, close, volume."""
        if use_cache:
            cached = get_cached_ohlcv(self._exchange_id, symbol, timeframe, since_ms, limit)
            if len(cached) >= limit:
                return self._to_df(cached)

        try:
            raw = await self.exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
            if raw:
                cache_ohlcv(self._exchange_id, symbol, timeframe, raw)
            return self._to_df(raw)
        except Exception as exc:
            logger.warning(f"OHLCV fetch failed for {symbol}: {exc}. Trying cache fallback.")
            cached = get_cached_ohlcv(self._exchange_id, symbol, timeframe, since_ms, limit)
            if cached:
                return self._to_df(cached)
            raise

    async def fetch_ticker(self, symbol: str) -> Dict:
        """Return current ticker data for a symbol."""
        return await self.exchange.fetch_ticker(symbol)

    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Dict]:
        """Return tickers for multiple symbols."""
        try:
            return await self.exchange.fetch_tickers(symbols)
        except Exception:
            result = {}
            for s in symbols:
                try:
                    result[s] = await self.fetch_ticker(s)
                except Exception as e:
                    logger.warning(f"Ticker fetch failed for {s}: {e}")
            return result

    async def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict:
        return await self.exchange.fetch_order_book(symbol, limit)

    @staticmethod
    def _to_df(candles: List) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df
