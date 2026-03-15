"""
strategies/grid.py — Grid trading strategy.
Places buy/sell orders at fixed intervals around the current price.
"""
from __future__ import annotations

import logging
from typing import Dict, List

import pandas as pd

from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.grid")


class GridStrategy(BaseStrategy):
    """
    Grid trading: creates a grid of buy/sell levels.
    Buys when price drops to a grid buy level, sells when it rises to a sell level.
    """

    @property
    def name(self) -> str:
        return "grid"

    def __init__(self, config):
        super().__init__(config)
        self._grid_levels: List[Dict] = []
        self._base_price: float = 0.0
        self._initialized = False

    def _init_grid(self, price: float) -> None:
        """Build grid levels around the current price."""
        spread = self._config.grid_spread_pct
        levels = self._config.grid_levels
        self._base_price = price
        self._grid_levels = []
        for i in range(-(levels // 2), (levels // 2) + 1):
            level_price = price * (1 + i * spread)
            self._grid_levels.append({
                "price": level_price,
                "type": "buy" if i < 0 else ("sell" if i > 0 else "base"),
                "index": i,
                "triggered": False,
            })
        self._initialized = True
        logger.info(f"Grid initialized around ${price:,.2f} with {len(self._grid_levels)} levels")

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
        if not price:
            return self._hold(symbol, "No price data")

        # Initialize grid on first run or if price moved > 2x grid width
        if not self._initialized or abs(price - self._base_price) / self._base_price > self._config.grid_levels * self._config.grid_spread_pct:
            self._init_grid(price)
            return self._hold(symbol, "Grid initialized, waiting for movement")

        has_position = any(p["symbol"] == symbol for p in open_positions)

        # Check sell levels
        if has_position:
            for level in self._grid_levels:
                if level["type"] == "sell" and not level["triggered"] and price >= level["price"]:
                    level["triggered"] = True
                    return Signal(
                        action="sell",
                        symbol=symbol,
                        size_pct=1.0 / max(1, self._config.grid_levels // 2),
                        stop_loss_pct=self._config.risk_stop_loss_pct,
                        take_profit_pct=self._config.grid_spread_pct * 2,
                        confidence=0.75,
                        reasoning=f"Grid sell triggered at ${level['price']:,.2f} (level {level['index']})",
                        strategy_name=self.name,
                    )

        # Check buy levels
        if cash_balance > 10:
            for level in self._grid_levels:
                if level["type"] == "buy" and not level["triggered"] and price <= level["price"]:
                    level["triggered"] = True
                    return Signal(
                        action="buy",
                        symbol=symbol,
                        size_pct=1.0 / max(1, self._config.grid_levels // 2),
                        stop_loss_pct=self._config.grid_spread_pct * 2,
                        take_profit_pct=self._config.grid_spread_pct,
                        confidence=0.70,
                        reasoning=f"Grid buy triggered at ${level['price']:,.2f} (level {level['index']})",
                        strategy_name=self.name,
                    )

        return self._hold(symbol, f"No grid level triggered (price=${price:,.2f})")
