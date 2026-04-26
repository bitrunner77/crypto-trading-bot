"""
exchange/ccxt_client.py — Live exchange client wrapping CCXT async.
Supports Binance, Coinbase, Kraken, Bybit, Hyperliquid, and any other CCXT exchange.

Side translation: the bot speaks 'long'/'short'/'close'; CCXT speaks
'buy'/'sell' with `params={'reduceOnly': True}` for closes. This client
translates on the way out and surfaces a unified order dict on the way back.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import ccxt.async_support as ccxt

from exchange.base import ExchangeClient
from utils.helpers import retry_async

logger = logging.getLogger("cryptobot.exchange")


def _translate_side(side: str) -> tuple[str, bool]:
    """Map bot side to (ccxt_side, reduce_only)."""
    s = side.lower()
    if s == "long":
        return "buy", False
    if s == "short":
        return "sell", False
    if s == "close":
        # Caller must know existing direction to set ccxt_side correctly;
        # we default to 'sell' and override via params['reduceOnly']=True.
        # The actual closing side is decided in close_position().
        return "sell", True
    raise ValueError(f"Unknown side '{side}' (expected long|short|close)")


class CCXTClient(ExchangeClient):
    """Production exchange client via CCXT.

    Maintains a `_positions_cache` mirroring on-exchange perp positions so
    `check_liquidations()` can detect exchange-side liquidations and notify
    the caller to clean up the local DB.
    """

    def __init__(self, config):
        exchange_cls = getattr(ccxt, config.exchange, None)
        if exchange_cls is None:
            raise ValueError(f"Unknown exchange '{config.exchange}'. Check CCXT docs for supported exchanges.")

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
        else:
            opts = {
                "apiKey": config.exchange_api_key,
                "secret": config.exchange_api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        self._exchange: ccxt.Exchange = exchange_cls(opts)
        self._config = config
        # symbol -> {"side": "long"|"short", "amount": contracts, "entry": float}
        self._positions_cache: Dict[str, Dict] = {}
        logger.info(f"Initialized CCXT client for [bold]{config.exchange}[/bold]")

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

    @retry_async(attempts=3)
    async def create_order(
        self, symbol: str, side: str, amount: float,
        price: Optional[float] = None, order_type: str = "market",
        leverage: int = 1,
    ) -> Dict:
        """Place a leveraged perp order.

        `amount` is in quote-currency margin (USDT/USDC). Contract amount is
        margin × leverage / price, then rounded to the market's precision.
        """
        if side == "close":
            return await self._close_position(symbol, price, order_type)

        ccxt_side, _ = _translate_side(side)

        # Set leverage before submitting (idempotent on most exchanges).
        await self._maybe_set_leverage(symbol, leverage)

        ref_price = price or await self._reference_price(symbol)
        if not ref_price or ref_price <= 0:
            raise ValueError(f"Cannot size order for {symbol}: invalid reference price {ref_price}")

        notional = amount * leverage
        contract_amount = notional / ref_price
        try:
            contract_amount = float(self._exchange.amount_to_precision(symbol, contract_amount))
        except Exception:  # market not loaded yet — submit raw and let exchange enforce precision
            pass

        logger.info(
            f"[LIVE] Placing {order_type} {side.upper()} {symbol} "
            f"margin=${amount:.2f} × {leverage}x → {contract_amount} @ {price or 'market'}"
        )

        if order_type == "market":
            order = await self._exchange.create_market_order(symbol, ccxt_side, contract_amount)
        else:
            order = await self._exchange.create_limit_order(symbol, ccxt_side, contract_amount, price)

        # Update local position cache only if filled.
        if order.get("status") in ("closed", "filled"):
            fill_price = order.get("average") or order.get("price") or ref_price
            self._positions_cache[symbol] = {
                "side": "long" if ccxt_side == "buy" else "short",
                "amount": contract_amount,
                "entry": fill_price,
                "leverage": leverage,
            }

        order.setdefault("amount", contract_amount)
        order.setdefault("price", price or ref_price)
        return order

    async def _close_position(
        self, symbol: str, price: Optional[float], order_type: str
    ) -> Dict:
        """Close an existing perp position via reduceOnly market order."""
        pos = self._positions_cache.get(symbol)
        if not pos:
            # Reconcile against exchange in case the bot was restarted.
            await self._refresh_positions_cache()
            pos = self._positions_cache.get(symbol)
        if not pos:
            logger.warning(f"[LIVE] No open position for {symbol} to close")
            return {"id": None, "symbol": symbol, "side": "close", "status": "skipped"}

        ccxt_side = "sell" if pos["side"] == "long" else "buy"
        amount = pos["amount"]
        params = {"reduceOnly": True}

        logger.info(f"[LIVE] Closing {pos['side'].upper()} {symbol} amount={amount}")
        if order_type == "market":
            order = await self._exchange.create_market_order(
                symbol, ccxt_side, amount, params=params
            )
        else:
            order = await self._exchange.create_limit_order(
                symbol, ccxt_side, amount, price, params=params
            )

        if order.get("status") in ("closed", "filled"):
            exit_price = order.get("average") or order.get("price") or price or pos["entry"]
            entry = pos["entry"]
            qty = pos["amount"]
            pnl = (exit_price - entry) * qty if pos["side"] == "long" else (entry - exit_price) * qty
            order["pnl"] = pnl
            order["entry_price"] = entry
            order["close_price"] = exit_price
            self._positions_cache.pop(symbol, None)
        return order

    async def _maybe_set_leverage(self, symbol: str, leverage: int) -> None:
        """Best-effort leverage configuration. Some exchanges don't support
        `set_leverage` via CCXT; in that case we silently rely on account-
        default leverage (the user must configure it manually)."""
        if leverage <= 1:
            return
        try:
            await self._exchange.set_leverage(leverage, symbol)
        except (ccxt.NotSupported, AttributeError):
            logger.debug(f"[LIVE] set_leverage not supported on {self._config.exchange}")
        except Exception as e:
            logger.warning(f"[LIVE] set_leverage failed for {symbol} @ {leverage}x: {e}")

    async def _reference_price(self, symbol: str) -> float:
        ticker = await self.fetch_ticker(symbol)
        return ticker.get("last") or ticker.get("close") or 0.0

    async def _refresh_positions_cache(self) -> None:
        """Pull live positions from exchange and rebuild the local cache."""
        try:
            positions = await self._exchange.fetch_positions()
        except (ccxt.NotSupported, AttributeError):
            return
        except Exception as e:
            logger.warning(f"[LIVE] fetch_positions failed: {e}")
            return
        new_cache: Dict[str, Dict] = {}
        for p in positions or []:
            sym = p.get("symbol")
            contracts = p.get("contracts") or p.get("contractSize") or 0.0
            if not sym or not contracts or contracts == 0:
                continue
            side = (p.get("side") or "").lower()
            if side not in ("long", "short"):
                continue
            new_cache[sym] = {
                "side": side,
                "amount": float(contracts),
                "entry": float(p.get("entryPrice") or p.get("markPrice") or 0),
                "leverage": int(p.get("leverage") or 1),
            }
        self._positions_cache = new_cache

    async def check_liquidations(self, current_prices: Dict[str, float]) -> List[str]:
        """Detect positions that the exchange has closed (likely liquidated)
        but that the bot still has in its local cache / DB. Returns symbols
        the caller should remove from its DB.
        """
        previous = set(self._positions_cache.keys())
        await self._refresh_positions_cache()
        current = set(self._positions_cache.keys())
        gone = previous - current
        for sym in gone:
            logger.error(f"[LIVE] Position vanished from exchange: {sym} (likely liquidated)")
        return list(gone)

    @retry_async(attempts=3)
    async def cancel_order(self, order_id: str, symbol: str) -> Dict:
        return await self._exchange.cancel_order(order_id, symbol)

    @retry_async(attempts=3)
    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
        return await self._exchange.fetch_open_orders(symbol)

    async def close(self) -> None:
        await self._exchange.close()
        logger.info("Exchange connection closed.")
