"""
ai/schemas.py — Pydantic models for AI input/output.
Updated for perpetuals: long/short directions, leverage, aggressive growth mode.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class TradeDecision(BaseModel):
    """Structured output from Claude Opus market analysis."""
    action: Literal["long", "short", "close", "hold"]
    symbol: str
    size_pct: float = Field(
        ge=0.0, le=1.0,
        description="Fraction of remaining budget to risk as margin (0.0–0.5 max per rule)"
    )
    leverage: int = Field(
        ge=1, le=20,
        description="Leverage multiplier (1–20x hard cap)"
    )
    stop_loss_pct: float = Field(
        ge=0.0, le=0.5,
        description="Stop-loss distance as fraction of entry price"
    )
    take_profit_pct: float = Field(
        ge=0.0, le=5.0,
        description="Take-profit distance as fraction of entry price"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="AI confidence in this decision (0.0–1.0)"
    )
    timeframe: str = Field(description="Timeframe this decision is based on")
    reasoning: str = Field(description="Spoken chain-of-thought — stated out loud before trade")
    key_signals: List[str] = Field(
        default_factory=list,
        description="Top 3–5 signals driving this decision"
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Overall risk assessment"
    )
    changes_from_last_round: str = Field(
        default="",
        description="What changed vs last round and why"
    )

    @field_validator("size_pct")
    @classmethod
    def zero_for_hold_or_close(cls, v, info):
        action = info.data.get("action")
        if action in ("hold", "close"):
            return 0.0
        return v

    @field_validator("leverage")
    @classmethod
    def zero_leverage_for_hold(cls, v, info):
        action = info.data.get("action")
        if action in ("hold", "close"):
            return 1
        return v


class RiskAssessment(BaseModel):
    """Second-pass risk validation from Claude Opus."""
    approved: bool
    adjusted_size_pct: Optional[float] = None
    adjusted_leverage: Optional[int] = None
    adjusted_stop_loss_pct: Optional[float] = None
    concerns: List[str] = Field(default_factory=list)
    reasoning: str = ""


class MacroContext(BaseModel):
    """High-level market context from the macro analysis pass."""
    market_sentiment: Literal["bullish", "bearish", "neutral", "volatile"]
    btc_trend: str
    risk_on: bool
    key_themes: List[str] = Field(default_factory=list)
    summary: str = ""
