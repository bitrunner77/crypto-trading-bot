"""
strategies/regime_filtered.py — Regime-aware wrapper for any BaseStrategy.

Bull  (EMA50 > EMA200, price > EMA200): only longs.
  strategy "buy"  -> "long"   open long
  strategy "sell" -> "close"  exit long

Bear  (EMA50 < EMA200, price < EMA200): only shorts.
  strategy "sell" -> "short"  open short
  strategy "buy"  -> "close"  cover short

Neutral: close any open position, no new entries.
Regime flip: force-close the wrong-direction position.
"""
from __future__ import annotations

import logging

import pandas as pd

from backtest.engine import detect_regime
from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.regime_filtered")


class RegimeFilteredStrategy(BaseStrategy):
    """Wraps an inner strategy with regime detection and perpetual-style signal translation."""

    def __init__(self, config, inner: BaseStrategy, leverage: int = 2):
        super().__init__(config)
        self._inner    = inner
        self._leverage = leverage

    @property
    def name(self) -> str:
        return f"regime_{self._inner.name}"

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
        regime = detect_regime(indicators)

        # Position state from DB positions passed in
        has_long  = any(p.get("symbol") == symbol and p.get("side") == "long"  for p in open_positions)
        has_short = any(p.get("symbol") == symbol and p.get("side") == "short" for p in open_positions)

        # Force-close if regime flipped against current position
        if regime == "bull" and has_short:
            return self._make(symbol, "close", indicators, reasoning=f"[BULL] Regime flip — covering short")
        if regime == "bear" and has_long:
            return self._make(symbol, "close", indicators, reasoning=f"[BEAR] Regime flip — exiting long")
        if regime == "neutral" and (has_long or has_short):
            return self._make(symbol, "close", indicators, reasoning="[NEUTRAL] Regime unclear — closing position")

        # Get the inner strategy's view
        inner_sig = self._inner.generate_signal(
            symbol=symbol, df=df, indicators=indicators,
            portfolio_value=portfolio_value, cash_balance=cash_balance,
            open_positions=open_positions, **kwargs,
        )
        raw = inner_sig.action

        # Translate to perpetual actions based on regime
        if regime == "bull":
            if raw == "buy" and not has_long:
                action = "long"
            elif raw == "sell" and has_long:
                action = "close"
            else:
                action = "hold"
        elif regime == "bear":
            if raw == "sell" and not has_short:
                action = "short"
            elif raw == "buy" and has_short:
                action = "close"
            else:
                action = "hold"
        else:  # neutral — no new entries
            action = "hold"

        reasoning = f"[{regime.upper()}] {inner_sig.reasoning}"
        logger.debug(f"{symbol} regime={regime} inner={raw} -> {action}")

        return Signal(
            action=action,
            symbol=symbol,
            size_pct=inner_sig.size_pct,
            stop_loss_pct=inner_sig.stop_loss_pct,
            take_profit_pct=inner_sig.take_profit_pct,
            confidence=inner_sig.confidence,
            reasoning=reasoning,
            strategy_name=self.name,
            leverage=self._leverage,
        )

    def _make(self, symbol: str, action: str, indicators: dict, reasoning: str) -> Signal:
        return Signal(
            action=action,
            symbol=symbol,
            size_pct=1.0,
            stop_loss_pct=self._config.risk_stop_loss_pct,
            take_profit_pct=self._config.risk_take_profit_pct,
            confidence=0.9,
            reasoning=reasoning,
            strategy_name=self.name,
            leverage=self._leverage,
        )
