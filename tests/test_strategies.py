"""
tests/test_strategies.py — Unit tests for trading strategies.
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock


def make_config():
    cfg = MagicMock()
    cfg.risk_max_position_pct = 0.05
    cfg.risk_stop_loss_pct = 0.02
    cfg.risk_take_profit_pct = 0.04
    cfg.grid_levels = 10
    cfg.grid_spread_pct = 0.01
    cfg.dca_interval_hours = 24
    cfg.dca_amount_usdt = 100.0
    return cfg


def make_ohlcv(n=100, trend="up") -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(42)
    prices = [50000.0]
    for _ in range(n - 1):
        if trend == "up":
            change = np.random.normal(0.002, 0.01)
        elif trend == "down":
            change = np.random.normal(-0.002, 0.01)
        else:
            change = np.random.normal(0, 0.01)
        prices.append(prices[-1] * (1 + change))

    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": prices,
        "high": [p * 1.005 for p in prices],
        "low": [p * 0.995 for p in prices],
        "close": prices,
        "volume": [np.random.uniform(100, 1000) for _ in prices],
    })
    return df


def make_indicators(df: pd.DataFrame) -> dict:
    from analysis.indicators import compute_indicators
    return compute_indicators(df)


class TestMomentumStrategy:
    def setup_method(self):
        from strategies.momentum import MomentumStrategy
        self.strategy = MomentumStrategy(make_config())

    def test_hold_on_insufficient_data(self):
        df = make_ohlcv(n=5)
        signal = self.strategy.generate_signal("BTC/USDT", df, {}, 10000, 5000, [])
        assert signal.action == "hold"

    def test_returns_valid_signal(self):
        df = make_ohlcv(n=100, trend="up")
        indicators = make_indicators(df)
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5000, [])
        assert signal.action in ("buy", "sell", "hold")
        assert 0.0 <= signal.confidence <= 1.0
        assert signal.symbol == "BTC/USDT"
        assert signal.strategy_name == "momentum"

    def test_no_buy_with_existing_position(self):
        df = make_ohlcv(n=100, trend="up")
        indicators = make_indicators(df)
        positions = [{"symbol": "BTC/USDT", "side": "buy", "entry_price": 48000, "amount": 0.1}]
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 0, positions)
        # With a position, should not generate another buy
        assert signal.action != "buy"


class TestMeanReversionStrategy:
    def setup_method(self):
        from strategies.mean_reversion import MeanReversionStrategy
        self.strategy = MeanReversionStrategy(make_config())

    def test_hold_on_insufficient_data(self):
        df = make_ohlcv(n=5)
        signal = self.strategy.generate_signal("ETH/USDT", df, {}, 10000, 5000, [])
        assert signal.action == "hold"

    def test_valid_signal_structure(self):
        df = make_ohlcv(n=100)
        indicators = make_indicators(df)
        signal = self.strategy.generate_signal("ETH/USDT", df, indicators, 10000, 5000, [])
        assert signal.action in ("buy", "sell", "hold")
        assert signal.strategy_name == "mean_reversion"


class TestDCAStrategy:
    def setup_method(self):
        from strategies.dca import DCAStrategy
        self.strategy = DCAStrategy(make_config())

    def test_first_buy_always_triggers(self):
        df = make_ohlcv(n=50)
        indicators = make_indicators(df)
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5000, [])
        assert signal.action == "buy"

    def test_no_second_buy_immediately(self):
        df = make_ohlcv(n=50)
        indicators = make_indicators(df)
        # First buy
        self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5000, [])
        # Second call immediately after
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5000, [])
        assert signal.action == "hold"

    def test_insufficient_cash_holds(self):
        df = make_ohlcv(n=50)
        indicators = make_indicators(df)
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5, [])
        assert signal.action == "hold"


class TestGridStrategy:
    def setup_method(self):
        from strategies.grid import GridStrategy
        self.strategy = GridStrategy(make_config())

    def test_first_call_initializes_grid(self):
        df = make_ohlcv(n=50)
        indicators = make_indicators(df)
        signal = self.strategy.generate_signal("BTC/USDT", df, indicators, 10000, 5000, [])
        # First call should hold (grid initialization)
        assert signal.action == "hold"
        assert self.strategy._initialized
