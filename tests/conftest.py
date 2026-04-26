"""
tests/conftest.py — shared pytest fixtures.

Consolidates the make_config / make_ohlcv helpers that were duplicated
across test_ai.py, test_backtest.py, test_risk.py, test_strategies.py.
Existing tests still define their own helpers; new tests should prefer
these fixtures.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


# ── Config fixtures ────────────────────────────────────────────────────────

def _base_config() -> MagicMock:
    """A MagicMock pre-populated with realistic config values."""
    cfg = MagicMock()
    cfg.exchange = "bybit"
    cfg.exchange_api_key = ""
    cfg.exchange_api_secret = ""
    cfg.exchange_wallet_address = ""
    cfg.trading_mode = "paper"
    cfg.trading_pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    cfg.strategy = "ai_driven"
    cfg.timeframe = "1h"
    cfg.loop_interval_seconds = 60
    cfg.paper_initial_balance = 10000.0
    cfg.paper_slippage_pct = 0.001
    cfg.max_leverage = 20
    cfg.risk_max_position_pct = 0.50
    cfg.risk_max_drawdown_pct = 0.90
    cfg.risk_stop_loss_pct = 0.03
    cfg.risk_take_profit_pct = 0.06
    cfg.risk_max_daily_loss_pct = 0.50
    cfg.grid_levels = 10
    cfg.grid_spread_pct = 0.01
    cfg.dca_interval_hours = 24
    cfg.dca_amount_usdt = 100.0
    cfg.anthropic_api_key = "test-key"
    cfg.ai_model_opus = "claude-opus-4-7"
    cfg.ai_model_haiku = "claude-haiku-4-5-20251001"
    cfg.ai_max_tokens = 4096
    cfg.ai_screen_max_tokens = 256
    cfg.ai_escalate_threshold = 0.55
    cfg.log_level = "WARNING"
    return cfg


@pytest.fixture
def config() -> MagicMock:
    """Default test config — override individual fields by mutating the result."""
    return _base_config()


# ── OHLCV fixture ──────────────────────────────────────────────────────────

def _synthetic_ohlcv(n: int = 200, trend: str = "flat", start: float = 50_000.0,
                     seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = {"up": 0.002, "down": -0.002, "flat": 0.0}[trend]
    closes = [start]
    for _ in range(n - 1):
        change = rng.normal(drift, 0.01)
        closes.append(closes[-1] * (1 + change))
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=n, freq="1h"),
        "open":   closes,
        "high":   [c * 1.005 for c in closes],
        "low":    [c * 0.995 for c in closes],
        "close":  closes,
        "volume": rng.uniform(100, 1000, n),
    })


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """A 200-bar synthetic flat-trend OHLCV frame."""
    return _synthetic_ohlcv()


@pytest.fixture
def ohlcv_factory():
    """Factory: ohlcv_factory(n=300, trend='up') for parametric data."""
    return _synthetic_ohlcv


# ── Database fixture ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path, monkeypatch) -> Iterator[Path]:
    """Point data.database.DB_PATH at an isolated per-test SQLite file."""
    import data.database as db_mod
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    db_mod.init_db()
    yield db_file


# ── Anthropic key in env (some tests rely on settings loading) ─────────────

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
