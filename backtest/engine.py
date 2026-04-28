"""
backtest/engine.py — Historical backtesting engine.
Simulates strategy execution on historical OHLCV data with:
  - Regime filtering (bull=longs only, bear=shorts only)
  - Configurable leverage with per-position liquidation checks

Leverage model (futures/perpetuals):
  Long:  spend `margin` cash, control `leverage * margin` exposure.
         Liquidation when price drops to entry * (1 - 1/leverage).
  Short: borrow & sell `leverage * margin` of coins, receive proceeds into cash.
         Liquidation when price rises to entry * (1 + 1/leverage).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from analysis.indicators import compute_indicators
from strategies.base import BaseStrategy

logger = logging.getLogger("cryptobot.backtest")


@dataclass
class BacktestTrade:
    timestamp: str
    symbol: str
    side: str          # "buy", "sell", "short", "cover", "liquidated_long", "liquidated_short"
    price: float
    amount: float
    cost: float
    fee: float
    pnl: Optional[float] = None
    reasoning: str = ""
    regime: str = "unknown"


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    timeframe: str
    start_date: str
    end_date: str
    initial_balance: float
    final_balance: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    regime_filter: bool = False
    leverage: float = 1.0
    liquidations: int = 0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    def summary(self) -> str:
        mode = "REGIME-FILTERED" if self.regime_filter else "NO REGIME FILTER"
        lev  = f"{self.leverage:.0f}x leverage"
        return (
            f"\n{'='*60}\n"
            f"BACKTEST RESULTS -- {self.symbol} | {self.strategy} | {self.timeframe}\n"
            f"                    {mode} | {lev}\n"
            f"{'='*60}\n"
            f"Period:         {self.start_date} -> {self.end_date}\n"
            f"Initial:        ${self.initial_balance:,.2f}\n"
            f"Final:          ${self.final_balance:,.2f}\n"
            f"Return:         {self.total_return_pct:+.2f}%\n"
            f"Max Drawdown:   {self.max_drawdown_pct:.2f}%\n"
            f"Sharpe Ratio:   {self.sharpe_ratio:.3f}\n"
            f"Win Rate:       {self.win_rate:.1%}\n"
            f"Total Trades:   {self.total_trades}\n"
            f"  Wins:         {self.winning_trades}\n"
            f"  Losses:       {self.losing_trades}\n"
            f"  Liquidations: {self.liquidations}\n"
            f"Avg Win:        {self.avg_win_pct:+.2f}%\n"
            f"Avg Loss:       {self.avg_loss_pct:+.2f}%\n"
            f"Profit Factor:  {self.profit_factor:.2f}\n"
            f"{'='*60}\n"
        )


def detect_regime(indicators: dict) -> str:
    """
    EMA50/EMA200 crossover regime classifier.
    Returns 'bull', 'bear', or 'neutral'.
    """
    ema50  = indicators.get("ema_50")
    ema200 = indicators.get("ema_200")
    price  = indicators.get("price")

    if ema50 is None or ema200 is None or price is None:
        return "neutral"
    if ema50 > ema200 and price > ema200:
        return "bull"
    if ema50 < ema200 and price < ema200:
        return "bear"
    return "neutral"


class BacktestEngine:
    """
    Backtests a strategy against historical OHLCV data.

    Parameters
    ----------
    regime_filter : bool
        Bull -> longs only. Bear -> shorts only. Neutral -> no new positions.
    leverage : float
        Position-size multiplier (e.g. 2.0 = 2x). Liquidation is enforced.
    """

    WINDOW  = 200      # candles of lookback for indicators
    FEE_PCT = 0.001    # 0.1% per side (taker fee)

    def __init__(self, config):
        self._config = config

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        initial_balance: float = 10_000.0,
        regime_filter: bool = False,
        leverage: float = 1.0,
    ) -> BacktestResult:
        if len(df) < self.WINDOW + 10:
            raise ValueError(f"Not enough data (need {self.WINDOW + 10}, got {len(df)})")

        leverage = max(1.0, float(leverage))

        cash = initial_balance

        # Long-position state
        long_amount: float = 0.0   # coins held (leveraged)
        long_entry:  float = 0.0   # entry price
        long_margin: float = 0.0   # actual cash put up as margin (net of entry fee)

        # Short-position state
        # Cash already contains the short-sale proceeds when a short is open.
        short_amount: float = 0.0  # coins owed (leveraged)
        short_entry:  float = 0.0
        short_margin: float = 0.0  # cash at risk (net of entry fee)

        trades:       List[BacktestTrade] = []
        equity_curve: List[float]         = [initial_balance]
        liquidations: int                 = 0

        for i in range(self.WINDOW, len(df)):
            window_df   = df.iloc[i - self.WINDOW : i + 1].copy()
            current_row = df.iloc[i]
            price       = float(current_row["close"])
            timestamp   = str(current_row["timestamp"])

            indicators = compute_indicators(window_df)
            regime     = detect_regime(indicators) if regime_filter else "neutral_off"

            # ── Liquidation checks (before strategy) ──────────────────────────
            if leverage > 1.0:
                # Long liquidation: price dropped to entry * (1 - 1/L)
                if long_amount > 0:
                    liq_long = long_entry * (1.0 - 1.0 / leverage)
                    if price <= liq_long:
                        fee = long_amount * price * self.FEE_PCT
                        pnl = long_amount * (price - long_entry) - fee
                        trades.append(BacktestTrade(
                            timestamp=timestamp, symbol=symbol, side="liquidated_long",
                            price=price, amount=long_amount, cost=long_amount * price,
                            fee=fee, pnl=pnl,
                            reasoning=f"Liquidated long (price ${price:,.2f} <= liq ${liq_long:,.2f})",
                            regime=regime,
                        ))
                        # Margin is wiped out; cash was already reduced at entry
                        long_amount = 0.0
                        long_margin = 0.0
                        long_entry  = 0.0
                        liquidations += 1
                        logger.debug(f"BT LIQUIDATED LONG @ ${price:,.2f}")

                # Short liquidation: price rose to entry * (1 + 1/L)
                if short_amount > 0:
                    liq_short = short_entry * (1.0 + 1.0 / leverage)
                    if price >= liq_short:
                        # Force buy-back at current price
                        cost = short_amount * price
                        fee  = cost * self.FEE_PCT
                        pnl  = short_amount * (short_entry - price) - fee
                        cash -= (cost + fee)
                        trades.append(BacktestTrade(
                            timestamp=timestamp, symbol=symbol, side="liquidated_short",
                            price=price, amount=short_amount, cost=cost,
                            fee=fee, pnl=pnl,
                            reasoning=f"Liquidated short (price ${price:,.2f} >= liq ${liq_short:,.2f})",
                            regime=regime,
                        ))
                        short_amount = 0.0
                        short_margin = 0.0
                        short_entry  = 0.0
                        liquidations += 1
                        logger.debug(f"BT LIQUIDATED SHORT @ ${price:,.2f}")

            # ── Build open-positions list for strategy ─────────────────────────
            open_positions = []
            if long_amount > 0:
                open_positions.append({
                    "symbol": symbol, "side": "buy",
                    "entry_price": long_entry, "amount": long_amount,
                })
            if short_amount > 0:
                open_positions.append({
                    "symbol": symbol, "side": "sell",
                    "entry_price": short_entry, "amount": short_amount,
                })

            # Equity accounts for leveraged long (deduct borrowed portion)
            portfolio_value = (
                cash
                + long_amount * price - (leverage - 1.0) * long_margin
                - short_amount * price
            )

            try:
                signal = strategy.generate_signal(
                    symbol=symbol,
                    df=window_df,
                    indicators=indicators,
                    portfolio_value=portfolio_value,
                    cash_balance=cash,
                    open_positions=open_positions,
                    timeframe=timeframe,
                )
            except Exception as e:
                logger.warning(f"Strategy error at {timestamp}: {e}")
                signal = None

            if signal and signal.action != "hold":
                action = signal.action

                # ── Regime-forced exits ────────────────────────────────────────
                if regime_filter:
                    if regime == "bull" and short_amount > 0:
                        cost = short_amount * price
                        fee  = cost * self.FEE_PCT
                        pnl  = short_amount * (short_entry - price) - fee
                        cash -= (cost + fee)
                        trades.append(BacktestTrade(
                            timestamp=timestamp, symbol=symbol, side="cover",
                            price=price, amount=short_amount, cost=cost, fee=fee,
                            pnl=pnl, reasoning="Regime -> bull: cover short", regime=regime,
                        ))
                        short_amount = 0.0; short_margin = 0.0; short_entry = 0.0

                    elif regime == "bear" and long_amount > 0:
                        proceeds = long_amount * price
                        fee      = proceeds * self.FEE_PCT
                        repay    = (leverage - 1.0) * long_margin
                        net      = proceeds - fee - repay
                        cash    += net
                        pnl      = long_amount * (price - long_entry) - fee
                        trades.append(BacktestTrade(
                            timestamp=timestamp, symbol=symbol, side="sell",
                            price=price, amount=long_amount, cost=proceeds, fee=fee,
                            pnl=pnl, reasoning="Regime -> bear: close long", regime=regime,
                        ))
                        long_amount = 0.0; long_margin = 0.0; long_entry = 0.0

                # ── LONG logic ─────────────────────────────────────────────────
                allow_long = (not regime_filter) or regime in ("bull", "neutral_off")

                if allow_long and action == "buy" and long_amount == 0 and cash > 10:
                    spend      = cash * signal.size_pct
                    fee        = spend * self.FEE_PCT
                    net_margin = spend - fee
                    amount     = leverage * net_margin / price
                    cash      -= spend           # lock margin
                    long_amount = amount
                    long_entry  = price
                    long_margin = net_margin
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="buy",
                        price=price, amount=amount, cost=spend, fee=fee,
                        reasoning=f"{signal.reasoning} [{leverage:.0f}x]", regime=regime,
                    ))
                    logger.debug(f"BT BUY {leverage:.0f}x  {amount:.6f} @ ${price:,.2f}")

                elif allow_long and action == "sell" and long_amount > 0:
                    sell_amt    = long_amount * signal.size_pct
                    proportion  = sell_amt / long_amount
                    proceeds    = sell_amt * price
                    fee         = proceeds * self.FEE_PCT
                    repay       = (leverage - 1.0) * long_margin * proportion
                    net         = proceeds - fee - repay
                    pnl         = sell_amt * (price - long_entry) - fee
                    cash       += net
                    long_margin -= long_margin * proportion
                    long_amount -= sell_amt
                    if long_amount < 1e-8:
                        long_amount = 0.0; long_margin = 0.0
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="sell",
                        price=price, amount=sell_amt, cost=proceeds, fee=fee,
                        pnl=pnl, reasoning=signal.reasoning, regime=regime,
                    ))
                    logger.debug(f"BT SELL  {sell_amt:.6f} @ ${price:,.2f} P&L=${pnl:+.2f}")

                # ── SHORT logic ────────────────────────────────────────────────
                allow_short = (not regime_filter) or regime in ("bear", "neutral_off")

                if allow_short and action == "sell" and short_amount == 0 and long_amount == 0 and cash > 10:
                    spend        = cash * signal.size_pct
                    fee          = spend * self.FEE_PCT
                    net_margin   = spend - fee
                    amount       = leverage * net_margin / price
                    # Receive leveraged short-sale proceeds into cash
                    cash        += leverage * net_margin
                    short_amount = amount
                    short_entry  = price
                    short_margin = net_margin
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="short",
                        price=price, amount=amount, cost=spend, fee=fee,
                        reasoning=f"{signal.reasoning} [{leverage:.0f}x]", regime=regime,
                    ))
                    logger.debug(f"BT SHORT {leverage:.0f}x  {amount:.6f} @ ${price:,.2f}")

                elif allow_short and action == "buy" and short_amount > 0:
                    cover_amt    = short_amount * signal.size_pct
                    proportion   = cover_amt / short_amount
                    cost         = cover_amt * price
                    fee          = cost * self.FEE_PCT
                    pnl          = cover_amt * (short_entry - price) - fee
                    cash        -= (cost + fee)
                    short_margin -= short_margin * proportion
                    short_amount -= cover_amt
                    if short_amount < 1e-8:
                        short_amount = 0.0; short_margin = 0.0
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="cover",
                        price=price, amount=cover_amt, cost=cost, fee=fee,
                        pnl=pnl, reasoning=signal.reasoning, regime=regime,
                    ))
                    logger.debug(f"BT COVER {cover_amt:.6f} @ ${price:,.2f} P&L=${pnl:+.2f}")

            # ── Equity snapshot ────────────────────────────────────────────────
            current_equity = (
                cash
                + long_amount * price - (leverage - 1.0) * long_margin
                - short_amount * price
            )
            equity_curve.append(max(0.0, current_equity))

        # Close remaining positions at final price
        final_price  = float(df.iloc[-1]["close"])
        final_equity = (
            cash
            + long_amount * final_price - (leverage - 1.0) * long_margin
            - short_amount * final_price
        )
        final_equity = max(0.0, final_equity)

        # ── Performance metrics ────────────────────────────────────────────────
        total_return_pct = (final_equity - initial_balance) / initial_balance * 100

        max_dd = 0.0
        peak   = equity_curve[0]
        for v in equity_curve:
            peak   = max(peak, v)
            if peak > 0:
                max_dd = max(max_dd, (peak - v) / peak)

        if len(equity_curve) > 2:
            returns = [
                (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
                for i in range(1, len(equity_curve))
                if equity_curve[i - 1] > 0
            ]
            if returns:
                mean_r = sum(returns) / len(returns)
                std_r  = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns))
                sharpe = mean_r / std_r * math.sqrt(365) if std_r > 0 else 0.0
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        closed = [t for t in trades if t.pnl is not None]
        wins   = [t for t in closed if t.pnl > 0]
        losses = [t for t in closed if t.pnl <= 0]
        win_rate = len(wins) / len(closed) if closed else 0.0

        avg_win_pct  = sum(t.pnl / t.cost for t in wins)  / len(wins)  * 100 if wins   else 0.0
        avg_loss_pct = sum(t.pnl / t.cost for t in losses) / len(losses) * 100 if losses else 0.0

        gross_profit  = sum(t.pnl for t in wins)
        gross_loss    = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        result = BacktestResult(
            symbol=symbol, strategy=strategy.name, timeframe=timeframe,
            start_date=str(df.iloc[self.WINDOW]["timestamp"]),
            end_date=str(df.iloc[-1]["timestamp"]),
            initial_balance=initial_balance, final_balance=final_equity,
            total_return_pct=total_return_pct, max_drawdown_pct=max_dd * 100,
            sharpe_ratio=sharpe, win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=len(wins), losing_trades=len(losses),
            avg_win_pct=avg_win_pct, avg_loss_pct=avg_loss_pct,
            profit_factor=profit_factor,
            regime_filter=regime_filter, leverage=leverage,
            liquidations=liquidations, trades=trades, equity_curve=equity_curve,
        )
        logger.info(result.summary())
        return result
