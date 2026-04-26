"""
utils/cost_tracker.py — Tracks Anthropic token usage and estimates session
cost. Updated after every Claude call so the dashboard can show:

    AI cost this session: $0.42  (3 Opus calls, 17 Haiku calls)

Prices below are USD per 1M tokens (input / output) for the public-facing
SKUs and are intentionally hard-coded — they're a sanity-check, not billing
truth. Override via PRICES_USD_PER_M if a model's pricing changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from threading import Lock
from typing import Dict, Optional

logger = logging.getLogger("cryptobot.cost")


# (input $/1M, output $/1M) — keep keys lowercase, match by prefix
PRICES_USD_PER_M: Dict[str, tuple[float, float]] = {
    "claude-opus-4":   (15.00, 75.00),
    "claude-sonnet-4": (3.00,  15.00),
    "claude-haiku-4":  (1.00,  5.00),
}


def _price_for(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    for prefix, prices in PRICES_USD_PER_M.items():
        if m.startswith(prefix):
            return prices
    return (0.0, 0.0)


@dataclass
class _ModelStats:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class CostTracker:
    """Thread-safe per-process accumulator. Use the module-level singleton
    `tracker` from anywhere; tests should construct their own instance.
    """

    def __init__(self) -> None:
        self._stats: Dict[str, _ModelStats] = {}
        self._lock = Lock()

    def record(
        self,
        model: str,
        usage: Optional[object] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        """Record a Claude call. `usage` is the SDK's `message.usage`; if
        provided, fields are read from it. Otherwise pass tokens explicitly.
        """
        if usage is not None:
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)

        in_price, out_price = _price_for(model)
        cost = (in_tok / 1_000_000) * in_price + (out_tok / 1_000_000) * out_price

        with self._lock:
            s = self._stats.setdefault(model, _ModelStats())
            s.calls += 1
            s.input_tokens += in_tok
            s.output_tokens += out_tok
            s.cost_usd += cost

    def get_summary(self) -> Dict:
        """Snapshot for dashboards. Returned dict is safe to mutate."""
        with self._lock:
            per_model = {
                model: {
                    "calls": s.calls,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cost_usd": round(s.cost_usd, 4),
                }
                for model, s in self._stats.items()
            }
            total_calls = sum(s.calls for s in self._stats.values())
            total_cost = round(sum(s.cost_usd for s in self._stats.values()), 4)
        return {
            "per_model": per_model,
            "total_calls": total_calls,
            "total_cost_usd": total_cost,
        }

    def reset(self) -> None:
        with self._lock:
            self._stats.clear()


tracker = CostTracker()
