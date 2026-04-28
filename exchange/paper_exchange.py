"""
exchange/paper_exchange.py — Simulated paper-trading exchange for leveraged perpetuals.
Tracks virtual margin, simulates liquidations, longs and shorts.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import ccxt.async_support as ccxt

from data.database import get_cached_ohlcv
from exchange.base import ExchangeClient
from utils.helpers import retry_async

logger = logging.getLogger("cryptobot.paper")

MAINTENANCE_MARGIN_RATE = 0.005   # 0.5% — standard for most crypto perps
TAKER_FEE = 0.0005                # 0.05% taker fee (e.g. Binance futures)


class PaperExchange(ExchangeClient):
    """
    Paper trading exchange simulating leveraged perpetual futures.
    - Supports long AND short positions
    - Tracks unrealised P&L, liquidation prices
    - Simulates slippage and fees
    - Halts on balance reaching $0
    """

    def __init__(self, config, exchange_id: str = ""):
        self._config = config
        self._exchange_id = exchange_id or config.exchange
        self._slippage = config.paper_slippage_pct

        # Cash balance (USDT)
        self._balance: float = config.paper_initial_balance

        # Open positions: symbol → {direction, entry_price, notional, margin, leverage, liq_price}
        self._positions: Dict[str, Dict] = {}
        self._orders: List[Dict] = []

        # Read-only market data (public endpoints, no credentials needed)
        exchange_cls = getattr(ccxt, self._exchange_id, ccxt.bitget)
        opts: dict = {"enableRateLimit": True, "verify": False}
        if self._exchange_id in ("bitget", "toobit"):
            opts["options"] = {"defaultType": "swap"}
        elif self._exchange_id == "bybit":
            opts["options"] = {"defaultType": "linear"}
        elif self._exchange_id != "hyperliquid":
            opts["options"] = {"defaultType": "swap"}
        self._market = exchange_cls(opts)
        self._quote_currency = "USDC" if self._exchange_id == "hyperliquid" else "USDT"

        logger.info(
            f"[bold yellow]PAPER TRADING MODE (PERPS) [{self._exchange_id.upper()}][/bold yellow] — "
            f"Starting balance: ${config.paper_initial_balance:,.2f} USDT | "
            f"Max leverage: {config.max_leverage}x"
        )

    # ── Market data ────────────────────────────────────────────────────────────

    def _resolve(self, symbol: str) -> str:
        """Return the symbol to use for public market data queries.
        Hyperliquid and swap exchanges serve perp symbols directly."""
        if self._exchange_id in ("hyperliquid", "bitget", "toobit"):
            return symbol
        return _to_spot(symbol)

    def _cached_ticker(self, symbol: str) -> Optional[Dict]:
        """Build a minimal ticker from the most-recent cached OHLCV candle."""
        rows = get_cached_ohlcv(self._exchange_id, symbol, self._config.timeframe, limit=1)
        if rows:
            last_close = rows[0][4]  # close column
            return {"symbol": symbol, "last": last_close, "close": last_close,
                    "bid": last_close * 0.9999, "ask": last_close * 1.0001,
                    "high": rows[0][2], "low": rows[0][3], "volume": rows[0][5]}
        return None

    @retry_async(attempts=3)
    async def fetch_ohlcv(self, symbol: str, timeframe: str,
                          since: Optional[int] = None, limit: int = 200) -> List[List]:
        q = self._resolve(symbol)
        try:
            return await self._market.fetch_ohlcv(q, timeframe, since=since, limit=limit)
        except Exception:
            try:
                return await self._market.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
            except Exception:
                cached = get_cached_ohlcv(self._exchange_id, symbol, timeframe, since, limit)
                if cached:
                    return cached
                raise

    @retry_async(attempts=3)
    async def fetch_ticker(self, symbol: str) -> Dict:
        q = self._resolve(symbol)
        try:
            return await self._market.fetch_ticker(q)
        except Exception:
            try:
                return await self._market.fetch_ticker(symbol)
            except Exception:
                fallback = self._cached_ticker(symbol)
                if fallback:
                    return fallback
                raise

    @retry_async(attempts=3)
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Dict]:
        resolved = [self._resolve(s) for s in symbols]
        try:
            result = await self._market.fetch_tickers(resolved)
            return {orig: result.get(self._resolve(orig), result.get(orig, {}))
                    for orig in symbols}
        except Exception:
            result = {}
            for s in symbols:
                try:
                    result[s] = await self.fetch_ticker(s)
                except Exception:
                    fallback = self._cached_ticker(s)
                    if fallback:
                        result[s] = fallback
            return result

    @retry_async(attempts=3)
    async def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict:
        return await self._market.fetch_order_book(self._resolve(symbol), limit)

    # ── Virtual account ────────────────────────────────────────────────────────

    async def fetch_balance(self) -> Dict:
        """Return balance dict with unrealised P&L folded in."""
        q = self._quote_currency
        return {
            q: {"free": self._balance, "used": self._used_margin(), "total": self.total_equity()},
            "info": {},
        }

    async def create_order(
        self,
        symbol: str,
        side: str,           # "long" | "short" | "close"
        amount: float,       # margin amount in USDT (NOT contracts)
        price: Optional[float] = None,
        order_type: str = "market",
        leverage: int = 1,
    ) -> Dict:
        ticker = await self.fetch_ticker(symbol)
        entry_price = ticker["last"]
        if side == "long":
            entry_price *= (1 + self._slippage)
        elif side == "short":
            entry_price *= (1 - self._slippage)

        order_id = str(uuid.uuid4())[:8]
        fee = amount * leverage * TAKER_FEE

        # ── CLOSE existing position ────────────────────────────────────────────
        if side == "close":
            return await self._close_position(symbol, entry_price, order_id)

        # ── OPEN new position ──────────────────────────────────────────────────
        if self._balance < amount + fee:
            raise ValueError(
                f"Insufficient balance (${self._balance:.2f}) "
                f"for margin ${amount:.2f} + fee ${fee:.2f}"
            )

        # Deduct margin + fee from balance
        self._balance -= (amount + fee)

        notional = amount * leverage
        liq_price = self._calc_liquidation(entry_price, side, leverage)

        self._positions[symbol] = {
            "direction": side,
            "entry_price": entry_price,
            "notional": notional,
            "margin": amount,
            "leverage": leverage,
            "liq_price": liq_price,
            "opened_at": datetime.utcnow().isoformat(),
        }

        logger.info(
            f"[PAPER] OPEN {side.upper()} {symbol} | "
            f"Entry: ${entry_price:,.2f} | Margin: ${amount:.2f} | "
            f"Leverage: {leverage}x | Notional: ${notional:,.2f} | "
            f"Liq: ${liq_price:,.2f} | Fee: ${fee:.2f}"
        )

        order = {
            "id": order_id,
            "symbol": symbol,
            "side": side,
            "amount": amount,
            "price": entry_price,
            "notional": notional,
            "leverage": leverage,
            "fee": {"cost": fee, "currency": "USDT"},
            "status": "closed",
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
        }
        self._orders.append(order)
        return order

    async def _close_position(self, symbol: str, close_price: float, order_id: str) -> Dict:
        pos = self._positions.get(symbol)
        if not pos:
            logger.warning(f"[PAPER] No open position for {symbol} to close")
            return {"id": order_id, "symbol": symbol, "side": "close", "status": "skipped"}

        direction = pos["direction"]
        entry = pos["entry_price"]
        notional = pos["notional"]
        margin = pos["margin"]

        # P&L calculation
        price_change_pct = (close_price - entry) / entry
        if direction == "short":
            price_change_pct *= -1
        pnl = notional * price_change_pct
        fee = notional * TAKER_FEE

        returned = margin + pnl - fee
        self._balance += max(returned, 0.0)  # Can't recover more than $0 on wipeout

        logger.info(
            f"[PAPER] CLOSE {direction.upper()} {symbol} | "
            f"Exit: ${close_price:,.2f} | P&L: ${pnl:+,.2f} | "
            f"Fee: ${fee:.2f} | Returned: ${returned:,.2f} | "
            f"New Balance: ${self._balance:,.2f}"
        )

        del self._positions[symbol]

        order = {
            "id": order_id,
            "symbol": symbol,
            "side": "close",
            "entry_price": entry,
            "close_price": close_price,
            "pnl": pnl,
            "fee": fee,
            "returned": returned,
            "status": "closed",
            "timestamp": int(datetime.utcnow().timestamp() * 1000),
        }
        self._orders.append(order)
        return order

    def check_liquidations(self, current_prices: Dict[str, float]) -> List[str]:
        """
        Check if any open positions hit their liquidation price.
        Returns list of liquidated symbols.
        """
        liquidated = []
        for symbol, pos in list(self._positions.items()):
            price = current_prices.get(symbol) or current_prices.get(_to_spot(symbol))
            if not price:
                continue
            liq = pos["liq_price"]
            direction = pos["direction"]
            if (direction == "long" and price <= liq) or (direction == "short" and price >= liq):
                logger.error(
                    f"[PAPER] ☠️  LIQUIDATION: {symbol} {direction.upper()} | "
                    f"Price: ${price:,.2f} crossed liq: ${liq:,.2f}"
                )
                # Lose entire margin (already deducted on open)
                del self._positions[symbol]
                liquidated.append(symbol)
        return liquidated

    def get_unrealised_pnl(self, symbol: str, current_price: float) -> float:
        pos = self._positions.get(symbol)
        if not pos:
            return 0.0
        entry = pos["entry_price"]
        notional = pos["notional"]
        direction = pos["direction"]
        change = (current_price - entry) / entry
        if direction == "short":
            change *= -1
        return notional * change

    def total_equity(self) -> float:
        """Cash balance + sum of unrealised P&L (requires price updates)."""
        return self._balance  # Conservative: only count cash; caller adds unrealised

    def get_positions(self) -> Dict[str, Dict]:
        return dict(self._positions)

    def get_cash_balance(self) -> float:
        return self._balance

    def _used_margin(self) -> float:
        return sum(p["margin"] for p in self._positions.values())

    @staticmethod
    def _calc_liquidation(entry: float, direction: str, leverage: int) -> float:
        liq_pct = (1.0 / leverage) - MAINTENANCE_MARGIN_RATE
        if direction == "long":
            return entry * (1 - liq_pct)
        return entry * (1 + liq_pct)

    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        return {"id": order_id, "status": "canceled"}

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return []

    async def close(self) -> None:
        await self._market.close()


def _to_spot(symbol: str) -> str:
    """Convert a perp symbol like BTC/USDT:USDT → BTC/USDT for public data endpoints."""
    if ":" in symbol:
        return symbol.split(":")[0]
    return symbol
