"""
strategies/registry.py — Strategy factory.
"""
from __future__ import annotations

from strategies.ai_driven import AIDrivenStrategy
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.grid import GridStrategy
from strategies.dca import DCAStrategy
from strategies.base import BaseStrategy


def get_strategy(name: str, config) -> BaseStrategy:
    """Return the strategy instance for the given name."""
    strategies = {
        "ai_driven": AIDrivenStrategy,
        "momentum": MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "grid": GridStrategy,
        "dca": DCAStrategy,
    }
    cls = strategies.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(strategies.keys())}")
    return cls(config)
