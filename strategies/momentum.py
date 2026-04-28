"""
strategies/momentum.py — EMA crossover momentum strategy.
Buys when EMA9 crosses above EMA21, sells when it crosses below.
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.momentum")


class MomentumStrategy(BaseStrategy):
    """EMA crossover with RSI and volume filters."""

    @property
    def name(self) -> str:
        return "momentum"

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
        if df is None or len(df) < 30:
            return self._hold(symbol, "Insufficient data")

        ema9 = indicators.get("ema_9")
        ema21 = indicators.get("ema_21")
        ema50 = indicators.get("ema_50")
        rsi = indicators.get("rsi_14")
        price = indicators.get("price")
        vol_ratio = indicators.get("volume_ratio")
        macd_hist = indicators.get("macd_hist")

        if any(v is None for v in [ema9, ema21, price]):
            return self._hold(symbol, "Missing indicators")

        has_position = any(p["symbol"] == symbol for p in open_positions)

        # Compare last COMPLETED candle against prior 5 completed candles.
        # Never use iloc[-1] — that's the current building candle (always looks low).
        if len(df) >= 7:
            vol_series      = df["volume"].astype(float)
            completed_vol   = float(vol_series.iloc[-2])          # last closed candle
            local_avg       = float(vol_series.iloc[-7:-2].mean()) # 5 candles before that
            vol_ratio_local = completed_vol / local_avg if local_avg > 0 else vol_ratio
        else:
            vol_ratio_local = vol_ratio

        # ── SELL SIGNAL ────────────────────────────────────────────────────────
        if has_position:
            if vol_ratio_local is not None and vol_ratio_local < 0.4:
                return self._hold(symbol, f"Volume too low to exit ({vol_ratio_local:.2f}x recent)")
            sell_signals = 0
            reasons = []
            if ema9 < ema21:
                sell_signals += 2
                reasons.append("EMA9 < EMA21 (bearish cross)")
            if rsi and rsi > 75:
                sell_signals += 1
                reasons.append(f"RSI overbought ({rsi:.0f})")
            if macd_hist and macd_hist < 0:
                sell_signals += 1
                reasons.append("MACD histogram negative")
            if sell_signals >= 2:
                confidence = min(0.9, 0.5 + sell_signals * 0.1)
                return Signal(
                    action="sell",
                    symbol=symbol,
                    size_pct=1.0,  # Sell full position
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=confidence,
                    reasoning=f"Bearish momentum: {', '.join(reasons)}",
                    strategy_name=self.name,
                )

        # ── BUY SIGNAL ─────────────────────────────────────────────────────────
        if not has_position:
            # Hard minimum: skip dead/illiquid candles (vs recent 5 candles, not 20)
            if vol_ratio_local is not None and vol_ratio_local < 0.2:
                return self._hold(symbol, f"Volume too low ({vol_ratio_local:.2f}x recent — need ≥0.2x)")

            buy_signals = 0
            reasons = []

            if ema9 > ema21:
                buy_signals += 2
                reasons.append("EMA9 > EMA21 (bullish cross)")
            if ema50 and price > ema50:
                buy_signals += 1
                reasons.append(f"Price above EMA50")
            if rsi and 40 <= rsi <= 65:
                buy_signals += 1
                reasons.append(f"RSI in sweet spot ({rsi:.0f})")
            if vol_ratio_local and vol_ratio_local > 1.5:
                buy_signals += 1
                reasons.append(f"Volume spike ({vol_ratio_local:.1f}x recent)")
            if macd_hist and macd_hist > 0:
                buy_signals += 1
                reasons.append("MACD histogram positive")

            if buy_signals >= 3:
                confidence = min(0.85, 0.4 + buy_signals * 0.1)
                size = self._config.risk_max_position_pct * (confidence / 0.85)
                return Signal(
                    action="buy",
                    symbol=symbol,
                    size_pct=size,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=confidence,
                    reasoning=f"Bullish momentum: {', '.join(reasons)}",
                    strategy_name=self.name,
                )

        return self._hold(symbol, f"No clear momentum signal (EMA9={ema9:.2f}, EMA21={ema21:.2f})")
