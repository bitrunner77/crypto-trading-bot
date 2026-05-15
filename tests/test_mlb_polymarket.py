"""
tests/test_mlb_polymarket.py — Unit tests for the Polymarket MLB module.
HTTP is fully mocked; nothing here hits the network.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from exchange.polymarket_client import (
    MLB_KEYWORDS,
    PolymarketClient,
    PolymarketMarket,
    PolymarketPaperBook,
)
from strategies.mlb_polymarket import MLBPolymarketStrategy


def make_config(**overrides):
    cfg = MagicMock()
    cfg.polymarket_min_edge = 0.02
    cfg.polymarket_max_stake_pct = 0.10
    cfg.polymarket_fee_pct = 0.0
    cfg.polymarket_min_liquidity = 0.0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def make_market(prices=("0.50", "0.50"), liquidity=1000.0) -> PolymarketMarket:
    raw = {
        "id": "12345",
        "question": "Will the Yankees beat the Red Sox?",
        "slug": "yankees-vs-red-sox-2025-05-15",
        "endDate": "2025-05-15T23:00:00Z",
        "outcomes": json.dumps(["Yes", "No"]),
        "clobTokenIds": json.dumps(["tok-yes", "tok-no"]),
        "outcomePrices": json.dumps(list(prices)),
        "liquidityNum": liquidity,
        "volumeNum": 5000.0,
    }
    m = PolymarketMarket.from_gamma(raw)
    assert m is not None
    return m


# ───────────────────────────── Parsing ─────────────────────────────────────

class TestPolymarketMarket:
    def test_parses_gamma_payload(self):
        m = make_market()
        assert m.id == "12345"
        assert m.outcomes == ["Yes", "No"]
        assert m.token_ids == ["tok-yes", "tok-no"]
        assert m.last_prices == [0.5, 0.5]

    def test_missing_token_ids_returns_none(self):
        raw = {"id": "1", "question": "?", "outcomes": json.dumps(["Yes", "No"])}
        assert PolymarketMarket.from_gamma(raw) is None

    def test_mlb_keyword_filter_catches_team_names(self):
        client = PolymarketClient()
        m = make_market()
        assert client._looks_mlb(m)

    def test_non_mlb_market_is_filtered_out(self):
        client = PolymarketClient()
        raw = {
            "id": "9", "question": "Will BTC hit 200k?", "slug": "btc-200k",
            "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["a", "b"]),
            "outcomePrices": json.dumps(["0.4", "0.6"]),
        }
        m = PolymarketMarket.from_gamma(raw)
        assert m is not None
        assert not client._looks_mlb(m)


# ───────────────────────────── Strategy ────────────────────────────────────

class TestMLBPolymarketStrategy:
    def setup_method(self):
        self.strategy = MLBPolymarketStrategy(make_config())

    def _book(self, ask_price: float, size: float = 100.0):
        return {"asks": [{"price": str(ask_price), "size": str(size)}]}

    def test_arbitrage_detected_when_asks_sum_below_one(self):
        m = make_market()
        books = {"tok-yes": self._book(0.45), "tok-no": self._book(0.45)}
        signals = self.strategy.evaluate(m, books, bankroll_usd=1000.0)
        assert len(signals) == 2
        assert all(s.kind == "arbitrage" for s in signals)
        # Equal payout shares: stake_a/price_a == stake_b/price_b
        s_yes, s_no = signals
        shares_yes = s_yes.stake_usd / s_yes.limit_price
        shares_no = s_no.stake_usd / s_no.limit_price
        assert shares_yes == pytest.approx(shares_no, rel=1e-6)
        assert s_yes.edge > 0

    def test_no_signal_when_market_efficient(self):
        m = make_market(prices=("0.50", "0.50"))
        books = {"tok-yes": self._book(0.50), "tok-no": self._book(0.50)}
        assert self.strategy.evaluate(m, books, bankroll_usd=1000.0) == []

    def test_value_signal_uses_last_price_as_fair_value(self):
        # last price is 0.60 → fair=0.60; ask is 0.50 → 10% raw edge
        m = make_market(prices=("0.60", "0.40"))
        books = {"tok-yes": self._book(0.50), "tok-no": self._book(0.55)}
        signals = self.strategy.evaluate(m, books, bankroll_usd=1000.0)
        assert any(s.kind == "value" and s.outcome == "Yes" for s in signals)

    def test_custom_fair_value_fn_is_used(self):
        called = []

        def fv(market, idx):
            called.append((market.id, idx))
            return 0.80 if idx == 0 else 0.20

        strat = MLBPolymarketStrategy(make_config(), fair_value_fn=fv)
        m = make_market(prices=("0.50", "0.50"))
        books = {"tok-yes": self._book(0.55), "tok-no": self._book(0.55)}
        signals = strat.evaluate(m, books, bankroll_usd=1000.0)
        assert called  # fair-value fn was consulted
        assert any(s.outcome == "Yes" and s.kind == "value" for s in signals)

    def test_skips_market_with_missing_orderbook(self):
        m = make_market()
        signals = self.strategy.evaluate(m, {"tok-yes": {}}, bankroll_usd=1000.0)
        assert signals == []

    def test_skips_non_binary_market(self):
        m = make_market()
        m.token_ids = ["a", "b", "c"]
        m.outcomes = ["A", "B", "C"]
        assert self.strategy.evaluate(m, {}, bankroll_usd=1000.0) == []

    def test_liquidity_filter(self):
        cfg = make_config(polymarket_min_liquidity=10_000)
        strat = MLBPolymarketStrategy(cfg)
        m = make_market(liquidity=500.0)
        books = {"tok-yes": self._book(0.40), "tok-no": self._book(0.40)}
        assert strat.evaluate(m, books, bankroll_usd=1000.0) == []


# ───────────────────────────── Paper book ──────────────────────────────────

class TestPolymarketPaperBook:
    def test_place_bet_deducts_balance(self):
        book = PolymarketPaperBook(balance=1000.0)
        m = make_market()
        bet = book.place_bet(m, outcome_index=0, limit_price=0.45, stake_usd=100.0)
        assert book.balance == pytest.approx(900.0)
        assert bet.shares == pytest.approx(100.0 / 0.45)
        assert bet.outcome == "Yes"

    def test_settle_winning_bet_credits_payout(self):
        book = PolymarketPaperBook(balance=1000.0)
        m = make_market()
        bet = book.place_bet(m, 0, 0.50, 100.0)
        book.settle(bet.id, won=True)
        # Won 200 shares × $1 each
        assert book.balance == pytest.approx(900.0 + 200.0)
        assert bet.settled and bet.payout_usd == pytest.approx(200.0)

    def test_settle_losing_bet_pays_zero(self):
        book = PolymarketPaperBook(balance=1000.0)
        m = make_market()
        bet = book.place_bet(m, 0, 0.50, 100.0)
        book.settle(bet.id, won=False)
        assert book.balance == pytest.approx(900.0)
        assert bet.settled and bet.payout_usd == 0.0

    def test_rejects_invalid_price(self):
        book = PolymarketPaperBook(balance=1000.0)
        m = make_market()
        with pytest.raises(ValueError):
            book.place_bet(m, 0, 1.5, 10.0)

    def test_rejects_overspend(self):
        book = PolymarketPaperBook(balance=10.0)
        m = make_market()
        with pytest.raises(ValueError):
            book.place_bet(m, 0, 0.5, 100.0)


# ───────────────────────────── HTTP client ─────────────────────────────────

class TestPolymarketClient:
    @pytest.mark.asyncio
    async def test_fetch_mlb_markets_filters_by_keyword(self, monkeypatch):
        client = PolymarketClient()
        mlb_raw = {
            "id": "1", "question": "Yankees vs Red Sox",
            "slug": "yankees-red-sox", "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["a", "b"]),
            "outcomePrices": json.dumps(["0.5", "0.5"]),
        }
        crypto_raw = {
            "id": "2", "question": "BTC > 100k?",
            "slug": "btc-100k", "outcomes": json.dumps(["Yes", "No"]),
            "clobTokenIds": json.dumps(["c", "d"]),
            "outcomePrices": json.dumps(["0.3", "0.7"]),
        }
        client._get = AsyncMock(return_value=[mlb_raw, crypto_raw])
        result = await client.fetch_mlb_markets(limit=10)
        ids = {m.id for m in result}
        assert ids == {"1"}

    @pytest.mark.asyncio
    async def test_fetch_orderbook_passes_token_id(self):
        client = PolymarketClient()
        client._get = AsyncMock(return_value={"bids": [], "asks": []})
        await client.fetch_orderbook("tok-123")
        client._get.assert_awaited_once()
        called_url, called_params = client._get.call_args.args[0], client._get.call_args.kwargs["params"]
        assert called_url.endswith("/book")
        assert called_params == {"token_id": "tok-123"}
