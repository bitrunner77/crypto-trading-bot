"""
tests/test_ai.py — Tests for AI schemas and prompt building (no real API calls).
Rules: long/short perps, leverage 1-20, size_pct 0-0.5, reasoning required.
"""
import pytest
from unittest.mock import MagicMock, patch

from ai.schemas import TradeDecision, RiskAssessment, MacroContext
from ai.prompts import build_asset_prompt, build_risk_prompt


def make_snapshot():
    from analysis.market_context import MarketSnapshot
    return MarketSnapshot(
        symbol="BTC/USDT:USDT",
        timeframe="1h",
        current_price=65000.0,
        indicators={
            "rsi_14": 55.0,
            "macd": 100.0,
            "ema_9": 64500.0,
            "ema_21": 64000.0,
            "price": 65000.0,
            "trend": "bullish",
        },
        recent_candles=[],
        portfolio_value=10500.0,
        cash_balance=5000.0,
        open_positions=[],
        recent_trades=[],
        trade_stats={"total": 5, "wins": 3, "win_rate": 0.6, "avg_pnl": 50.0, "total_pnl": 250.0},
        timestamp="2024-01-15T12:00:00",
    )


class TestTradeDecision:
    def test_valid_long(self):
        d = TradeDecision(
            action="long", symbol="BTC/USDT:USDT",
            size_pct=0.3, leverage=10,
            stop_loss_pct=0.03, take_profit_pct=0.06,
            confidence=0.8, timeframe="1h",
            reasoning="Strong bullish setup with EMA crossover",
            key_signals=["EMA9>EMA21", "RSI=55", "volume_spike"],
        )
        assert d.action == "long"
        assert d.leverage == 10
        assert d.size_pct == 0.3

    def test_valid_short(self):
        d = TradeDecision(
            action="short", symbol="ETH/USDT:USDT",
            size_pct=0.2, leverage=5,
            stop_loss_pct=0.03, take_profit_pct=0.09,
            confidence=0.75, timeframe="4h",
            reasoning="Bearish divergence on RSI",
        )
        assert d.action == "short"

    def test_hold_forces_zero_size_and_leverage(self):
        d = TradeDecision(
            action="hold", symbol="SOL/USDT:USDT",
            size_pct=0.5, leverage=20,  # Both overridden
            stop_loss_pct=0.03, take_profit_pct=0.06,
            confidence=0.3, timeframe="1h", reasoning="Unclear market",
        )
        assert d.size_pct == 0.0
        assert d.leverage == 1

    def test_size_pct_max_1(self):
        with pytest.raises(Exception):
            TradeDecision(
                action="long", symbol="BTC/USDT:USDT",
                size_pct=1.5,  # > 1.0 invalid
                leverage=5,
                stop_loss_pct=0.03, take_profit_pct=0.06,
                confidence=0.8, timeframe="1h", reasoning="test",
            )

    def test_leverage_max_20(self):
        with pytest.raises(Exception):
            TradeDecision(
                action="long", symbol="BTC/USDT:USDT",
                size_pct=0.3, leverage=25,  # > 20 invalid
                stop_loss_pct=0.03, take_profit_pct=0.06,
                confidence=0.8, timeframe="1h", reasoning="test",
            )

    def test_changes_from_last_round_field(self):
        d = TradeDecision(
            action="long", symbol="BTC/USDT:USDT",
            size_pct=0.3, leverage=5,
            stop_loss_pct=0.03, take_profit_pct=0.06,
            confidence=0.8, timeframe="1h",
            reasoning="Momentum accelerating",
            changes_from_last_round="Switching from short to long — trend reversed",
        )
        assert "trend reversed" in d.changes_from_last_round


class TestRiskAssessment:
    def test_approved_with_leverage_adjustment(self):
        ra = RiskAssessment(
            approved=True,
            adjusted_leverage=10,
            reasoning="Capped leverage at 10x for safety"
        )
        assert ra.approved is True
        assert ra.adjusted_leverage == 10

    def test_rejected_over_leverage(self):
        ra = RiskAssessment(
            approved=False,
            concerns=["Leverage 50x exceeds 20x hard cap"],
            reasoning="Rejected: leverage too high"
        )
        assert ra.approved is False


class TestPromptBuilding:
    def test_asset_prompt_contains_symbol_and_leverage_rules(self):
        snapshot = make_snapshot()
        prompt = build_asset_prompt(snapshot)
        assert "BTC/USDT:USDT" in prompt
        assert "20x" in prompt or "20" in prompt
        assert "50%" in prompt or "0.5" in prompt
        assert "long" in prompt.lower() or "short" in prompt.lower()

    def test_asset_prompt_contains_reasoning_instruction(self):
        snapshot = make_snapshot()
        prompt = build_asset_prompt(snapshot)
        assert "reasoning" in prompt.lower()

    def test_risk_prompt_contains_leverage(self):
        config = MagicMock()
        config.max_leverage = 20
        config.risk_max_position_pct = 0.5
        config.risk_max_drawdown_pct = 0.9
        decision = {"action": "long", "size_pct": 0.3, "leverage": 10, "symbol": "BTC/USDT:USDT"}
        prompt = build_risk_prompt(decision, 10000, 5000, 0.05, config)
        assert "20" in prompt  # max leverage mentioned
        assert "notional" in prompt.lower() or "leverage" in prompt.lower()


class TestMarketAnalystMocked:
    def test_fallback_on_api_failure(self):
        from ai.analyst import MarketAnalyst
        config = MagicMock()
        config.anthropic_api_key = "test-key"
        config.max_leverage = 20
        config.risk_stop_loss_pct = 0.03
        config.risk_take_profit_pct = 0.06
        config.paper_initial_balance = 10000.0
        config.risk_max_position_pct = 0.5
        config.risk_max_drawdown_pct = 0.9

        snapshot = make_snapshot()

        with patch("anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = Exception("API unavailable")
            mock_anthropic.return_value = mock_client

            analyst = MarketAnalyst(config)
            decision, risk = analyst.analyze(snapshot)

            assert decision.action == "hold"
            assert decision.confidence == 0.0
