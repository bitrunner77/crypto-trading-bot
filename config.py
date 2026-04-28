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
    # Single exchange (backward compat). If EXCHANGES is set, it takes precedence.
    exchange: str = Field("bitget", description="Primary CCXT exchange id")
    # Multi-exchange: comma-separated or JSON list, e.g. ["bitget","toobit"]
    exchanges: List[str] = Field(default=[], description="All exchanges to trade on simultaneously")
    exchange_api_key: str = Field("", description="Exchange API key")
    exchange_api_secret: str = Field("", description="Exchange API secret")
    exchange_wallet_address: str = Field("", description="Wallet address (Hyperliquid only)")
    # Per-exchange credentials: BITGET_API_KEY, BITGET_API_SECRET, TOOBIT_API_KEY, etc.
    bitget_api_key: str = Field("", description="Bitget API key")
    bitget_api_secret: str = Field("", description="Bitget API secret")
    bitget_passphrase: str = Field("", description="Bitget passphrase")
    toobit_api_key: str = Field("", description="Toobit API key")
    toobit_api_secret: str = Field("", description="Toobit API secret")

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

    @field_validator("trading_pairs", "exchanges", mode="before")
    @classmethod
    def parse_str_list(cls, v):
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    def active_exchanges(self) -> List[str]:
        """Return the effective list of exchanges to trade on."""
        if self.exchanges:
            return self.exchanges
        return [self.exchange]

    def credentials_for(self, exchange_id: str) -> dict:
        """Return API credentials for the given exchange id."""
        if exchange_id == "bitget":
            return {
                "apiKey": self.bitget_api_key or self.exchange_api_key,
                "secret": self.bitget_api_secret or self.exchange_api_secret,
                "password": self.bitget_passphrase,
            }
        if exchange_id == "toobit":
            return {
                "apiKey": self.toobit_api_key or self.exchange_api_key,
                "secret": self.toobit_api_secret or self.exchange_api_secret,
            }
        if exchange_id == "hyperliquid":
            return {
                "walletAddress": self.exchange_wallet_address,
                "privateKey": self.exchange_api_secret,
            }
        return {
            "apiKey": self.exchange_api_key,
            "secret": self.exchange_api_secret,
        }


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
