"""
strategies/ai_driven.py — Pure Claude Opus decision-making strategy.
Supports long/short/close/hold on perpetuals with leverage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from ai.analyst import MarketAnalyst
from analysis.market_context import MarketSnapshot
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.ai")


@dataclass
class LeveragedSignal(Signal):
    """Extends Signal with leverage and directional fields for perps."""
    leverage: int = 1
    changes_from_last_round: str = ""


class AIDrivenStrategy(BaseStrategy):
    """
    Delegates all trading decisions to Claude Opus.
    Supports long/short perpetuals with up to 20x leverage.
    """

    def __init__(self, config):
        super().__init__(config)
        self._analyst = MarketAnalyst(config)
        self._recent_pnl: List[dict] = []

    @property
    def name(self) -> str:
        return "ai_driven"

    def generate_signal(
        self,
        symbol: str,
        df: pd.DataFrame,
        indicators: dict,
        portfolio_value: float,
        cash_balance: float,
        open_positions: list,
        recent_trades: list = None,
        trade_stats: dict = None,
        timeframe: str = "1h",
        **kwargs,
    ) -> LeveragedSignal:
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            current_price=indicators.get("price", 0.0),
            indicators=indicators,
            recent_candles=self._tail_candles(df),
            portfolio_value=portfolio_value,
            cash_balance=cash_balance,
            open_positions=open_positions,
            recent_trades=recent_trades or [],
            trade_stats=trade_stats or {},
        )

        decision, _risk = self._analyst.analyze(snapshot, self._recent_pnl[-5:])

        return LeveragedSignal(
            action=decision.action,
            symbol=symbol,
            size_pct=decision.size_pct,
            stop_loss_pct=decision.stop_loss_pct,
            take_profit_pct=decision.take_profit_pct,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            strategy_name=self.name,
            leverage=decision.leverage,
            changes_from_last_round=decision.changes_from_last_round,
        )

    @staticmethod
    def _tail_candles(df: pd.DataFrame, n: int = 10) -> list:
        if df is None or df.empty:
            return []
        return [
            {
                "time": str(row["timestamp"]),
                "open": round(row["open"], 4),
                "high": round(row["high"], 4),
                "low": round(row["low"], 4),
                "close": round(row["close"], 4),
                "volume": round(row["volume"], 2),
            }
            for _, row in df.tail(n).iterrows()
        ]
