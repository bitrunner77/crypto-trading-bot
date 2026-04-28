"""
strategies/registry.py — Strategy factory.
"""
from __future__ import annotations

from strategies.ai_driven import AIDrivenStrategy
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.grid import GridStrategy
from strategies.dca import DCAStrategy
from strategies.regime_filtered import RegimeFilteredStrategy
from strategies.scalping import ScalpingStrategy
from strategies.base import BaseStrategy


def get_strategy(name: str, config) -> BaseStrategy:
    """Return the strategy instance for the given name."""
    if name == "regime_mean_reversion":
        return RegimeFilteredStrategy(config, MeanReversionStrategy(config), leverage=config.max_leverage)
    if name == "regime_momentum":
        return RegimeFilteredStrategy(config, MomentumStrategy(config), leverage=config.max_leverage)
    if name == "regime_scalping":
        return RegimeFilteredStrategy(config, ScalpingStrategy(config), leverage=config.max_leverage)

    strategies = {
        "ai_driven":      AIDrivenStrategy,
        "momentum":       MomentumStrategy,
        "mean_reversion": MeanReversionStrategy,
        "grid":           GridStrategy,
        "dca":            DCAStrategy,
        "scalping":       ScalpingStrategy,
    }
    cls = strategies.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Choose from: {list(strategies.keys())}")
    return cls(config)
