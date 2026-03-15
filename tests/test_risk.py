"""
tests/test_risk.py — Unit tests for the risk management module (perps rules).
Rules: max 20x leverage, max 50% of budget, $0 = game over.
"""
import pytest
from unittest.mock import MagicMock


def make_config(**overrides):
    cfg = MagicMock()
    cfg.max_leverage = 20
    cfg.risk_max_position_pct = 0.50
    cfg.risk_max_drawdown_pct = 0.90
    cfg.risk_stop_loss_pct = 0.03
    cfg.risk_take_profit_pct = 0.06
    cfg.risk_max_daily_loss_pct = 0.50
    cfg.paper_initial_balance = 10000.0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TestRiskManager:
    def setup_method(self):
        from risk.manager import RiskManager
        self.config = make_config()
        self.rm = RiskManager(self.config)

    def test_hold_always_passes(self):
        ok, size, lev, reason = self.rm.validate_trade(
            action="hold", symbol="BTC/USDT:USDT", size_pct=0.0, leverage=1,
            portfolio_value=10000, cash_balance=5000,
            initial_balance=10000, open_positions=[],
        )
        assert ok is True

    def test_long_within_limits(self):
        ok, size, lev, reason = self.rm.validate_trade(
            action="long", symbol="BTC/USDT:USDT", size_pct=0.3, leverage=5,
            portfolio_value=10000, cash_balance=5000,
            initial_balance=10000, open_positions=[],
        )
        assert ok is True
        assert size == pytest.approx(0.3)
        assert lev == 5

    def test_leverage_capped_at_20x(self):
        """Rule: max 20x leverage."""
        ok, size, lev, reason = self.rm.validate_trade(
            action="long", symbol="BTC/USDT:USDT", size_pct=0.1, leverage=50,
            portfolio_value=10000, cash_balance=5000,
            initial_balance=10000, open_positions=[],
        )
        assert ok is True
        assert lev == 20  # capped

    def test_size_capped_at_50pct(self):
        """Rule: max 50% of remaining budget per trade."""
        ok, size, lev, reason = self.rm.validate_trade(
            action="long", symbol="BTC/USDT:USDT", size_pct=0.9, leverage=1,
            portfolio_value=10000, cash_balance=10000,
            initial_balance=10000, open_positions=[],
        )
        assert ok is True
        assert size == pytest.approx(0.5)  # capped at 50%

    def test_bankruptcy_halts_trading(self):
        """Rule: balance hits $0 → you're out."""
        ok, size, lev, reason = self.rm.validate_trade(
            action="long", symbol="BTC/USDT:USDT", size_pct=0.5, leverage=10,
            portfolio_value=0.5, cash_balance=0.5,  # effectively $0
            initial_balance=10000, open_positions=[],
        )
        assert ok is False
        assert self.rm.is_halted
        assert "game over" in self.rm._halt_reason.lower() or "$0" in self.rm._halt_reason

    def test_check_bankruptcy_direct(self):
        assert self.rm.check_bankruptcy(0.0) is True
        assert self.rm.is_halted

    def test_check_bankruptcy_healthy(self):
        from risk.manager import RiskManager
        rm2 = RiskManager(self.config)
        assert rm2.check_bankruptcy(5000.0) is False

    def test_liquidation_price_long(self):
        """Long position liquidates below entry."""
        liq = self.rm.liquidation_price(50000, "long", 10)
        assert liq < 50000
        # At 10x leverage, liq should be ~10% below entry (1/10 = 10%)
        assert liq == pytest.approx(50000 * (1 - 0.0950), rel=0.05)

    def test_liquidation_price_short(self):
        """Short position liquidates above entry."""
        liq = self.rm.liquidation_price(50000, "short", 10)
        assert liq > 50000

    def test_stop_loss_above_liquidation_for_long(self):
        """Stop-loss must be hit BEFORE liquidation."""
        sl = self.rm.compute_stop_loss(50000, "long", 10)
        liq = self.rm.liquidation_price(50000, "long", 10)
        assert sl > liq  # SL triggers before liquidation

    def test_take_profit_2to1_rr(self):
        entry = 50000
        sl = self.rm.compute_stop_loss(entry, "long", 5)
        tp = self.rm.compute_take_profit(entry, "long", sl)
        tp_dist = tp - entry
        sl_dist = entry - sl
        assert tp_dist >= sl_dist * 1.9  # ~2:1 minimum

    def test_pnl_tracking(self):
        self.rm.record_pnl(-500.0)
        self.rm.record_pnl(200.0)
        assert self.rm.get_daily_pnl() == pytest.approx(-300.0)

    def test_round_counter(self):
        assert self.rm.increment_round() == 1
        assert self.rm.increment_round() == 2
        assert self.rm.round_number == 2
