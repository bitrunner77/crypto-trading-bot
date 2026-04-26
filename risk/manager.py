"""
risk/manager.py — Risk management engine for perpetuals trading.
Rules enforced:
  - Max 20x leverage (hard cap)
  - Max 50% of remaining budget per trade
  - Balance reaches $0 → permanent halt (eliminated)
  - Bankruptcy protection at every step
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from utils.helpers import clamp

logger = logging.getLogger("cryptobot.risk")

MIN_BALANCE = 1.0          # Below $1 = effectively $0 → halt
BANKRUPTCY_MSG = "BALANCE REACHED $0 — YOU'RE OUT. Game over."


class RiskManager:
    """
    Centralised risk enforcement layer for leveraged perpetuals.
    All trades must pass through validate_trade() before execution.
    """

    def __init__(self, config):
        self._config = config
        self._daily_pnl: Dict[str, float] = {}
        self._trading_halted = False
        self._halt_reason = ""
        self._round_number = 0

    # ── Main gate ──────────────────────────────────────────────────────────────

    def validate_trade(
        self,
        action: str,
        symbol: str,
        size_pct: float,
        leverage: int,
        portfolio_value: float,
        cash_balance: float,
        initial_balance: float,
        open_positions: List[Dict],
    ) -> Tuple[bool, float, int, str]:
        """
        Validate a proposed leveraged trade.
        Returns: (approved: bool, adjusted_size_pct: float, adjusted_leverage: int, reason: str)
        """
        if action in ("hold", "close"):
            return True, size_pct, leverage, "No validation needed"

        # 1. Bankruptcy check — if balance is essentially $0, halt permanently
        if cash_balance <= MIN_BALANCE or portfolio_value <= MIN_BALANCE:
            self._halt(BANKRUPTCY_MSG)
            return False, 0.0, 1, BANKRUPTCY_MSG

        # 2. Global trading halt
        if self._trading_halted:
            return False, 0.0, 1, f"Trading halted: {self._halt_reason}"

        # 3. Leverage hard cap: max 20x
        if leverage > self._config.max_leverage:
            old_lev = leverage
            leverage = self._config.max_leverage
            logger.warning(f"Leverage capped: {old_lev}x → {leverage}x (max 20x rule)")

        # 4. Position size: max 50% of remaining cash
        max_size_pct = 0.50
        if size_pct > max_size_pct:
            logger.warning(f"Size capped: {size_pct:.0%} → {max_size_pct:.0%} (max 50% rule)")
            size_pct = max_size_pct

        # 5. Minimum cash check
        trade_margin = cash_balance * size_pct
        if trade_margin < 1.0:
            return False, 0.0, 1, f"Trade margin too small (${trade_margin:.2f})"

        # 6. Notional exposure sanity: leverage × margin ≤ 20 × 50% of balance
        notional = trade_margin * leverage
        max_notional = cash_balance * max_size_pct * self._config.max_leverage
        if notional > max_notional:
            # Scale down size to fit
            size_pct = max_notional / (leverage * cash_balance) if leverage * cash_balance > 0 else max_size_pct
            size_pct = clamp(size_pct, 0.01, max_size_pct)
            logger.warning(f"Notional capped: adjusted size to {size_pct:.1%}")

        # 7. Daily loss circuit breaker. If today's realized PnL has dropped
        # below -(risk_max_daily_loss_pct × initial_balance), halt new entries.
        max_daily_loss = self._config.risk_max_daily_loss_pct * max(initial_balance, 0.0)
        if max_daily_loss > 0 and self.get_daily_pnl() <= -max_daily_loss:
            reason = (
                f"Daily loss limit hit: realized P&L "
                f"${self.get_daily_pnl():,.2f} ≤ -${max_daily_loss:,.2f}"
            )
            self._halt(reason)
            return False, 0.0, 1, reason

        return True, size_pct, leverage, "OK"

    # ── Liquidation price calculation ──────────────────────────────────────────

    def liquidation_price(
        self, entry_price: float, direction: str, leverage: int, maintenance_margin: float = 0.005
    ) -> float:
        """
        Approximate liquidation price for a leveraged position.
        direction: 'long' or 'short'
        maintenance_margin: typically 0.5% for crypto perps
        """
        liq_distance_pct = (1.0 / leverage) - maintenance_margin
        if direction == "long":
            return entry_price * (1 - liq_distance_pct)
        return entry_price * (1 + liq_distance_pct)

    def compute_stop_loss(
        self, entry_price: float, direction: str, leverage: int,
        atr: Optional[float] = None
    ) -> float:
        """
        Compute stop-loss price, ensuring it's above liquidation price.
        Stop-loss must be hit BEFORE liquidation.
        """
        liq = self.liquidation_price(entry_price, direction, leverage)

        if atr and atr > 0:
            sl_distance = atr * 1.5
            sl_pct = sl_distance / entry_price
            sl_pct = clamp(sl_pct, 0.005, 0.15)
        else:
            sl_pct = self._config.risk_stop_loss_pct

        if direction == "long":
            sl = entry_price * (1 - sl_pct)
            # Make sure SL is above liquidation (with a 10% buffer)
            sl = max(sl, liq * 1.10)
            return sl
        else:
            sl = entry_price * (1 + sl_pct)
            # Make sure SL is below liquidation
            sl = min(sl, liq * 0.90)
            return sl

    def compute_take_profit(
        self, entry_price: float, direction: str, stop_loss: float, min_rr: float = 2.0
    ) -> float:
        """Compute take-profit with minimum 2:1 reward-to-risk ratio."""
        sl_distance = abs(entry_price - stop_loss)
        tp_distance = max(
            sl_distance * min_rr,
            entry_price * self._config.risk_take_profit_pct
        )
        return entry_price + tp_distance if direction == "long" else entry_price - tp_distance

    # ── P&L + balance tracking ─────────────────────────────────────────────────

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def record_pnl(self, pnl: float) -> None:
        key = self._today_utc()
        self._daily_pnl[key] = self._daily_pnl.get(key, 0.0) + pnl

    def get_daily_pnl(self) -> float:
        return self._daily_pnl.get(self._today_utc(), 0.0)

    def check_bankruptcy(self, balance: float) -> bool:
        """Returns True if the trader is out (balance ≤ $0)."""
        if balance <= MIN_BALANCE:
            self._halt(BANKRUPTCY_MSG)
            return True
        return False

    def increment_round(self) -> int:
        self._round_number += 1
        return self._round_number

    @property
    def round_number(self) -> int:
        return self._round_number

    # ── Position sizing ────────────────────────────────────────────────────────

    def calculate_position_size(
        self,
        cash_balance: float,
        confidence: float,
        leverage: int,
        volatility_pct: Optional[float] = None,
    ) -> float:
        """
        Size a position: up to 50% of cash, scaled by confidence.
        Returns fraction of cash to use as margin.
        """
        max_pct = 0.50  # Hard rule: never more than 50%
        base = confidence * max_pct
        if volatility_pct and volatility_pct > 0:
            # Reduce size in high-volatility environments
            vol_scalar = clamp(0.03 / volatility_pct, 0.2, 1.0)
            base *= vol_scalar
        # Higher leverage → smaller base margin (same notional risk)
        leverage_scalar = clamp(5.0 / max(leverage, 1), 0.1, 1.0)
        base *= leverage_scalar
        return clamp(base, 0.01, max_pct)

    # ── Halt control ──────────────────────────────────────────────────────────

    def _halt(self, reason: str) -> None:
        self._trading_halted = True
        self._halt_reason = reason
        logger.error(f"[bold red]{'='*50}[/bold red]")
        logger.error(f"[bold red]TRADING HALTED: {reason}[/bold red]")
        logger.error(f"[bold red]{'='*50}[/bold red]")

    def resume_trading(self) -> None:
        self._trading_halted = False
        self._halt_reason = ""
        logger.info("Trading resumed.")

    @property
    def is_halted(self) -> bool:
        return self._trading_halted

    # ── Metrics ───────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_drawdown(current_value: float, peak_value: float) -> float:
        if peak_value <= 0:
            return 0.0
        return max(0.0, (peak_value - current_value) / peak_value)

    def get_risk_summary(self, portfolio_value: float, initial_balance: float) -> Dict:
        drawdown = self._calc_drawdown(portfolio_value, initial_balance)
        return {
            "halted": self._trading_halted,
            "halt_reason": self._halt_reason,
            "drawdown_pct": drawdown,
            "max_drawdown_pct": self._config.risk_max_drawdown_pct,
            "daily_pnl": self.get_daily_pnl(),
            "round": self._round_number,
            "bankrupt": portfolio_value <= MIN_BALANCE,
        }
