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

    # ── Exchange ───────────────────────────────────────────────────────────────
    exchange: str = Field("bybit", description="CCXT exchange id")
    exchange_api_key: str = Field("", description="Exchange API key")
    exchange_api_secret: str = Field("", description="Exchange API secret / Hyperliquid private key")
    exchange_passphrase: str = Field("", description="Exchange API passphrase (BitGet, OKX, etc.)")
    exchange_wallet_address: str = Field("", description="Wallet address (Hyperliquid only)")

    # ── Trading ────────────────────────────────────────────────────────────────
    trading_mode: Literal["paper", "live"] = Field("paper")
    # Perpetual futures pairs. Hyperliquid uses USDC margin (BTC/USDC:USDC).
    trading_pairs: List[str] = Field(default=["BTC/USDC:USDC", "ETH/USDC:USDC", "SOL/USDC:USDC"])
    strategy: Literal["ai_driven", "momentum", "mean_reversion", "grid", "dca",
                      "regime_mean_reversion", "regime_momentum",
                      "scalping", "regime_scalping"] = Field("ai_driven")
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

    # ── Advanced Features ──────────────────────────────────────────────────────
    whale_volume_threshold: float = Field(3.0)
    session_sniper_mode:    bool  = Field(True)
    auto_optimize_weekly:   bool  = Field(True)
    strategy_rotation_enabled: bool = Field(True)
    equity_optimizer_enabled:  bool = Field(True)

    # ── V7 Features ────────────────────────────────────────────────────────────
    coin_pool: List[str] = Field(default_factory=list, description="Full scan universe for coin rotator")
    coin_rotation_enabled:      bool  = Field(True)
    coin_rotation_max_active:   int   = Field(2)
    hedging_enabled:            bool  = Field(True)
    hedge_trigger_pct:          float = Field(0.05)
    hedge_ratio:                float = Field(0.50)
    equity_protection_enabled:  bool  = Field(True)
    manipulation_detection:     bool  = Field(True)
    dynamic_leverage_enabled:   bool  = Field(True)
    session_learning_enabled:   bool  = Field(True)

    # ── V8 Features ────────────────────────────────────────────────────────────
    news_sentiment_enabled:      bool  = Field(True)
    messari_api_key:             str   = Field("")
    btc_dominance_enabled:       bool  = Field(True)
    portfolio_slots:             int   = Field(3)
    portfolio_allocator_enabled: bool  = Field(True)
    monthly_growth_target:       float = Field(0.10)
    growth_engine_enabled:       bool  = Field(True)

    # ── Telegram ───────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field("", description="Telegram bot token")
    telegram_chat_id:   str = Field("", description="Telegram chat ID")

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
