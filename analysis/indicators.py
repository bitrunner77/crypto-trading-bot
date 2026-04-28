"""
analysis/indicators.py — Technical indicator computation via the `ta` library.
Returns a flat dict of indicator values for the latest candle.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger("cryptobot.indicators")

try:
    import ta as _ta
    HAS_TA = True
except ImportError:
    HAS_TA = False
    logger.warning("ta library not installed. Indicators will use manual calculations.")


def compute_indicators(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """
    Compute a comprehensive suite of technical indicators on the given OHLCV DataFrame.
    Returns a flat dict of the most recent values.
    """
    if df is None or len(df) < 20:
        return _empty_indicators()

    df = df.copy()

    if HAS_TA:
        return _compute_with_ta(df)
    return _compute_manual(df)


def _compute_with_ta(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    result: Dict[str, Optional[float]] = {}

    # RSI (14)
    try:
        result["rsi_14"] = float(_ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1])
    except Exception:
        result["rsi_14"] = None

    # MACD (12, 26, 9)
    try:
        macd = _ta.trend.MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        result["macd"] = float(macd.macd().iloc[-1])
        result["macd_signal"] = float(macd.macd_signal().iloc[-1])
        result["macd_hist"] = float(macd.macd_diff().iloc[-1])
    except Exception:
        result.update({"macd": None, "macd_signal": None, "macd_hist": None})

    # Bollinger Bands (20, 2)
    try:
        bb = _ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
        result["bb_upper"] = float(bb.bollinger_hband().iloc[-1])
        result["bb_mid"] = float(bb.bollinger_mavg().iloc[-1])
        result["bb_lower"] = float(bb.bollinger_lband().iloc[-1])
        result["bb_pct"] = float(bb.bollinger_pband().iloc[-1])
    except Exception:
        result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None})

    # EMAs
    for period in [9, 21, 50, 200]:
        try:
            result[f"ema_{period}"] = float(
                _ta.trend.EMAIndicator(close=close, window=period).ema_indicator().iloc[-1]
            )
        except Exception:
            result[f"ema_{period}"] = None

    # ATR (14)
    try:
        result["atr_14"] = float(
            _ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1]
        )
    except Exception:
        result["atr_14"] = None

    # Stochastic (14, 3)
    try:
        stoch = _ta.momentum.StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
        result["stoch_k"] = float(stoch.stoch().iloc[-1])
        result["stoch_d"] = float(stoch.stoch_signal().iloc[-1])
    except Exception:
        result.update({"stoch_k": None, "stoch_d": None})

    # OBV
    try:
        result["obv"] = float(_ta.volume.OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume().iloc[-1])
    except Exception:
        result["obv"] = None

    # Volume SMA (20)
    try:
        vol_sma = float(volume.rolling(20).mean().iloc[-1])
        result["volume_sma_20"] = vol_sma
        result["volume_ratio"] = float(volume.iloc[-1] / vol_sma) if vol_sma else None
    except Exception:
        result.update({"volume_sma_20": None, "volume_ratio": None})

    # Price-derived stats
    result["price"] = float(close.iloc[-1])
    result["price_change_1"] = _pct_change(close, 1)
    result["price_change_5"] = _pct_change(close, 5)
    result["price_change_24"] = _pct_change(close, 24)

    # Short-term trend
    ema9 = result.get("ema_9")
    ema21 = result.get("ema_21")
    ema50 = result.get("ema_50")
    if all(v is not None for v in [ema9, ema21, ema50]):
        result["trend"] = "bullish" if ema9 > ema21 > ema50 else ("bearish" if ema9 < ema21 < ema50 else "neutral")
    else:
        result["trend"] = "unknown"

    return result


def _compute_manual(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """Fallback: compute all key indicators without external libraries."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    result: Dict[str, Optional[float]] = {}

    # EMAs
    for period in [9, 21, 50, 200]:
        try:
            result[f"ema_{period}"] = float(close.ewm(span=period, adjust=False).mean().iloc[-1])
        except Exception:
            result[f"ema_{period}"] = None

    # RSI
    try:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        result["rsi_14"] = float(100 - 100 / (1 + rs.iloc[-1]))
    except Exception:
        result["rsi_14"] = None

    # MACD
    try:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        result["macd"] = float(macd_line.iloc[-1])
        result["macd_signal"] = float(signal_line.iloc[-1])
        result["macd_hist"] = float((macd_line - signal_line).iloc[-1])
    except Exception:
        result.update({"macd": None, "macd_signal": None, "macd_hist": None})

    # Bollinger Bands
    try:
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        result["bb_upper"] = float(upper.iloc[-1])
        result["bb_mid"] = float(sma.iloc[-1])
        result["bb_lower"] = float(lower.iloc[-1])
        band_width = upper.iloc[-1] - lower.iloc[-1]
        result["bb_pct"] = float((close.iloc[-1] - lower.iloc[-1]) / band_width) if band_width > 0 else None
    except Exception:
        result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None})

    # ATR
    try:
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ], axis=1).max(axis=1)
        result["atr_14"] = float(tr.rolling(14).mean().iloc[-1])
    except Exception:
        result["atr_14"] = None

    # Stochastic
    try:
        low14 = low.rolling(14).min()
        high14 = high.rolling(14).max()
        k = 100 * (close - low14) / (high14 - low14)
        result["stoch_k"] = float(k.iloc[-1])
        result["stoch_d"] = float(k.rolling(3).mean().iloc[-1])
    except Exception:
        result.update({"stoch_k": None, "stoch_d": None})

    # OBV
    try:
        direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        result["obv"] = float((direction * volume).cumsum().iloc[-1])
    except Exception:
        result["obv"] = None

    # Volume ratio
    try:
        vol_sma = float(volume.rolling(20).mean().iloc[-1])
        result["volume_sma_20"] = vol_sma
        result["volume_ratio"] = float(volume.iloc[-1] / vol_sma) if vol_sma else None
    except Exception:
        result.update({"volume_sma_20": None, "volume_ratio": None})

    result["price"] = float(close.iloc[-1])
    result["price_change_1"] = _pct_change(close, 1)
    result["price_change_5"] = _pct_change(close, 5)
    result["price_change_24"] = _pct_change(close, 24)

    ema9 = result.get("ema_9")
    ema21 = result.get("ema_21")
    ema50 = result.get("ema_50")
    if all(v is not None for v in [ema9, ema21, ema50]):
        result["trend"] = "bullish" if ema9 > ema21 > ema50 else ("bearish" if ema9 < ema21 < ema50 else "neutral")
    else:
        result["trend"] = "unknown"

    return result


def _empty_indicators() -> Dict[str, Optional[float]]:
    return {
        "rsi_14": None, "macd": None, "macd_signal": None, "macd_hist": None,
        "bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None,
        "ema_9": None, "ema_21": None, "ema_50": None, "ema_200": None,
        "atr_14": None, "stoch_k": None, "stoch_d": None, "obv": None,
        "volume_sma_20": None, "volume_ratio": None,
        "price": None, "price_change_1": None, "price_change_5": None, "price_change_24": None,
        "trend": "unknown",
    }


def _last(series) -> Optional[float]:
    try:
        val = series.dropna().iloc[-1]
        return float(val)
    except (IndexError, TypeError):
        return None


def _pct_change(series: pd.Series, periods: int) -> Optional[float]:
    try:
        if len(series) <= periods:
            return None
        old = series.iloc[-(periods + 1)]
        new = series.iloc[-1]
        return float((new - old) / old) if old != 0 else None
    except Exception:
        return None
