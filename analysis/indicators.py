"""
analysis/indicators.py — Technical indicator computation via pandas-ta.
Returns a flat dict of indicator values for the latest candle.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import pandas as pd

logger = logging.getLogger("cryptobot.indicators")

# Try to import pandas_ta; if missing, provide a fallback stub
try:
    import pandas_ta as ta
    HAS_PANDAS_TA = True
except ImportError:
    HAS_PANDAS_TA = False
    logger.warning("pandas-ta not installed. Indicators will use manual calculations.")


def compute_indicators(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """
    Compute a comprehensive suite of technical indicators on the given OHLCV DataFrame.
    Returns a flat dict of the most recent values.
    """
    if df is None or len(df) < 20:
        return _empty_indicators()

    df = df.copy()

    if HAS_PANDAS_TA:
        return _compute_with_pandas_ta(df)
    return _compute_manual(df)


def _compute_with_pandas_ta(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    result: Dict[str, Optional[float]] = {}

    # RSI (14)
    try:
        rsi = ta.rsi(close, length=14)
        result["rsi_14"] = _last(rsi)
    except Exception:
        result["rsi_14"] = None

    # MACD (12, 26, 9)
    try:
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            result["macd"] = _last(macd_df.iloc[:, 0])
            result["macd_signal"] = _last(macd_df.iloc[:, 2])
            result["macd_hist"] = _last(macd_df.iloc[:, 1])
        else:
            result.update({"macd": None, "macd_signal": None, "macd_hist": None})
    except Exception:
        result.update({"macd": None, "macd_signal": None, "macd_hist": None})

    # Bollinger Bands (20, 2)
    try:
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None and not bb.empty:
            result["bb_upper"] = _last(bb.iloc[:, 0])
            result["bb_mid"] = _last(bb.iloc[:, 1])
            result["bb_lower"] = _last(bb.iloc[:, 2])
            result["bb_pct"] = _last(bb.iloc[:, 3]) if bb.shape[1] > 3 else None
        else:
            result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None})
    except Exception:
        result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None})

    # EMAs
    for period in [9, 21, 50, 200]:
        try:
            ema = ta.ema(close, length=period)
            result[f"ema_{period}"] = _last(ema)
        except Exception:
            result[f"ema_{period}"] = None

    # ATR (14)
    try:
        atr = ta.atr(high, low, close, length=14)
        result["atr_14"] = _last(atr)
    except Exception:
        result["atr_14"] = None

    # Stochastic (14, 3, 3)
    try:
        stoch = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
        if stoch is not None and not stoch.empty:
            result["stoch_k"] = _last(stoch.iloc[:, 0])
            result["stoch_d"] = _last(stoch.iloc[:, 1])
        else:
            result.update({"stoch_k": None, "stoch_d": None})
    except Exception:
        result.update({"stoch_k": None, "stoch_d": None})

    # OBV
    try:
        obv = ta.obv(close, volume)
        result["obv"] = _last(obv)
    except Exception:
        result["obv"] = None

    # Volume SMA (20)
    try:
        result["volume_sma_20"] = float(volume.rolling(20).mean().iloc[-1])
        result["volume_ratio"] = float(volume.iloc[-1] / result["volume_sma_20"]) if result["volume_sma_20"] else None
    except Exception:
        result.update({"volume_sma_20": None, "volume_ratio": None})

    # Price-derived stats
    result["price"] = float(close.iloc[-1])
    result["price_change_1"] = _pct_change(close, 1)
    result["price_change_5"] = _pct_change(close, 5)
    result["price_change_24"] = _pct_change(close, 24)

    # Trend direction
    ema9 = result.get("ema_9")
    ema21 = result.get("ema_21")
    ema50 = result.get("ema_50")
    price = result["price"]
    if all(v is not None for v in [ema9, ema21, ema50]):
        result["trend"] = "bullish" if ema9 > ema21 > ema50 else ("bearish" if ema9 < ema21 < ema50 else "neutral")
    else:
        result["trend"] = "unknown"

    return result


def _compute_manual(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    """Fallback: compute key indicators without pandas-ta."""
    close = df["close"]
    result: Dict[str, Optional[float]] = {}

    # Simple EMA
    for period in [9, 21, 50, 200]:
        try:
            result[f"ema_{period}"] = float(close.ewm(span=period, adjust=False).mean().iloc[-1])
        except Exception:
            result[f"ema_{period}"] = None

    # RSI manual
    try:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss
        result["rsi_14"] = float(100 - 100 / (1 + rs.iloc[-1]))
    except Exception:
        result["rsi_14"] = None

    # BB manual
    try:
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        result["bb_upper"] = float(sma.iloc[-1] + 2 * std.iloc[-1])
        result["bb_mid"] = float(sma.iloc[-1])
        result["bb_lower"] = float(sma.iloc[-1] - 2 * std.iloc[-1])
        result["bb_pct"] = None
    except Exception:
        result.update({"bb_upper": None, "bb_mid": None, "bb_lower": None, "bb_pct": None})

    result.update({"macd": None, "macd_signal": None, "macd_hist": None,
                   "atr_14": None, "stoch_k": None, "stoch_d": None, "obv": None,
                   "volume_sma_20": None, "volume_ratio": None})
    result["price"] = float(close.iloc[-1])
    result["price_change_1"] = _pct_change(close, 1)
    result["price_change_5"] = _pct_change(close, 5)
    result["price_change_24"] = _pct_change(close, 24)
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
