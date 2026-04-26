"""
tests/test_backtest_perp.py — backtest engine in perp (long/short/close) mode
with explicit leverage and liquidation simulation.
"""
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd
import pytest


@dataclass
class _FakeSignal:
    action: str
    symbol: str = "BTC/USDT:USDT"
    size_pct: float = 0.0
    leverage: int = 1
    confidence: float = 0.8
    reasoning: str = "test"
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06
    strategy_name: str = "fake"


class _ScriptedStrategy:
    """Replays a fixed script of (bar_offset → signal) so tests can assert
    on exact behaviour without strategy heuristics."""

    name = "scripted"

    def __init__(self, script: dict[int, _FakeSignal]):
        self._script = script
        self._call = 0

    def generate_signal(self, **_kw) -> _FakeSignal:
        sig = self._script.get(self._call, _FakeSignal(action="hold"))
        self._call += 1
        return sig


def _ramp_df(n: int = 250, start: float = 50_000.0, drift: float = 0.0) -> pd.DataFrame:
    """Deterministic flat-ish OHLCV with a tiny drift."""
    closes = [start * (1 + drift) ** i for i in range(n)]
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open":  closes,
        "high":  [c * 1.001 for c in closes],
        "low":   [c * 0.999 for c in closes],
        "close": closes,
        "volume": [100.0] * n,
    })


def _drop_df(n: int = 250, start: float = 50_000.0) -> pd.DataFrame:
    """OHLCV that crashes 50% mid-run — used to trigger liquidation."""
    closes = []
    for i in range(n):
        if i < 220:
            closes.append(start)
        else:
            closes.append(start * 0.5)
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open":   closes,
        "high":   [c * 1.001 for c in closes],
        "low":    [c * 0.999 for c in closes],
        "close":  closes,
        "volume": [100.0] * n,
    })


def test_perp_long_then_close_yields_profit(config):
    from backtest.engine import BacktestEngine
    df = _ramp_df(n=250, drift=0.005)  # +0.5% per bar
    engine = BacktestEngine(config)
    # WINDOW=200 → first signal at index 200
    strategy = _ScriptedStrategy({
        0:  _FakeSignal(action="long", size_pct=0.1, leverage=5),
        20: _FakeSignal(action="close"),
    })
    result = engine.run(strategy, df, "BTC/USDT:USDT", "1h", initial_balance=10_000)
    assert result.total_return_pct > 0
    assert result.total_trades >= 2  # open + close
    assert result.winning_trades >= 1


def test_perp_long_liquidates_on_sharp_drop(config):
    from backtest.engine import BacktestEngine
    df = _drop_df(n=250, start=50_000.0)
    engine = BacktestEngine(config)
    # Open 10x long right before the crash; with 10x leverage, ~10% drop
    # triggers liquidation. We crash 50% — definitely liquidates.
    strategy = _ScriptedStrategy({
        0: _FakeSignal(action="long", size_pct=0.5, leverage=10),
    })
    result = engine.run(strategy, df, "BTC/USDT:USDT", "1h", initial_balance=10_000)
    liq_trades = [t for t in result.trades if t.side == "liquidation"]
    assert len(liq_trades) == 1
    # Margin was 50% of 10k = 5k. After liquidation, equity should reflect
    # ~5k loss (plus opening fee).
    assert result.final_balance < 5_500
    assert result.final_balance > 4_000  # but not zero


def test_perp_short_profits_when_price_drops(config):
    from backtest.engine import BacktestEngine
    df = _ramp_df(n=250, drift=-0.001)  # -0.1% per bar
    engine = BacktestEngine(config)
    strategy = _ScriptedStrategy({
        0:  _FakeSignal(action="short", size_pct=0.1, leverage=3),
        30: _FakeSignal(action="close"),
    })
    result = engine.run(strategy, df, "BTC/USDT:USDT", "1h", initial_balance=10_000)
    assert result.total_return_pct > 0


def test_spot_buy_sell_still_works(config, ohlcv_factory):
    """Backward compatibility: legacy momentum-style buy/sell semantics
    must continue to work on the same engine."""
    from backtest.engine import BacktestEngine
    df = ohlcv_factory(n=250, trend="up")
    engine = BacktestEngine(config)
    strategy = _ScriptedStrategy({
        0:  _FakeSignal(action="buy", size_pct=0.1),
        20: _FakeSignal(action="sell", size_pct=1.0),
    })
    result = engine.run(strategy, df, "BTC/USDT:USDT", "1h", initial_balance=10_000)
    assert result.total_trades == 2
    assert result.winning_trades + result.losing_trades == 1  # one closed leg
