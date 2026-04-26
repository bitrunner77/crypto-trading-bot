"""
tests/test_paper_exchange.py — exercises the paper trading engine which
was previously untested (296 lines, safety-critical).
"""
import asyncio
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def paper(config, monkeypatch):
    """A PaperExchange whose fetch_ticker is mocked to return a fixed price."""
    from exchange.paper_exchange import PaperExchange
    px = PaperExchange(config)
    px.fetch_ticker = AsyncMock(return_value={"last": 50_000.0, "close": 50_000.0})
    return px


@pytest.mark.asyncio
async def test_open_long_deducts_margin_and_fee(paper):
    start = paper.get_cash_balance()
    order = await paper.create_order("BTC/USDT:USDT", "long", amount=1000.0, leverage=5)
    assert order["status"] == "closed"
    # Margin (1000) + fee (notional * 0.0005 = 5000*0.0005 = 2.5) deducted
    assert paper.get_cash_balance() == pytest.approx(start - 1000.0 - 2.5, rel=1e-3)
    pos = paper.get_positions()["BTC/USDT:USDT"]
    assert pos["direction"] == "long"
    assert pos["leverage"] == 5
    assert pos["notional"] == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_open_short_inverts_slippage(paper, config):
    config.paper_slippage_pct = 0.01  # exaggerate
    paper._slippage = 0.01
    await paper.create_order("BTC/USDT:USDT", "short", amount=500.0, leverage=2)
    pos = paper.get_positions()["BTC/USDT:USDT"]
    # Short fills slightly below mid (favorable for the seller's mark-down)
    assert pos["entry_price"] == pytest.approx(50_000.0 * (1 - 0.01))


@pytest.mark.asyncio
async def test_close_returns_pnl_and_balance_credit(paper):
    await paper.create_order("BTC/USDT:USDT", "long", amount=1000.0, leverage=10)
    paper.fetch_ticker = AsyncMock(return_value={"last": 55_000.0, "close": 55_000.0})
    bal_mid = paper.get_cash_balance()

    order = await paper.create_order("BTC/USDT:USDT", "close", amount=0)
    assert order["status"] == "closed"
    assert order["pnl"] > 0  # 10x long up 10% → +$1000-ish notional
    assert "BTC/USDT:USDT" not in paper.get_positions()
    assert paper.get_cash_balance() > bal_mid  # margin + pnl returned


@pytest.mark.asyncio
async def test_liquidation_triggers_when_price_crosses_liq(paper):
    await paper.create_order("BTC/USDT:USDT", "long", amount=1000.0, leverage=20)
    pos = paper.get_positions()["BTC/USDT:USDT"]
    liq = pos["liq_price"]
    # Price slips one tick below liquidation
    liquidated = await paper.check_liquidations({"BTC/USDT:USDT": liq - 1})
    assert liquidated == ["BTC/USDT:USDT"]
    assert paper.get_positions() == {}


@pytest.mark.asyncio
async def test_insufficient_balance_raises(paper):
    with pytest.raises(ValueError):
        # 100x more than starting balance
        await paper.create_order(
            "BTC/USDT:USDT", "long",
            amount=paper.get_cash_balance() * 2, leverage=1,
        )


@pytest.mark.asyncio
async def test_close_with_no_position_is_skipped(paper):
    order = await paper.create_order("BTC/USDT:USDT", "close", amount=0)
    assert order["status"] == "skipped"


@pytest.mark.asyncio
async def test_short_pnl_when_price_drops(paper):
    await paper.create_order("BTC/USDT:USDT", "short", amount=1000.0, leverage=5)
    paper.fetch_ticker = AsyncMock(return_value={"last": 47_500.0, "close": 47_500.0})
    order = await paper.create_order("BTC/USDT:USDT", "close", amount=0)
    assert order["pnl"] > 0


@pytest.mark.asyncio
async def test_loss_capped_at_margin(paper):
    """If price moves against position by more than 1/leverage, the user
    cannot lose more than they put up — balance is capped at >= 0."""
    start = paper.get_cash_balance()
    await paper.create_order("BTC/USDT:USDT", "long", amount=1000.0, leverage=2)
    # 60% adverse move — well past 50% liquidation point for 2x
    paper.fetch_ticker = AsyncMock(return_value={"last": 20_000.0, "close": 20_000.0})
    await paper.create_order("BTC/USDT:USDT", "close", amount=0)
    assert paper.get_cash_balance() >= 0
    # Loss bounded by margin (plus open/close fees)
    assert paper.get_cash_balance() >= start - 1000.0 - 10
