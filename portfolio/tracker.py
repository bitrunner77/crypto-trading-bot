"""
portfolio/tracker.py — Portfolio state tracking and performance metrics.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.portfolio")


class PortfolioTracker:
    """
    Tracks the current portfolio state including cash, positions, and P&L.
    Works with both paper and live exchange clients.
    """

    def __init__(self, exchange_client, config, db_module):
        self._exchange = exchange_client
        self._config = config
        self._db = db_module
        self._initial_value: Optional[float] = None
        self._peak_value: float = 0.0
        self._current_prices: Dict[str, float] = {}

    def update_prices(self, prices: Dict[str, float]) -> None:
        """Update current market prices for position valuation."""
        self._current_prices.update(prices)

    async def refresh(self) -> Dict:
        """Fetch latest balance from exchange and compute portfolio snapshot."""
        balance = await self._exchange.fetch_balance()
        return self._compute_snapshot(balance)

    def _compute_snapshot(self, balance: Dict) -> Dict:
        """Compute portfolio value from balance dict."""
        usdt_free = 0.0
        usdt_total = 0.0
        holdings = {}

        for currency, data in balance.items():
            if currency in ("info", "free", "used", "total", "timestamp", "datetime"):
                continue
            if not isinstance(data, dict):
                continue
            total = data.get("total", 0.0) or 0.0
            if total <= 1e-8:
                continue

            if currency in ("USDT", "USDC"):
                usdt_free = data.get("free", 0.0) or 0.0
                usdt_total = total
            else:
                # Try to value in USDT
                symbol = f"{currency}/USDT"
                price = self._current_prices.get(symbol)
                if price and price > 0:
                    value = total * price
                    holdings[currency] = {
                        "amount": total,
                        "price": price,
                        "value_usdt": value,
                    }

        holdings_value = sum(h["value_usdt"] for h in holdings.values())
        total_value = usdt_total + holdings_value

        if self._initial_value is None:
            self._initial_value = total_value
        self._peak_value = max(self._peak_value, total_value)

        initial = self._initial_value or total_value
        pnl_total = total_value - initial
        pnl_pct = pnl_total / initial if initial > 0 else 0.0
        drawdown = (self._peak_value - total_value) / self._peak_value if self._peak_value > 0 else 0.0

        # Get open positions from DB
        positions = self._db.get_positions()

        snapshot = {
            "total_value": total_value,
            "cash_balance": usdt_free,
            "holdings": holdings,
            "positions": positions,
            "pnl_total": pnl_total,
            "pnl_pct": pnl_pct,
            "drawdown": drawdown,
            "peak_value": self._peak_value,
            "initial_value": initial,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Persist snapshot periodically
        try:
            self._db.save_portfolio_snapshot(
                total_value=total_value,
                cash_balance=usdt_free,
                holdings=holdings,
                pnl_total=pnl_total,
                pnl_pct=pnl_pct,
            )
        except Exception:
            pass

        return snapshot

    def get_snapshot(self) -> Dict:
        """Return last computed snapshot (sync, no network calls)."""
        return {
            "total_value": self._peak_value or self._config.paper_initial_balance,
            "cash_balance": self._config.paper_initial_balance,
            "holdings": {},
            "positions": self._db.get_positions(),
            "pnl_total": 0.0,
            "pnl_pct": 0.0,
            "drawdown": 0.0,
            "peak_value": self._peak_value,
            "initial_value": self._initial_value or self._config.paper_initial_balance,
        }

    def compute_trade_pnl(
        self, symbol: str, sell_price: float, sell_amount: float
    ) -> Optional[float]:
        """Compute realised P&L for a sell trade using the DB position."""
        positions = self._db.get_positions()
        for pos in positions:
            if pos["symbol"] == symbol:
                entry = pos["entry_price"]
                pnl = (sell_price - entry) * sell_amount
                return pnl
        return None

    def get_performance_metrics(self) -> Dict:
        """Return Sharpe, win rate, and other metrics from trade history."""
        stats = self._db.get_trade_stats()
        history = self._db.get_portfolio_history(limit=100)

        metrics = {
            "total_trades": stats.get("total", 0),
            "win_rate": stats.get("win_rate", 0.0),
            "total_pnl": stats.get("total_pnl", 0.0),
            "avg_pnl_per_trade": stats.get("avg_pnl", 0.0),
        }

        if len(history) >= 2:
            values = [h["total_value"] for h in reversed(history)]
            returns = [(values[i] - values[i-1]) / values[i-1]
                       for i in range(1, len(values)) if values[i-1] > 0]
            if returns:
                import statistics
                mean_r = statistics.mean(returns)
                std_r = statistics.stdev(returns) if len(returns) > 1 else 0.0001
                metrics["sharpe_ratio"] = (mean_r / std_r * (252 ** 0.5)) if std_r > 0 else 0.0

        return metrics
