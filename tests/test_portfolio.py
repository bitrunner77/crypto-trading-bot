"""
tests/test_portfolio.py — async portfolio tracker tests.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_positions.return_value = []
    db.save_portfolio_snapshot.return_value = None
    db.get_trade_stats.return_value = {"total": 0, "win_rate": 0.0,
                                       "total_pnl": 0.0, "avg_pnl": 0.0}
    db.get_portfolio_history.return_value = []
    return db


@pytest.fixture
def mock_exchange():
    ex = MagicMock()
    ex.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 8000.0, "used": 2000.0, "total": 10000.0},
        "info": {},
    })
    return ex


@pytest.mark.asyncio
async def test_refresh_uses_exchange_balance(config, mock_exchange, mock_db):
    from portfolio.tracker import PortfolioTracker
    pt = PortfolioTracker(mock_exchange, config, mock_db)
    snap = await pt.refresh()
    assert snap["total_value"] == pytest.approx(10_000.0)
    assert snap["cash_balance"] == pytest.approx(8000.0)
    mock_exchange.fetch_balance.assert_awaited_once()


@pytest.mark.asyncio
async def test_initial_value_locks_in(config, mock_exchange, mock_db):
    from portfolio.tracker import PortfolioTracker
    pt = PortfolioTracker(mock_exchange, config, mock_db)
    await pt.refresh()
    # balance shifts but initial_value is sticky
    mock_exchange.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 12_000.0, "used": 0.0, "total": 12_000.0},
        "info": {},
    })
    snap = await pt.refresh()
    assert snap["initial_value"] == pytest.approx(10_000.0)
    assert snap["pnl_total"] == pytest.approx(2000.0)


@pytest.mark.asyncio
async def test_drawdown_from_peak(config, mock_exchange, mock_db):
    from portfolio.tracker import PortfolioTracker
    pt = PortfolioTracker(mock_exchange, config, mock_db)
    # peak: 15k, then drop to 12k → 20% drawdown
    mock_exchange.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 15_000.0, "used": 0.0, "total": 15_000.0},
        "info": {},
    })
    await pt.refresh()
    mock_exchange.fetch_balance = AsyncMock(return_value={
        "USDT": {"free": 12_000.0, "used": 0.0, "total": 12_000.0},
        "info": {},
    })
    snap = await pt.refresh()
    assert snap["peak_value"] == pytest.approx(15_000.0)
    assert snap["drawdown"] == pytest.approx(0.20, rel=1e-3)


def test_realized_pnl_uses_order_pnl_when_present():
    from portfolio.tracker import PortfolioTracker
    order = {"pnl": 123.45}
    assert PortfolioTracker.realized_pnl_from_order(order, None) == pytest.approx(123.45)


def test_realized_pnl_reconstructs_long():
    from portfolio.tracker import PortfolioTracker
    order = {"close_price": 55_000}
    pos = {"entry_price": 50_000, "side": "long", "amount": 100, "leverage": 10}
    pnl = PortfolioTracker.realized_pnl_from_order(order, pos)
    # notional = 1000, +10% move → +100
    assert pnl == pytest.approx(100.0)


def test_realized_pnl_reconstructs_short():
    from portfolio.tracker import PortfolioTracker
    order = {"close_price": 47_500}
    pos = {"entry_price": 50_000, "side": "short", "amount": 200, "leverage": 5}
    pnl = PortfolioTracker.realized_pnl_from_order(order, pos)
    # notional = 1000, +5% favorable for short → +50
    assert pnl == pytest.approx(50.0)


def test_realized_pnl_safe_when_data_missing():
    from portfolio.tracker import PortfolioTracker
    assert PortfolioTracker.realized_pnl_from_order({}, None) == 0.0
    assert PortfolioTracker.realized_pnl_from_order({"close_price": 0}, {"entry_price": 100}) == 0.0
