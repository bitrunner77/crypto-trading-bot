"""
tests/test_cost_tracker.py — Anthropic token / cost accounting.
"""
from types import SimpleNamespace

import pytest


def test_records_opus_cost():
    from utils.cost_tracker import CostTracker
    t = CostTracker()
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000)
    t.record("claude-opus-4-7", usage=usage)
    summary = t.get_summary()
    # opus pricing 15 + 75 per 1M tokens
    assert summary["per_model"]["claude-opus-4-7"]["cost_usd"] == pytest.approx(90.0)
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == pytest.approx(90.0)


def test_records_haiku_cost():
    from utils.cost_tracker import CostTracker
    t = CostTracker()
    usage = SimpleNamespace(input_tokens=200_000, output_tokens=50_000)
    t.record("claude-haiku-4-5-20251001", usage=usage)
    summary = t.get_summary()
    # haiku 1.0 + 5.0 per 1M
    expected = 0.2 * 1.0 + 0.05 * 5.0
    assert summary["total_cost_usd"] == pytest.approx(expected)


def test_unknown_model_is_zero_cost_but_still_counted():
    from utils.cost_tracker import CostTracker
    t = CostTracker()
    t.record("some-future-model", input_tokens=1000, output_tokens=1000)
    summary = t.get_summary()
    assert summary["total_calls"] == 1
    assert summary["total_cost_usd"] == 0.0


def test_accumulates_across_calls():
    from utils.cost_tracker import CostTracker
    t = CostTracker()
    for _ in range(5):
        t.record("claude-haiku-4-5-20251001", input_tokens=10_000, output_tokens=1_000)
    summary = t.get_summary()
    assert summary["per_model"]["claude-haiku-4-5-20251001"]["calls"] == 5


def test_reset():
    from utils.cost_tracker import CostTracker
    t = CostTracker()
    t.record("claude-opus-4-7", input_tokens=100, output_tokens=100)
    t.reset()
    assert t.get_summary()["total_calls"] == 0
