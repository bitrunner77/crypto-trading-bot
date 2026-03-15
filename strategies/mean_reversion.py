"""
strategies/mean_reversion.py — Bollinger Band mean reversion strategy.
Buys at lower band, sells at upper band or middle.
"""
from __future__ import annotations

import logging

import pandas as pd

from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.mean_reversion")


class MeanReversionStrategy(BaseStrategy):
    """Bollinger Band mean reversion with RSI confirmation."""

    @property
    def name(self) -> str:
        return "mean_reversion"

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
        if df is None or len(df) < 25:
            return self._hold(symbol, "Insufficient data")

        price = indicators.get("price")
        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        bb_mid = indicators.get("bb_mid")
        rsi = indicators.get("rsi_14")
        atr = indicators.get("atr_14")

        if any(v is None for v in [price, bb_upper, bb_lower, bb_mid]):
            return self._hold(symbol, "Missing Bollinger Band data")

        band_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
        has_position = any(p["symbol"] == symbol for p in open_positions)

        # ── SELL (at upper band or mean reversion complete) ────────────────────
        if has_position:
            if price >= bb_upper * 0.99:
                confidence = 0.75
                if rsi and rsi > 70:
                    confidence = 0.85
                return Signal(
                    action="sell",
                    symbol=symbol,
                    size_pct=1.0,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=confidence,
                    reasoning=f"Price at upper BB (${price:.2f} ≥ ${bb_upper:.2f}), RSI={rsi:.0f if rsi else 'N/A'}",
                    strategy_name=self.name,
                )
            # Take partial profit at midband
            if price >= bb_mid * 0.995:
                return Signal(
                    action="sell",
                    symbol=symbol,
                    size_pct=0.5,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=0.60,
                    reasoning=f"Price reached midband (${price:.2f} ≥ ${bb_mid:.2f}), partial exit",
                    strategy_name=self.name,
                )

        # ── BUY (at lower band with RSI oversold) ─────────────────────────────
        if not has_position and band_width > 0.01:  # Only trade when bands are meaningful
            if price <= bb_lower * 1.01:
                confidence = 0.65
                reasons = [f"Price at lower BB (${price:.2f} ≤ ${bb_lower:.2f})"]
                if rsi and rsi < 35:
                    confidence += 0.10
                    reasons.append(f"RSI oversold ({rsi:.0f})")
                if rsi and rsi < 25:
                    confidence += 0.10
                    reasons.append("Extreme oversold")
                # Only trade if band is wide enough (volatility present)
                if band_width > 0.03:
                    confidence += 0.05
                    reasons.append(f"Band width {band_width:.1%}")

                if confidence >= 0.65:
                    # Use ATR for stop-loss if available
                    sl_pct = (atr / price * 1.5) if atr and price > 0 else self._config.risk_stop_loss_pct
                    sl_pct = max(sl_pct, self._config.risk_stop_loss_pct)
                    return Signal(
                        action="buy",
                        symbol=symbol,
                        size_pct=self._config.risk_max_position_pct * confidence,
                        stop_loss_pct=sl_pct,
                        take_profit_pct=(bb_upper - price) / price if price > 0 else self._config.risk_take_profit_pct,
                        confidence=confidence,
                        reasoning=f"Mean reversion buy: {', '.join(reasons)}",
                        strategy_name=self.name,
                    )

        return self._hold(symbol, f"No reversion setup (price=${price:.2f}, BB=[{bb_lower:.2f}-{bb_upper:.2f}])")
