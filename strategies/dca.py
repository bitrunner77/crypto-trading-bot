"""
strategies/dca.py — Dollar Cost Averaging strategy.
Buys a fixed USD amount at regular intervals regardless of price.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.dca")


class DCAStrategy(BaseStrategy):
    """
    Dollar Cost Averaging: buys a fixed USDT amount every N hours.
    Ignores market conditions — focuses on consistent accumulation.
    """

    @property
    def name(self) -> str:
        return "dca"

    def __init__(self, config):
        super().__init__(config)
        self._last_buy: Optional[datetime] = None

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        indicators: dict,
        portfolio_value: float,
        cash_balance: float,
        open_positions: list,
        **kwargs,
    ) -> Signal:
        price = indicators.get("price")
        if not price or price <= 0:
            return self._hold(symbol, "No price data")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        interval = timedelta(hours=self._config.dca_interval_hours)
        dca_amount = self._config.dca_amount_usdt

        # Check if interval has passed
        if self._last_buy is not None:
            time_since = now - self._last_buy
            if time_since < interval:
                remaining = interval - time_since
                return self._hold(
                    symbol,
                    f"DCA: next buy in {remaining.seconds // 3600}h {(remaining.seconds % 3600) // 60}m"
                )

        # Check we have enough cash
        if cash_balance < dca_amount:
            return self._hold(symbol, f"Insufficient cash (${cash_balance:.2f} < ${dca_amount:.2f})")

        # Calculate size as fraction of available cash
        size_pct = min(dca_amount / cash_balance, 0.5)  # Never more than 50% in one DCA

        self._last_buy = now

        rsi = indicators.get("rsi_14")
        extra = ""
        # Small bonus buy if RSI is very oversold
        if rsi and rsi < 30:
            size_pct = min(size_pct * 1.5, 0.5)
            extra = f" (bonus: RSI={rsi:.0f})"

        return Signal(
            action="buy",
            symbol=symbol,
            size_pct=size_pct,
            stop_loss_pct=self._config.risk_stop_loss_pct * 3,  # Wider SL for DCA
            take_profit_pct=0.20,   # 20% take profit for long-term accumulation
            confidence=0.70,
            reasoning=f"DCA buy: ${dca_amount:.0f} USDT every {self._config.dca_interval_hours}h{extra}",
            strategy_name=self.name,
        )
