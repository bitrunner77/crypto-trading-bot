"""
scripts/grid_trader.py — Live Grid Trading Bot for Sideways / Choppy Markets

Activates on:  choppy | mean_reverting | low_vol
Auto-exits on: trending_up | trending_down | high_vol

Grid mechanics:
  - Builds N levels above and below current price at fixed spread %
  - Places limit BUY orders below price, limit SELL orders above
  - When a BUY fills → cancels paired level → places SELL one level up (re-fill)
  - When a SELL fills → places BUY one level down (re-fill)
  - Min spread enforced above round-trip fees (0.12% min, default 0.5%)
  - Regime checked every 15 min — auto-cancels all orders if market starts trending
  - Dynamic ATR-based grid width: wide grid in volatile ranging, tight in calm

Usage:
  python scripts/grid_trader.py                          # live, BTC
  python scripts/grid_trader.py --symbol ETH/USDT        # live, ETH
  python scripts/grid_trader.py --dry-run                # simulate
  python scripts/grid_trader.py --symbol SOL/USDT --levels 6 --spread 0.008
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import ccxt
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from analysis.regime_detector import detect_regime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("grid_trader")

# ── Config ────────────────────────────────────────────────────────────────────
DEFAULT_SYMBOL       = os.getenv("GRID_SYMBOL",        "BTC/USDT")
DEFAULT_LEVELS       = int(os.getenv("GRID_LEVELS",    "8"))      # levels each side
DEFAULT_SPREAD       = float(os.getenv("GRID_SPREAD",  "0.005"))  # 0.5% per level
DEFAULT_SIZE_USDT    = float(os.getenv("GRID_SIZE",    "5.0"))    # $ per grid order
DEFAULT_LEVERAGE     = int(os.getenv("GRID_LEVERAGE",  "3"))      # conservative grid lev
REGIME_CHECK_MIN     = 15       # recheck regime every 15 min
FILL_POLL_SEC        = 10       # poll order fills every 10s
GRID_REGIMES         = {"choppy", "mean_reverting", "low_vol"}
TAKER_FEE            = float(os.getenv("BITGET_TAKER_FEE", "0.0006"))
MIN_SPREAD           = TAKER_FEE * 2 * 1.5   # 1.5× round-trip fees minimum
FUTURES_SUFFIX       = "/USDT:USDT"

STATE_FILE = Path(__file__).resolve().parents[1] / "data" / "grid_state.json"
PID_FILE   = Path(__file__).resolve().parents[1] / "data" / "grid_trader.pid"


# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class GridLevel:
    index:    int        # -N (lowest buy) to +N (highest sell)
    price:    float
    side:     str        # "buy" | "sell"
    order_id: str  = ""
    filled:   bool = False
    qty:      float = 0.0


@dataclass
class GridState:
    symbol:       str
    base_price:   float
    spread:       float
    levels:       List[GridLevel]   = field(default_factory=list)
    total_profit: float = 0.0
    fills:        int   = 0
    started_at:   float = 0.0


# ── PID lock ──────────────────────────────────────────────────────────────────
def _acquire_lock() -> None:
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            if existing != os.getpid():
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, existing)
                if handle:
                    ctypes.windll.kernel32.CloseHandle(handle)
                    log.error(f"Grid trader already running (PID {existing}). Stop it first.")
                    sys.exit(1)
        except (ValueError, OSError):
            pass
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _release_lock() -> None:
    try:
        if PID_FILE.exists() and PID_FILE.read_text().strip() == str(os.getpid()):
            PID_FILE.unlink()
    except Exception:
        pass


# ── Exchange ──────────────────────────────────────────────────────────────────
def make_exchange() -> ccxt.Exchange:
    return ccxt.bitget({
        "apiKey":   os.getenv("EXCHANGE_API_KEY", ""),
        "secret":   os.getenv("EXCHANGE_API_SECRET", ""),
        "password": os.getenv("EXCHANGE_PASSPHRASE", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def make_pub() -> ccxt.Exchange:
    return ccxt.binance({"enableRateLimit": True})


# ── Telegram alert ────────────────────────────────────────────────────────────
def _alert(msg: str) -> None:
    try:
        from scripts.telegram_alerts import send_alert
        send_alert(msg)
    except Exception:
        pass


# ── Regime check ──────────────────────────────────────────────────────────────
def get_regime(symbol: str, pub: ccxt.Exchange) -> str:
    try:
        raw = pub.fetch_ohlcv(symbol, "1h", limit=60)
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"open": float,"high": float,"low": float,"close": float})
        return detect_regime(df).regime
    except Exception as exc:
        log.warning(f"Regime check failed: {exc}")
        return "unknown"


# ── ATR helper ────────────────────────────────────────────────────────────────
def get_atr(symbol: str, pub: ccxt.Exchange) -> float:
    try:
        raw = pub.fetch_ohlcv(symbol, "1h", limit=30)
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"high": float,"low": float,"close": float})
        tr  = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(14).mean().iloc[-1])
    except Exception:
        return 0.0


# ── Grid builder ──────────────────────────────────────────────────────────────
def build_grid(symbol: str, price: float, spread: float,
               n_levels: int, size_usdt: float,
               exchange: ccxt.Exchange) -> GridState:
    """Build grid levels and calculate qty per level."""
    futures_sym = symbol.replace("/USDT", "") + FUTURES_SUFFIX
    try:
        mkt  = exchange.market(futures_sym)
        min_qty = float(mkt.get("limits", {}).get("amount", {}).get("min") or 1)
        precision = int(mkt.get("precision", {}).get("amount") or 0)
    except Exception:
        min_qty, precision = 1, 0

    levels: List[GridLevel] = []
    for i in range(1, n_levels + 1):
        buy_price  = price * (1 - i * spread)
        sell_price = price * (1 + i * spread)
        qty = max(min_qty, round(size_usdt / buy_price, precision))

        levels.append(GridLevel(index=-i, price=round(buy_price, 6),  side="buy",  qty=qty))
        levels.append(GridLevel(index=+i, price=round(sell_price, 6), side="sell", qty=qty))

    levels.sort(key=lambda l: l.price)
    return GridState(
        symbol=symbol, base_price=price, spread=spread,
        levels=levels, started_at=time.time()
    )


# ── Order placement ───────────────────────────────────────────────────────────
def place_order(level: GridLevel, symbol: str, leverage: int,
                exchange: ccxt.Exchange, dry_run: bool) -> bool:
    futures_sym = symbol.replace("/USDT", "") + FUTURES_SUFFIX
    try:
        if dry_run:
            level.order_id = f"DRY_{level.side}_{level.index}_{int(time.time())}"
            log.info(f"  [DRY] {level.side.upper()} limit @ ${level.price:.6f} "
                     f"qty={level.qty} (level {level.index:+d})")
            return True

        exchange.set_leverage(leverage, futures_sym)
        side   = "buy" if level.side == "buy" else "sell"
        order  = exchange.create_limit_order(futures_sym, side, level.qty, level.price)
        level.order_id = order.get("id", "")
        log.info(f"  {level.side.upper()} limit placed @ ${level.price:.6f} "
                 f"qty={level.qty} id={level.order_id} (level {level.index:+d})")
        return True
    except Exception as exc:
        log.warning(f"  Failed to place {level.side} @ ${level.price:.6f}: {exc}")
        return False


def cancel_order(order_id: str, futures_sym: str,
                 exchange: ccxt.Exchange, dry_run: bool) -> None:
    if dry_run or not order_id or order_id.startswith("DRY_"):
        return
    try:
        exchange.cancel_order(order_id, futures_sym)
        log.info(f"  Cancelled order {order_id}")
    except Exception as exc:
        log.debug(f"  Cancel {order_id} skipped: {exc}")


def cancel_all_orders(symbol: str, exchange: ccxt.Exchange, dry_run: bool) -> None:
    futures_sym = symbol.replace("/USDT", "") + FUTURES_SUFFIX
    if dry_run:
        log.info("[DRY] Would cancel all open grid orders")
        return
    try:
        open_orders = exchange.fetch_open_orders(futures_sym)
        for o in open_orders:
            try:
                exchange.cancel_order(o["id"], futures_sym)
            except Exception:
                pass
        log.info(f"  Cancelled {len(open_orders)} open grid orders")
    except Exception as exc:
        log.warning(f"  Cancel-all failed: {exc}")


# ── Fill checker ──────────────────────────────────────────────────────────────
def check_fills(state: GridState, exchange: ccxt.Exchange,
                leverage: int, dry_run: bool) -> int:
    """Check for filled orders. Re-place opposite side on fill. Returns fill count."""
    futures_sym = state.symbol.replace("/USDT", "") + FUTURES_SUFFIX
    new_fills   = 0

    for level in state.levels:
        if level.filled or not level.order_id:
            continue

        filled = False
        if dry_run:
            # Simulate fills: fetch live price and check if limit would have triggered
            try:
                px = exchange.fetch_ticker(futures_sym)["last"]
                if level.side == "buy"  and float(px) <= level.price:
                    filled = True
                elif level.side == "sell" and float(px) >= level.price:
                    filled = True
            except Exception:
                pass
        else:
            try:
                order  = exchange.fetch_order(level.order_id, futures_sym)
                status = order.get("status", "")
                filled = status in ("closed", "filled")
            except Exception:
                pass

        if not filled:
            continue

        level.filled = True
        new_fills   += 1
        fee_cost     = level.price * level.qty * TAKER_FEE
        profit_this  = level.price * level.qty * state.spread - fee_cost * 2

        if level.side == "buy":
            state.total_profit += profit_this
            state.fills        += 1
            log.info(f"  FILL BUY  @ ${level.price:.6f} (level {level.index:+d}) "
                     f"est_profit=${profit_this:.4f}")
            _alert(f"*GRID FILL BUY: {state.symbol}*\n"
                   f"Level {level.index:+d} @ `${level.price:.6f}`\n"
                   f"Est profit/fill: `${profit_this:.4f}` | Total: `${state.total_profit:.4f}`")
            # Re-place sell one level up
            sell_price = level.price * (1 + state.spread)
            new_sell   = GridLevel(index=-level.index, price=round(sell_price, 6),
                                   side="sell", qty=level.qty)
            if place_order(new_sell, state.symbol, leverage, exchange, dry_run):
                state.levels.append(new_sell)

        else:  # sell fill
            state.total_profit += profit_this
            state.fills        += 1
            log.info(f"  FILL SELL @ ${level.price:.6f} (level {level.index:+d}) "
                     f"est_profit=${profit_this:.4f}")
            _alert(f"*GRID FILL SELL: {state.symbol}*\n"
                   f"Level {level.index:+d} @ `${level.price:.6f}`\n"
                   f"Est profit/fill: `${profit_this:.4f}` | Total: `${state.total_profit:.4f}`")
            # Re-place buy one level down
            buy_price = level.price * (1 - state.spread)
            new_buy   = GridLevel(index=-level.index, price=round(buy_price, 6),
                                  side="buy", qty=level.qty)
            if place_order(new_buy, state.symbol, leverage, exchange, dry_run):
                state.levels.append(new_buy)

    return new_fills


# ── State persistence ─────────────────────────────────────────────────────────
def save_state(state: GridState) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        d = asdict(state)
        STATE_FILE.write_text(json.dumps(d, indent=2))
    except Exception as exc:
        log.warning(f"State save failed: {exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Grid Trading Bot")
    parser.add_argument("--symbol",   default=DEFAULT_SYMBOL)
    parser.add_argument("--levels",   type=int,   default=DEFAULT_LEVELS)
    parser.add_argument("--spread",   type=float, default=DEFAULT_SPREAD)
    parser.add_argument("--size",     type=float, default=DEFAULT_SIZE_USDT,
                        help="USDT per grid level")
    parser.add_argument("--leverage", type=int,   default=DEFAULT_LEVERAGE)
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()

    # Enforce minimum spread
    if args.spread < MIN_SPREAD:
        log.warning(f"Spread {args.spread:.4f} below fee minimum {MIN_SPREAD:.4f} — "
                    f"raised to {MIN_SPREAD:.4f}")
        args.spread = MIN_SPREAD

    mode = "DRY RUN" if args.dry_run else "LIVE"
    log.info(f"=== Grid Trader [{mode}] ===")
    log.info(f"Symbol: {args.symbol} | Levels: ±{args.levels} | "
             f"Spread: {args.spread*100:.2f}%/level | Size: ${args.size:.2f}/level | "
             f"Leverage: {args.leverage}x")

    _acquire_lock()
    pub      = make_pub()
    exchange = make_exchange()
    state: Optional[GridState] = None
    last_regime_check = 0.0

    try:
        while True:
            now    = time.time()
            regime = get_regime(args.symbol, pub)

            # ── Regime check every 15 min ─────────────────────────────────────
            if now - last_regime_check >= REGIME_CHECK_MIN * 60 or state is None:
                last_regime_check = now
                log.info(f"Regime: {regime}")

                if regime not in GRID_REGIMES:
                    if state is not None:
                        log.info(f"Regime changed to {regime} — cancelling all grid orders")
                        _alert(f"*GRID AUTO-EXIT: {args.symbol}*\n"
                               f"Regime → `{regime}` (trending/volatile)\n"
                               f"Cancelling all orders | Profit: `${state.total_profit:.4f}`")
                        cancel_all_orders(args.symbol, exchange, args.dry_run)
                        save_state(state)
                        state = None
                    log.info(f"  Waiting for choppy/mean_reverting/low_vol (current: {regime})")
                    time.sleep(FILL_POLL_SEC)
                    continue

                # ── Build/rebuild grid ────────────────────────────────────────
                if state is None:
                    try:
                        futures_sym = args.symbol.replace("/USDT", "") + FUTURES_SUFFIX
                        ticker      = exchange.fetch_ticker(futures_sym)
                        price       = float(ticker["last"])
                    except Exception as exc:
                        log.error(f"Price fetch failed: {exc}")
                        time.sleep(FILL_POLL_SEC)
                        continue

                    # ATR-aware spread: widen grid if market is volatile within range
                    atr      = get_atr(args.symbol, pub)
                    atr_pct  = atr / price if price > 0 else args.spread
                    spread   = max(args.spread, min(atr_pct * 0.8, args.spread * 2.5))
                    if spread != args.spread:
                        log.info(f"  ATR-adjusted spread: {spread*100:.3f}% (ATR={atr_pct*100:.3f}%)")

                    log.info(f"  Building grid around ${price:.6f} | regime={regime} | "
                             f"spread={spread*100:.2f}%")
                    state = build_grid(args.symbol, price, spread,
                                       args.levels, args.size, exchange)

                    # Place all initial orders
                    placed = 0
                    for level in state.levels:
                        if place_order(level, args.symbol, args.leverage, exchange, args.dry_run):
                            placed += 1
                        time.sleep(0.3)   # avoid rate limit

                    log.info(f"  Grid active: {placed} orders placed | "
                             f"Buy range: ${state.levels[0].price:.4f} — "
                             f"${state.levels[args.levels-1].price:.4f} | "
                             f"Sell range: ${state.levels[args.levels].price:.4f} — "
                             f"${state.levels[-1].price:.4f}")
                    _alert(
                        f"*GRID STARTED: {args.symbol}*\n"
                        f"Regime: `{regime}` | Spread: `{spread*100:.2f}%`\n"
                        f"Levels: ±{args.levels} | Size: `${args.size:.2f}`/level\n"
                        f"Base price: `${price:.4f}`"
                    )
                    save_state(state)

            # ── Poll for fills ────────────────────────────────────────────────
            if state is not None:
                new_fills = check_fills(state, exchange, args.leverage, args.dry_run)
                if new_fills:
                    save_state(state)
                    log.info(f"  Grid stats: {state.fills} total fills | "
                             f"Profit: ${state.total_profit:.4f}")

            time.sleep(FILL_POLL_SEC)

    except KeyboardInterrupt:
        log.info("Shutting down grid trader...")
        if state is not None:
            cancel_all_orders(args.symbol, exchange, args.dry_run)
            save_state(state)
            _alert(f"*GRID STOPPED: {args.symbol}*\n"
                   f"Total fills: {state.fills} | Profit: `${state.total_profit:.4f}`")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
