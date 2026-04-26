"""
config.py — Centralised settings loaded from .env via pydantic-settings.
All modules import `settings` from here.
"""
from __future__ import annotations

from typing import List, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Anthropic ──────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., description="Anthropic API key")
    ai_model_opus: str = Field("claude-opus-4-7", description="Claude model for deep analysis")
    ai_model_haiku: str = Field("claude-haiku-4-5-20251001", description="Claude model for fast screen")
    ai_max_tokens: int = Field(4096, description="Max tokens for Opus analysis response")
    ai_screen_max_tokens: int = Field(256, description="Max tokens for Haiku screen response")
    ai_escalate_threshold: float = Field(0.55, description="Min Haiku signal_strength to call Opus")

    # ── Exchange ───────────────────────────────────────────────────────────────
    exchange: str = Field("bybit", description="CCXT exchange id")
    exchange_api_key: str = Field("", description="Exchange API key (not used by Hyperliquid)")
    exchange_api_secret: str = Field("", description="Exchange API secret / Hyperliquid private key")
    exchange_wallet_address: str = Field("", description="Wallet address (Hyperliquid only)")

    # ── Trading ────────────────────────────────────────────────────────────────
    trading_mode: Literal["paper", "live"] = Field("paper")
    # Perpetual futures pairs. Hyperliquid uses USDC margin (BTC/USDC:USDC).
    trading_pairs: List[str] = Field(default=["BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC"])
    strategy: Literal["ai_driven", "momentum", "mean_reversion", "grid", "dca"] = Field("ai_driven")
    timeframe: str = Field("1h")
    loop_interval_seconds: int = Field(60)

    # ── Paper Trading ──────────────────────────────────────────────────────────
    paper_initial_balance: float = Field(10000.0)
    paper_slippage_pct: float = Field(0.001)

    # ── Leverage ───────────────────────────────────────────────────────────────
    max_leverage: int = Field(20, description="Maximum leverage allowed (hard cap: 20x)")

    # ── Risk Management ────────────────────────────────────────────────────────
    # Rule: max 50% of remaining budget per trade
    risk_max_position_pct: float = Field(0.50)
    risk_max_drawdown_pct: float = Field(0.90)  # Only halt at ~wipeout; $0 check is the real gate
    risk_stop_loss_pct: float = Field(0.03)
    risk_take_profit_pct: float = Field(0.06)
    risk_max_daily_loss_pct: float = Field(0.50)  # Aggressive growth — wide daily limit

    # ── Grid Strategy ──────────────────────────────────────────────────────────
    grid_levels: int = Field(10)
    grid_spread_pct: float = Field(0.01)

    # ── DCA Strategy ───────────────────────────────────────────────────────────
    dca_interval_hours: int = Field(24)
    dca_amount_usdt: float = Field(100.0)

    # ── Backtesting ────────────────────────────────────────────────────────────
    backtest_start: str = Field("2024-01-01")
    backtest_end: str = Field("2024-12-31")

    # ── Logging ────────────────────────────────────────────────────────────────
    log_level: str = Field("INFO")

    @field_validator("trading_pairs", mode="before")
    @classmethod
    def parse_pairs(cls, v):
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v


def load_settings() -> Settings:
    """Load and return settings, raising a clear error on misconfiguration."""
    try:
        return Settings()
    except Exception as exc:
        raise SystemExit(
            f"\n[CONFIG ERROR] Failed to load settings: {exc}\n"
            "  → Copy .env.example to .env and fill in your values.\n"
        ) from exc


settings: Settings = load_settings()
