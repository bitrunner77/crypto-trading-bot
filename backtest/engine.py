"""
backtest/engine.py — Historical backtesting engine.
Simulates strategy execution on historical OHLCV data and computes performance metrics.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from analysis.indicators import compute_indicators
from strategies.base import BaseStrategy

logger = logging.getLogger("cryptobot.backtest")


@dataclass
class BacktestTrade:
    timestamp: str
    symbol: str
    side: str
    price: float
    amount: float
    cost: float
    fee: float
    pnl: Optional[float] = None
    reasoning: str = ""


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
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"BACKTEST RESULTS — {self.symbol} | {self.strategy} | {self.timeframe}\n"
            f"{'='*60}\n"
            f"Period:         {self.start_date} → {self.end_date}\n"
            f"Initial:        ${self.initial_balance:,.2f}\n"
            f"Final:          ${self.final_balance:,.2f}\n"
            f"Return:         {self.total_return_pct:+.2f}%\n"
            f"Max Drawdown:   {self.max_drawdown_pct:.2f}%\n"
            f"Sharpe Ratio:   {self.sharpe_ratio:.3f}\n"
            f"Win Rate:       {self.win_rate:.1%}\n"
            f"Total Trades:   {self.total_trades}\n"
            f"  Wins:         {self.winning_trades}\n"
            f"  Losses:       {self.losing_trades}\n"
            f"Avg Win:        {self.avg_win_pct:+.2f}%\n"
            f"Avg Loss:       {self.avg_loss_pct:+.2f}%\n"
            f"Profit Factor:  {self.profit_factor:.2f}\n"
            f"{'='*60}\n"
        )


class BacktestEngine:
    """
    Backtests a strategy against historical OHLCV data.
    Uses a rolling window of candles so indicators have enough history.
    """

    WINDOW = 200       # Candles of history to use for indicators
    FEE_PCT = 0.001    # 0.1% trading fee per side

    def __init__(self, config):
        self._config = config

    def run(
        self,
        strategy: BaseStrategy,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        initial_balance: float = 10000.0,
    ) -> BacktestResult:
        """
        Run a full backtest. `df` must have OHLCV columns sorted by timestamp ASC.
        """
        logger.info(f"Starting backtest: {symbol} | {strategy.name} | {len(df)} candles")

        if len(df) < self.WINDOW + 10:
            raise ValueError(f"Not enough data for backtest (need {self.WINDOW + 10}, got {len(df)})")

        cash = initial_balance
        position_amount = 0.0
        position_entry = 0.0
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = [initial_balance]
        peak_equity = initial_balance

        start_idx = self.WINDOW

        for i in range(start_idx, len(df)):
            window_df = df.iloc[i - self.WINDOW : i + 1].copy()
            current_row = df.iloc[i]
            price = float(current_row["close"])
            timestamp = str(current_row["timestamp"])

            indicators = compute_indicators(window_df)

            open_positions = []
            if position_amount > 0:
                open_positions = [{
                    "symbol": symbol,
                    "side": "buy",
                    "entry_price": position_entry,
                    "amount": position_amount,
                }]

            portfolio_value = cash + position_amount * price

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
                if signal.action == "buy" and position_amount == 0 and cash > 10:
                    spend = cash * signal.size_pct
                    fee = spend * self.FEE_PCT
                    amount = (spend - fee) / price
                    cash -= spend
                    position_amount = amount
                    position_entry = price
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="buy",
                        price=price, amount=amount, cost=spend, fee=fee,
                        reasoning=signal.reasoning,
                    ))
                    logger.debug(f"BT BUY  {amount:.6f} {symbol} @ ${price:,.2f}")

                elif signal.action == "sell" and position_amount > 0:
                    sell_amount = position_amount * signal.size_pct
                    proceeds = sell_amount * price
                    fee = proceeds * self.FEE_PCT
                    net_proceeds = proceeds - fee
                    pnl = net_proceeds - (sell_amount * position_entry)
                    cash += net_proceeds
                    position_amount -= sell_amount
                    if position_amount < 1e-8:
                        position_amount = 0.0
                    trades.append(BacktestTrade(
                        timestamp=timestamp, symbol=symbol, side="sell",
                        price=price, amount=sell_amount, cost=proceeds, fee=fee,
                        pnl=pnl, reasoning=signal.reasoning,
                    ))
                    logger.debug(f"BT SELL {sell_amount:.6f} {symbol} @ ${price:,.2f} (P&L: ${pnl:+.2f})")

            # Update equity curve
            current_equity = cash + position_amount * price
            equity_curve.append(current_equity)
            peak_equity = max(peak_equity, current_equity)

        # Close any remaining position at last price
        final_price = float(df.iloc[-1]["close"])
        final_equity = cash + position_amount * final_price

        # ── Compute metrics ────────────────────────────────────────────────────
        total_return_pct = (final_equity - initial_balance) / initial_balance * 100

        # Max drawdown
        max_dd = 0.0
        peak = equity_curve[0]
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        # Sharpe ratio (annualised, assuming daily returns for simplicity)
        if len(equity_curve) > 2:
            returns = [(equity_curve[i] - equity_curve[i-1]) / equity_curve[i-1]
                       for i in range(1, len(equity_curve)) if equity_curve[i-1] > 0]
            if returns:
                mean_r = sum(returns) / len(returns)
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns))
                sharpe = (mean_r / std_r * math.sqrt(365)) if std_r > 0 else 0.0
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Win/loss stats
        sell_trades = [t for t in trades if t.side == "sell" and t.pnl is not None]
        wins = [t for t in sell_trades if t.pnl > 0]
        losses = [t for t in sell_trades if t.pnl <= 0]
        win_rate = len(wins) / len(sell_trades) if sell_trades else 0.0

        avg_win_pct = (
            sum(t.pnl / (t.cost - t.fee) for t in wins) / len(wins) * 100
            if wins else 0.0
        )
        avg_loss_pct = (
            sum(t.pnl / (t.cost - t.fee) for t in losses) / len(losses) * 100
            if losses else 0.0
        )

        gross_profit = sum(t.pnl for t in wins)
        gross_loss = abs(sum(t.pnl for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        result = BacktestResult(
            symbol=symbol,
            strategy=strategy.name,
            timeframe=timeframe,
            start_date=str(df.iloc[start_idx]["timestamp"]),
            end_date=str(df.iloc[-1]["timestamp"]),
            initial_balance=initial_balance,
            final_balance=final_equity,
            total_return_pct=total_return_pct,
            max_drawdown_pct=max_dd * 100,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            profit_factor=profit_factor,
            trades=trades,
            equity_curve=equity_curve,
        )
        logger.info(result.summary())
        return result
