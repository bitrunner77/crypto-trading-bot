"""
tests/test_ai_driven.py — exercises the AI-driven strategy + analyst pipeline
with a mocked anthropic client. Covers Haiku short-circuit, Opus escalation,
and malformed-JSON fallback paths.
"""
import json
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _mock_message(text: str):
    """Wrap a string into the anthropic SDK's Message shape."""
    block = MagicMock()
    block.text = text
    msg = MagicMock()
    msg.content = [block]
    return msg


def _haiku_payload(escalate: bool, strength: float = 0.4) -> str:
    return json.dumps({
        "escalate": escalate,
        "signal": "long" if escalate else "hold",
        "signal_strength": strength,
        "reason": "test",
    })


def _opus_payload(action: str = "long", size: float = 0.3, lev: int = 5) -> str:
    return json.dumps({
        "action": action,
        "symbol": "BTC/USDT:USDT",
        "size_pct": size,
        "leverage": lev,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "confidence": 0.8,
        "timeframe": "1h",
        "reasoning": "EMA crossover + RSI oversold",
        "key_signals": ["ema9>ema21", "rsi<30"],
        "risk_level": "medium",
        "changes_from_last_round": "",
    })


def _df():
    return pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=50, freq="1h"),
        "open":  [50_000] * 50, "high": [50_100] * 50,
        "low":   [49_900] * 50, "close": [50_050] * 50,
        "volume": [100] * 50,
    })


@pytest.fixture
def patched_anthropic():
    with patch("anthropic.Anthropic") as mock_anthropic:
        client = MagicMock()
        mock_anthropic.return_value = client
        yield client


def test_haiku_short_circuits_when_escalate_false(config, patched_anthropic):
    """If Haiku says no signal, Opus should NEVER be called (cost-saving)."""
    from strategies.ai_driven import AIDrivenStrategy
    patched_anthropic.messages.create.return_value = _mock_message(_haiku_payload(False))

    strategy = AIDrivenStrategy(config)
    sig = strategy.generate_signal(
        symbol="BTC/USDT:USDT", df=_df(),
        indicators={"price": 50_050},
        portfolio_value=10_000, cash_balance=8000,
        open_positions=[],
    )
    assert sig.action == "hold"
    assert patched_anthropic.messages.create.call_count == 1  # only Haiku


def test_opus_called_when_haiku_escalates(config, patched_anthropic):
    from strategies.ai_driven import AIDrivenStrategy

    # Haiku → Opus → RiskAssessment (3 calls)
    patched_anthropic.messages.create.side_effect = [
        _mock_message(_haiku_payload(True, 0.8)),
        _mock_message(_opus_payload(action="long", size=0.3, lev=5)),
        _mock_message(json.dumps({"approved": True, "reasoning": "ok"})),
    ]

    strategy = AIDrivenStrategy(config)
    sig = strategy.generate_signal(
        symbol="BTC/USDT:USDT", df=_df(),
        indicators={"price": 50_050},
        portfolio_value=10_000, cash_balance=8000,
        open_positions=[],
    )
    assert sig.action == "long"
    assert sig.leverage == 5
    assert sig.size_pct == pytest.approx(0.3)
    assert patched_anthropic.messages.create.call_count == 3


def test_malformed_opus_json_falls_back_to_hold(config, patched_anthropic):
    from strategies.ai_driven import AIDrivenStrategy

    patched_anthropic.messages.create.side_effect = [
        _mock_message(_haiku_payload(True, 0.9)),
        _mock_message("not even close to JSON"),  # attempt 1
        _mock_message("still not JSON"),           # attempt 2
        _mock_message("nope"),                     # attempt 3
    ]

    strategy = AIDrivenStrategy(config)
    sig = strategy.generate_signal(
        symbol="BTC/USDT:USDT", df=_df(),
        indicators={"price": 50_050},
        portfolio_value=10_000, cash_balance=8000,
        open_positions=[],
    )
    assert sig.action == "hold"
    assert sig.size_pct == 0.0
    assert sig.confidence == 0.0


def test_decision_validation_rejects_long_with_zero_size(config, patched_anthropic):
    """Self-contradictory action=long with size_pct=0 must be rejected
    rather than letting the bot place a 0-sized phantom order."""
    from ai.analyst import _validate_decision, AIResponseParseError
    from ai.schemas import TradeDecision

    # Pydantic auto-zeroes size_pct for hold/close, so we have to construct via dict
    decision = TradeDecision(
        action="long", symbol="BTC/USDT:USDT", size_pct=0.0001,  # > 0 to pass validator
        leverage=5, stop_loss_pct=0.03, take_profit_pct=0.06,
        confidence=0.5, timeframe="1h", reasoning="x",
    )
    decision = decision.model_copy(update={"size_pct": 0.0})  # smuggle in a bad value
    with pytest.raises(AIResponseParseError):
        _validate_decision(decision)


def test_parse_json_extracts_from_markdown_fence():
    from ai.analyst import _parse_json
    text = """Here's the analysis:

```json
{"escalate": true, "signal_strength": 0.7}
```

Extra prose."""
    data = _parse_json(text)
    assert data["escalate"] is True
    assert data["signal_strength"] == 0.7


def test_parse_json_raises_on_empty():
    from ai.analyst import _parse_json, AIResponseParseError
    with pytest.raises(AIResponseParseError):
        _parse_json("")
    with pytest.raises(AIResponseParseError):
        _parse_json("no json here, just words")


def test_parse_json_raises_on_array_response():
    from ai.analyst import _parse_json, AIResponseParseError
    # `_parse_json` finds first { and last } — an array-only response has no {
    with pytest.raises(AIResponseParseError):
        _parse_json("[1, 2, 3]")
