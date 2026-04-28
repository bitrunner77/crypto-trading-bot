"""
exchange/ccxt_client.py — Live exchange client wrapping CCXT async.
Supports Binance, Coinbase, Kraken, Bybit, BitGet, and any other CCXT exchange.

BitGet perpetuals notes:
  - Requires apiKey + secret + password (passphrase)
  - Symbol format for USDT-margined perps: SOL/USDT:USDT
  - "long" action  -> buy  open  (positionSide=long)
  - "short" action -> sell open  (positionSide=short)
  - "close" action -> sell/buy close with reduceOnly=True
  - Leverage must be set via set_leverage() before placing orders
  - Amount is in contracts; qty = (margin_usdt * leverage) / price
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import ccxt.async_support as ccxt

from exchange.base import ExchangeClient
from utils.helpers import retry_async

logger = logging.getLogger("cryptobot.exchange")

# Exchanges that use hedge-mode (separate long/short positions)
_HEDGE_MODE_EXCHANGES = {"bitget", "bybit", "binance"}


def _fmt_price(price: float, ref_price: float) -> str:
    """Format a price with enough decimal places for micro-price coins."""
    if ref_price < 0.001:
        return f"{price:.8f}"
    if ref_price < 0.1:
        return f"{price:.6f}"
    if ref_price < 10:
        return f"{price:.4f}"
    return f"{price:.2f}"


class CCXTClient(ExchangeClient):
    """Production exchange client via CCXT."""

    def __init__(self, config):
        exchange_cls = getattr(ccxt, config.exchange, None)
        if exchange_cls is None:
            raise ValueError(
                f"Unknown exchange '{config.exchange}'. Check CCXT docs for supported exchanges."
            )

        if config.exchange == "hyperliquid":
            opts: dict = {
                "walletAddress": config.exchange_wallet_address,
                "privateKey": config.exchange_api_secret,
                "enableRateLimit": True,
            }
        elif config.exchange == "bybit":
            opts = {
                "apiKey": config.exchange_api_key,
                "secret": config.exchange_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "linear"},
            }
        elif config.exchange == "bitget":
            opts = {
                "apiKey": config.exchange_api_key,
                "secret": config.exchange_api_secret,
                "password": config.exchange_passphrase,  # BitGet requires passphrase
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},  # USDT-margined perpetual swaps
            }
        else:
            opts = {
                "apiKey": config.exchange_api_key,
                "secret": config.exchange_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }

        self._exchange: ccxt.Exchange = exchange_cls(opts)
        self._config = config
        self._leverage_set: Dict[str, bool] = {}
        logger.info(f"Initialized CCXT client for [bold]{config.exchange}[/bold]")

    async def init_session(self) -> None:
        """
        Replace aiohttp's default async DNS resolver with ThreadedResolver.
        The async resolver fails on Windows Python 3.8+ (ProactorEventLoop DNS bug).
        ThreadedResolver calls socket.getaddrinfo() in a thread pool — always works.
        """
        import aiohttp
        connector = aiohttp.TCPConnector(
            resolver=aiohttp.ThreadedResolver(),
            limit=50,
            force_close=False,
            enable_cleanup_closed=True,
        )
        self._exchange.session = aiohttp.ClientSession(connector=connector)

    # ── Market data ──────────────────────────────────────────────────────────────

    @retry_async(attempts=3)
    async def fetch_ohlcv(
        self, symbol: str, timeframe: str,
        since: Optional[int] = None, limit: int = 200,
    ) -> List[List]:
        return await self._exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    @retry_async(attempts=3)
    async def fetch_ticker(self, symbol: str) -> Dict:
        return await self._exchange.fetch_ticker(symbol)

    @retry_async(attempts=3)
    async def fetch_tickers(self, symbols: List[str]) -> Dict[str, Dict]:
        return await self._exchange.fetch_tickers(symbols)

    @retry_async(attempts=3)
    async def fetch_order_book(self, symbol: str, limit: int = 10) -> Dict:
        return await self._exchange.fetch_order_book(symbol, limit)

    @retry_async(attempts=3)
    async def fetch_balance(self) -> Dict:
        return await self._exchange.fetch_balance()

    # ── Leverage helper ──────────────────────────────────────────────────────────

    async def _ensure_leverage(self, symbol: str, leverage: int) -> None:
        """Set leverage once per symbol per session."""
        if self._leverage_set.get(symbol):
            return
        try:
            await self._exchange.set_leverage(leverage, symbol)
            logger.info(f"Leverage set to {leverage}x for {symbol}")
        except Exception as exc:
            logger.warning(f"Could not set leverage for {symbol}: {exc}")
        self._leverage_set[symbol] = True

    # ── Order placement ──────────────────────────────────────────────────────────

    @retry_async(attempts=3)
    async def create_order(
        self,
        symbol: str,
        side: str,           # "long" | "short" | "close" | "buy" | "sell"
        amount: float,       # USDT margin to deploy
        price: Optional[float] = None,
        leverage: int = 1,
        order_type: str = "market",
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Dict:
        """
        Translate high-level actions into CCXT perpetuals orders.

        For BitGet (and other hedge-mode exchanges):
          long  -> market buy  open  (positionSide: long)
          short -> market sell open  (positionSide: short)
          close -> market sell/buy reduceOnly (closes whichever side is open)

        stop_loss / take_profit are attached to the order on BitGet via
        stopLossPrice / takeProfitPrice params (preset TP/SL on position open).
        """
        exchange_id = self._config.exchange

        # ── BitGet (and hedge-mode exchanges) ────────────────────────────────────
        if exchange_id in _HEDGE_MODE_EXCHANGES:
            await self._ensure_leverage(symbol, leverage)

            # Convert USDT margin to contract quantity
            qty = (amount * leverage) / price if price and price > 0 else amount

            if side == "long":
                logger.info(
                    f"[LIVE] LONG {symbol}: {qty:.4f} contracts @ ~${price:,.2f} ({leverage}x)"
                    + (f" | SL=${stop_loss}" if stop_loss else "")
                    + (f" TP=${take_profit}" if take_profit else "")
                )
                params: Dict = {"positionSide": "long"}
                if stop_loss:
                    params["stopLossPrice"] = _fmt_price(stop_loss, price)
                if take_profit:
                    params["takeProfitPrice"] = _fmt_price(take_profit, price)
                return await self._exchange.create_market_buy_order(symbol, qty, params=params)

            elif side == "short":
                logger.info(
                    f"[LIVE] SHORT {symbol}: {qty:.4f} contracts @ ~${price:,.2f} ({leverage}x)"
                    + (f" | SL=${stop_loss}" if stop_loss else "")
                    + (f" TP=${take_profit}" if take_profit else "")
                )
                params = {"positionSide": "short"}
                if stop_loss:
                    params["stopLossPrice"] = _fmt_price(stop_loss, price)
                if take_profit:
                    params["takeProfitPrice"] = _fmt_price(take_profit, price)
                return await self._exchange.create_market_sell_order(symbol, qty, params=params)

            elif side == "close":
                # Fetch open positions to determine which side to close
                try:
                    positions = await self._exchange.fetch_positions([symbol])
                    for pos in positions:
                        pos_size = float(pos.get("contracts") or pos.get("size") or 0)
                        pos_side = (pos.get("side") or "").lower()
                        if abs(pos_size) < 1e-8:
                            continue
                        if pos_side in ("long", "buy"):
                            logger.info(f"[LIVE] CLOSE LONG {symbol}: {pos_size} contracts")
                            params = {"positionSide": "long", "reduceOnly": True}
                            return await self._exchange.create_market_sell_order(
                                symbol, abs(pos_size), params=params
                            )
                        elif pos_side in ("short", "sell"):
                            logger.info(f"[LIVE] CLOSE SHORT {symbol}: {pos_size} contracts")
                            params = {"positionSide": "short", "reduceOnly": True}
                            return await self._exchange.create_market_buy_order(
                                symbol, abs(pos_size), params=params
                            )
                except Exception as exc:
                    logger.warning(f"Could not fetch positions to close {symbol}: {exc}")
                return {"pnl": 0.0, "status": "no_position"}

            else:
                # Legacy "buy"/"sell" fallthrough (shouldn't normally be used)
                logger.info(f"[LIVE] {side.upper()} {symbol}: {amount} @ {price or 'market'}")
                if side == "buy":
                    return await self._exchange.create_market_buy_order(symbol, amount)
                return await self._exchange.create_market_sell_order(symbol, amount)

        # ── Generic exchanges (Hyperliquid, Kraken, etc.) ────────────────────────
        logger.info(f"[LIVE] {order_type} {side} order: {amount} {symbol} @ {price or 'market'}")
        if order_type == "market":
            ccxt_side = "buy" if side in ("long", "buy") else "sell"
            return await self._exchange.create_market_order(symbol, ccxt_side, amount)
        return await self._exchange.create_limit_order(symbol, side, amount, price)

    # ── Order management ─────────────────────────────────────────────────────────

    @retry_async(attempts=3)
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        return await self._exchange.cancel_order(order_id, symbol)

    @retry_async(attempts=3)
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return await self._exchange.fetch_open_orders(symbol)

    async def close(self) -> None:
        await self._exchange.close()
        logger.info("Exchange connection closed.")
