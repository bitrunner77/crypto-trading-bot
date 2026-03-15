"""
tests/test_backtest.py — Tests for the backtesting engine.
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


def make_ohlcv(n=300) -> pd.DataFrame:
    np.random.seed(99)
    prices = [30000.0]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1 + np.random.normal(0.001, 0.015)))
    df = pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open": prices,
        "high": [p * 1.005 for p in prices],
        "low": [p * 0.995 for p in prices],
        "close": prices,
        "volume": [np.random.uniform(100, 1000) for _ in prices],
    })
    return df


class TestBacktestEngine:
    def setup_method(self):
        from backtest.engine import BacktestEngine
        from strategies.momentum import MomentumStrategy
        self.engine = BacktestEngine(make_config())
        self.strategy = MomentumStrategy(make_config())

    def test_runs_without_error(self):
        df = make_ohlcv(300)
        result = self.engine.run(self.strategy, df, "BTC/USDT", "1h", initial_balance=10000.0)
        assert result is not None

    def test_result_fields(self):
        df = make_ohlcv(300)
        result = self.engine.run(self.strategy, df, "BTC/USDT", "1h", initial_balance=10000.0)
        assert result.symbol == "BTC/USDT"
        assert result.strategy == "momentum"
        assert result.initial_balance == 10000.0
        assert result.final_balance > 0
        assert 0.0 <= result.win_rate <= 1.0
        assert result.total_trades >= 0
        assert result.max_drawdown_pct >= 0.0

    def test_equity_curve_length(self):
        df = make_ohlcv(300)
        result = self.engine.run(self.strategy, df, "BTC/USDT", "1h", initial_balance=10000.0)
        # Equity curve should have one entry per candle after the window
        assert len(result.equity_curve) > 0

    def test_insufficient_data_raises(self):
        from backtest.engine import BacktestEngine
        engine = BacktestEngine(make_config())
        df = make_ohlcv(10)
        with pytest.raises(ValueError, match="Not enough data"):
            engine.run(self.strategy, df, "BTC/USDT", "1h")

    def test_summary_string(self):
        df = make_ohlcv(300)
        result = self.engine.run(self.strategy, df, "BTC/USDT", "1h", initial_balance=10000.0)
        summary = result.summary()
        assert "BTC/USDT" in summary
        assert "momentum" in summary
        assert "Win Rate" in summary
