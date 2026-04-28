"""
strategies/base.py — Abstract base class for all trading strategies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class Signal:
    """A trading signal produced by a strategy."""
    action: str               # "buy" | "sell" | "hold" | "long" | "short" | "close"
    symbol: str
    size_pct: float           # Fraction of available cash (0.0–1.0)
    stop_loss_pct: float
    take_profit_pct: float
    confidence: float         # 0.0–1.0
    reasoning: str
    strategy_name: str
    leverage: int = 1         # Leverage multiplier (1 = no leverage)


class BaseStrategy(ABC):
    """All strategies implement this interface."""

    def __init__(self, config):
        self._config = config

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy identifier."""

    @abstractmethod
    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        indicators: dict,
        portfolio_value: float,
        cash_balance: float,
        open_positions: list,
    ) -> Signal:
        """
        Analyse the market and return a trading signal.
        Must be synchronous (called in a sync context).
        """

    def _hold(self, symbol: str, reason: str = "") -> Signal:
        return Signal(
            action="hold",
            symbol=symbol,
            size_pct=0.0,
            stop_loss_pct=self._config.risk_stop_loss_pct,
            take_profit_pct=self._config.risk_take_profit_pct,
            confidence=0.0,
            reasoning=reason or "No clear signal",
            strategy_name=self.name,
        )
