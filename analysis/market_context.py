"""
analysis/market_context.py — Assembles a rich MarketSnapshot for the AI analyst.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

import pandas as pd

from analysis.indicators import compute_indicators

logger = logging.getLogger("cryptobot.context")


@dataclass
class MarketSnapshot:
    """Full market context passed to the AI analyst."""
    symbol: str
    timeframe: str
    current_price: float
    indicators: Dict
    recent_candles: List[Dict]       # Last 10 OHLCV candles as dicts
    portfolio_value: float
    cash_balance: float
    open_positions: List[Dict]
    recent_trades: List[Dict]
    trade_stats: Dict
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


class MarketContextBuilder:
    """Builds a MarketSnapshot from raw data."""

    def __init__(self, fetcher, portfolio_tracker, db_module):
        self._fetcher = fetcher
        self._portfolio = portfolio_tracker
        self._db = db_module

    async def build(self, symbol: str, timeframe: str) -> MarketSnapshot:
        from datetime import datetime

        # Fetch OHLCV
        df: pd.DataFrame = await self._fetcher.fetch_ohlcv(symbol, timeframe, limit=200)

        # Compute indicators
        indicators = compute_indicators(df)

        # Current price
        current_price = indicators.get("price") or 0.0

        # Recent candles (last 10, as simple dicts)
        recent_candles = []
        if not df.empty:
            tail = df.tail(10).copy()
            for _, row in tail.iterrows():
                recent_candles.append({
                    "time": str(row["timestamp"]),
                    "open": round(row["open"], 4),
                    "high": round(row["high"], 4),
                    "low": round(row["low"], 4),
                    "close": round(row["close"], 4),
                    "volume": round(row["volume"], 2),
                })

        # Portfolio state
        snapshot = self._portfolio.get_snapshot()

        # Recent trades (last 5 for this symbol)
        recent_trades = self._db.get_recent_trades(symbol=symbol, limit=5)

        # Trade stats
        trade_stats = self._db.get_trade_stats(symbol=symbol)

        return MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=current_price,
            indicators=indicators,
            recent_candles=recent_candles,
            portfolio_value=snapshot["total_value"],
            cash_balance=snapshot["cash_balance"],
            open_positions=snapshot["positions"],
            recent_trades=recent_trades,
            trade_stats=trade_stats,
            timestamp=datetime.utcnow().isoformat(),
        )
