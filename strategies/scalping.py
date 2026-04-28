"""
strategies/scalping.py — High-frequency scalping strategy.
Uses fast EMA crossovers (EMA3/EMA8) + RSI + MACD for short timeframes (5m, 15m).
Designed for maximum trade frequency with tight signals.
"""
from __future__ import annotations

import logging
import pandas as pd

from strategies.base import BaseStrategy, Signal

logger = logging.getLogger("cryptobot.strategy.scalping")


class ScalpingStrategy(BaseStrategy):
    """
    Fast EMA scalping:
      - BUY  when EMA3 crosses above EMA8 + RSI 40-65 + MACD > 0
      - SELL when EMA3 crosses below EMA8 OR RSI > 72 OR MACD flips negative
    Exits are aggressive to lock in small gains quickly.
    """

    @property
    def name(self) -> str:
        return "scalping"

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
        if df is None or len(df) < 20:
            return self._hold(symbol, "Insufficient data")

        price    = indicators.get("price")
        ema9     = indicators.get("ema_9")    # use as fast EMA proxy
        ema21    = indicators.get("ema_21")   # use as slow EMA proxy
        ema50    = indicators.get("ema_50")
        rsi      = indicators.get("rsi_14")
        macd_h   = indicators.get("macd_hist")
        vol_ratio = indicators.get("volume_ratio")
        bb_upper = indicators.get("bb_upper")
        bb_lower = indicators.get("bb_lower")
        atr      = indicators.get("atr_14")

        if any(v is None for v in [price, ema9, ema21, rsi]):
            return self._hold(symbol, "Missing indicators")

        # Compute previous-bar crossover using last 2 rows
        if len(df) >= 3:
            import ta as _ta
            close = df["close"]
            fast = _ta.trend.EMAIndicator(close=close, window=3).ema_indicator()
            slow = _ta.trend.EMAIndicator(close=close, window=8).ema_indicator()
            ema3_now  = float(fast.iloc[-1])
            ema3_prev = float(fast.iloc[-2])
            ema8_now  = float(slow.iloc[-1])
            ema8_prev = float(slow.iloc[-2])
            crossed_up   = ema3_prev <= ema8_prev and ema3_now > ema8_now
            crossed_down = ema3_prev >= ema8_prev and ema3_now < ema8_now
        else:
            ema3_now = ema9
            ema8_now = ema21
            crossed_up   = ema3_now > ema8_now
            crossed_down = ema3_now < ema8_now

        has_long  = any(p["symbol"] == symbol and p.get("side") in ("buy", "long")  for p in open_positions)
        has_short = any(p["symbol"] == symbol and p.get("side") in ("sell", "short") for p in open_positions)

        # ── EXIT LONG ──────────────────────────────────────────────────────────
        if has_long:
            exit_reasons = []
            if crossed_down:
                exit_reasons.append("EMA3 crossed below EMA8")
            if rsi and rsi > 72:
                exit_reasons.append(f"RSI overbought ({rsi:.0f})")
            if macd_h and macd_h < 0:
                exit_reasons.append("MACD turned negative")
            if bb_upper and price and price >= bb_upper * 0.998:
                exit_reasons.append("Price at BB upper")
            if exit_reasons:
                return Signal(
                    action="sell", symbol=symbol, size_pct=1.0,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=0.8,
                    reasoning=f"EXIT LONG: {', '.join(exit_reasons)}",
                    strategy_name=self.name,
                )

        # ── EXIT SHORT ─────────────────────────────────────────────────────────
        if has_short:
            exit_reasons = []
            if crossed_up:
                exit_reasons.append("EMA3 crossed above EMA8")
            if rsi and rsi < 28:
                exit_reasons.append(f"RSI oversold ({rsi:.0f})")
            if macd_h and macd_h > 0:
                exit_reasons.append("MACD turned positive")
            if bb_lower and price and price <= bb_lower * 1.002:
                exit_reasons.append("Price at BB lower")
            if exit_reasons:
                return Signal(
                    action="buy", symbol=symbol, size_pct=1.0,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=0.8,
                    reasoning=f"EXIT SHORT: {', '.join(exit_reasons)}",
                    strategy_name=self.name,
                )

        # ── ENTER LONG ─────────────────────────────────────────────────────────
        if not has_long and not has_short:
            buy_score = 0
            buy_reasons = []

            if crossed_up:
                buy_score += 3
                buy_reasons.append("EMA3 x EMA8 bullish cross")
            elif ema3_now > ema8_now:
                buy_score += 1
                buy_reasons.append("EMA3 > EMA8")

            if rsi and 38 <= rsi <= 65:
                buy_score += 2
                buy_reasons.append(f"RSI sweet spot ({rsi:.0f})")
            elif rsi and rsi < 38:
                buy_score -= 1  # too weak

            if macd_h and macd_h > 0:
                buy_score += 2
                buy_reasons.append("MACD positive")

            if vol_ratio and vol_ratio > 1.3:
                buy_score += 1
                buy_reasons.append(f"Volume {vol_ratio:.1f}x")

            if ema50 and price and price > ema50:
                buy_score += 1
                buy_reasons.append("Above EMA50")

            if buy_score >= 5:
                conf = min(0.9, 0.5 + buy_score * 0.06)
                size = self._config.risk_max_position_pct * conf
                return Signal(
                    action="buy", symbol=symbol, size_pct=size,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=conf,
                    reasoning=f"SCALP LONG: {', '.join(buy_reasons)} (score={buy_score})",
                    strategy_name=self.name,
                )

            # ── ENTER SHORT ───────────────────────────────────────────────────
            sell_score = 0
            sell_reasons = []

            if crossed_down:
                sell_score += 3
                sell_reasons.append("EMA3 x EMA8 bearish cross")
            elif ema3_now < ema8_now:
                sell_score += 1
                sell_reasons.append("EMA3 < EMA8")

            if rsi and 35 <= rsi <= 62:
                sell_score += 2
                sell_reasons.append(f"RSI in range ({rsi:.0f})")

            if macd_h and macd_h < 0:
                sell_score += 2
                sell_reasons.append("MACD negative")

            if vol_ratio and vol_ratio > 1.3:
                sell_score += 1
                sell_reasons.append(f"Volume {vol_ratio:.1f}x")

            if ema50 and price and price < ema50:
                sell_score += 1
                sell_reasons.append("Below EMA50")

            if sell_score >= 5:
                conf = min(0.9, 0.5 + sell_score * 0.06)
                size = self._config.risk_max_position_pct * conf
                return Signal(
                    action="sell", symbol=symbol, size_pct=size,
                    stop_loss_pct=self._config.risk_stop_loss_pct,
                    take_profit_pct=self._config.risk_take_profit_pct,
                    confidence=conf,
                    reasoning=f"SCALP SHORT: {', '.join(sell_reasons)} (score={sell_score})",
                    strategy_name=self.name,
                )

        return self._hold(symbol, f"No scalp signal (EMA3={ema3_now:.2f}, EMA8={ema8_now:.2f}, RSI={rsi:.0f})")
