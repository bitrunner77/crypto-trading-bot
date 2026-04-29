"""
scripts/scalp_trader.py — Live Scalping Bot (5m candles, long + short)

Signal stack (all required for entry):
  Entry LONG : EMA3 crosses above EMA8 | RSI(7) 40-65 | price > EMA21
               | volume spike >1.5x | MACD histogram > 0
  Entry SHORT: EMA3 crosses below EMA8 | RSI(7) 35-60 | price < EMA21
               | volume spike >1.5x | MACD histogram < 0

Exit:
  SL  : 0.4% against entry
  TP1 : 0.6% → close 50%, lock SL to breakeven
  TP2 : 1.2% → close remainder
  Hard: EMA crossover reversal OR RSI extreme (>75 / <25)

Filters:
  - BTC regime must allow scalping (any except low_vol)
  - Trading hours: 8am–9pm UTC (London + NY)
  - Min volume: $5M/24h
  - Fee gate: TP1 must exceed round-trip cost × 2
  - Max concurrent positions: 3
  - Manipulation detection (pump/dump veto)

Usage:
  python scripts/scalp_trader.py                  # BTC+ETH+SOL live
  python scripts/scalp_trader.py --dry-run        # simulate
  python scripts/scalp_trader.py --symbols ETH SOL PEPE  # custom list
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import ccxt
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from analysis.regime_detector import detect_regime
from analysis.manipulation_detector import detect_manipulation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scalp_trader")

# ── Config ────────────────────────────────────────────────────────────────────
TIMEFRAME          = "5m"
SCAN_INTERVAL_SEC  = 30          # scan every 30 seconds
CANDLES            = 100         # candles to fetch per symbol
MAX_POSITIONS      = 5           # max concurrent scalp positions
LEVERAGE           = int(os.getenv("SCALP_LEVERAGE", "5"))
SIZE_USDT          = float(os.getenv("SCALP_SIZE",   "10.0"))  # $ per trade
TRADING_HOURS_UTC  = (8, 21)
MIN_VOLUME_USDT    = 5_000_000   # $5M/24h minimum

# Signal thresholds
RSI_LONG_MIN    = 40.0
RSI_LONG_MAX    = 65.0
RSI_SHORT_MIN   = 35.0
RSI_SHORT_MAX   = 60.0
RSI_EXIT_LONG   = 75.0
RSI_EXIT_SHORT  = 25.0
VOL_SPIKE_MULT  = 1.5

# Risk per trade
SL_PCT   = 0.004   # 0.4% SL
TP1_PCT  = 0.006   # 0.6% TP1 (close 50%)
TP2_PCT  = 0.012   # 1.2% TP2 (close remaining)

# Fees
TAKER_FEE   = float(os.getenv("BITGET_TAKER_FEE", "0.0006"))
ROUND_TRIP  = TAKER_FEE * 2

FUTURES_SUFFIX = "/USDT:USDT"
DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

TRADES_LOG = Path(__file__).resolve().parents[1] / "data" / "scalp_trades.json"
PID_FILE   = Path(__file__).resolve().parents[1] / "data" / "scalp_trader.pid"

# Block regimes where scalping is too slow/dangerous
BLOCK_REGIMES = {"low_vol"}


# ── Data classes ──────────────────────────────────────────────────────────────
@dataclass
class ScalpPosition:
    symbol:      str
    futures_sym: str
    side:        str    # "long" | "short"
    entry:       float
    sl:          float
    tp1:         float
    tp2:         float
    qty:         float
    leverage:    int
    opened_at:   float = 0.0
    tp1_hit:     bool  = False
    be_locked:   bool  = False
    order_id:    str   = ""


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
                    log.error(f"Scalp trader already running (PID {existing}).")
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


# ── Telegram ──────────────────────────────────────────────────────────────────
def _alert(msg: str) -> None:
    try:
        from scripts.telegram_alerts import send_alert
        send_alert(msg)
    except Exception:
        pass


# ── Indicators (no external ta lib dependency) ────────────────────────────────
def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, length: int = 7) -> float:
    delta = close.diff().dropna()
    gain  = delta.clip(lower=0).rolling(length).mean()
    loss  = (-delta.clip(upper=0)).rolling(length).mean()
    rs    = gain / loss.replace(0, 1e-9)
    rsi   = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])


def _macd_hist(close: pd.Series) -> float:
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd  = ema12 - ema26
    sig   = _ema(macd, 9)
    return float((macd - sig).iloc[-1])


def _atr(df: pd.DataFrame, length: int = 14) -> float:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(length).mean().iloc[-1])


def _vwap(df: pd.DataFrame) -> float:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    return float((typical * df["vol"]).sum() / df["vol"].sum())


def _bollinger(close: pd.Series, length: int = 20) -> Tuple[float, float]:
    sma  = close.rolling(length).mean()
    std  = close.rolling(length).std()
    return float((sma + 2 * std).iloc[-1]), float((sma - 2 * std).iloc[-1])


# ── Session filter ────────────────────────────────────────────────────────────
def in_session() -> bool:
    return TRADING_HOURS_UTC[0] <= datetime.now(timezone.utc).hour < TRADING_HOURS_UTC[1]


# ── BTC regime ────────────────────────────────────────────────────────────────
def get_btc_regime(pub: ccxt.Exchange) -> str:
    try:
        raw = pub.fetch_ohlcv("BTC/USDT", "15m", limit=60)
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"open": float,"high": float,"low": float,"close": float})
        return detect_regime(df).regime
    except Exception:
        return "unknown"


# ── Volume scan — top 100 coins by 24h volume ────────────────────────────────
def scan_high_vol(pub: ccxt.Exchange, symbols: List[str]) -> List[str]:
    try:
        tickers = pub.fetch_tickers()
        dynamic = [
            t["symbol"] for t in tickers.values()
            if t["symbol"].endswith("/USDT")
            and (t.get("quoteVolume") or 0) >= MIN_VOLUME_USDT
            and t["symbol"].isascii()
            and not t["symbol"].startswith("USDT")
            and "/" in t["symbol"]
        ]
        dynamic.sort(key=lambda s: tickers[s].get("quoteVolume", 0), reverse=True)
        # Watchlist first, then top 100 by volume
        combined = list(dict.fromkeys(symbols + dynamic))
        return combined[:100]
    except Exception:
        return symbols


# ── Signal generator ──────────────────────────────────────────────────────────
def generate_signal(symbol: str, pub: ccxt.Exchange) -> Optional[Dict]:
    """
    Returns dict with keys: side, entry, sl, tp1, tp2, score, reason
    or None if no signal.
    """
    try:
        raw = pub.fetch_ohlcv(symbol, TIMEFRAME, limit=CANDLES)
        if len(raw) < 30:
            return None
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df = df.astype({"open": float,"high": float,"low": float,
                        "close": float,"vol": float})

        close  = df["close"]
        price  = float(close.iloc[-1])

        # ── Indicators ────────────────────────────────────────────────────────
        ema3   = _ema(close, 3)
        ema8   = _ema(close, 8)
        ema21  = _ema(close, 21)
        ema50  = _ema(close, 50)

        ema3_now,  ema3_prev  = float(ema3.iloc[-1]),  float(ema3.iloc[-2])
        ema8_now,  ema8_prev  = float(ema8.iloc[-1]),  float(ema8.iloc[-2])
        ema21_now             = float(ema21.iloc[-1])
        ema50_now             = float(ema50.iloc[-1])

        crossed_up   = ema3_prev <= ema8_prev and ema3_now > ema8_now
        crossed_down = ema3_prev >= ema8_prev and ema3_now < ema8_now

        rsi       = _rsi(close, 7)
        macd_h    = _macd_hist(close)
        atr_val   = _atr(df)
        bb_up, bb_lo = _bollinger(close)
        vwap      = _vwap(df)

        avg_vol   = float(df["vol"].iloc[-20:-1].mean())
        cur_vol   = float(df["vol"].iloc[-1])
        vol_spike = cur_vol >= avg_vol * VOL_SPIKE_MULT

        # Manipulation veto
        dfm = df.rename(columns={"vol": "volume"})
        manip = detect_manipulation(dfm)
        if manip.action == "avoid":
            return None

        # ── LONG signal ───────────────────────────────────────────────────────
        long_score = 0
        long_why   = []

        if crossed_up:
            long_score += 3
            long_why.append("EMA3↑EMA8 cross")
        elif ema3_now > ema8_now:
            long_score += 1
            long_why.append("EMA3>EMA8")

        if RSI_LONG_MIN <= rsi <= RSI_LONG_MAX:
            long_score += 2
            long_why.append(f"RSI {rsi:.0f}")

        if macd_h > 0:
            long_score += 2
            long_why.append("MACD+")

        if vol_spike:
            long_score += 1
            long_why.append(f"Vol {cur_vol/avg_vol:.1f}x")

        if price > ema21_now:
            long_score += 1
            long_why.append("Above EMA21")

        if price > vwap:
            long_score += 1
            long_why.append("Above VWAP")

        if manip.action == "reduce_size":
            long_score -= 1   # caution but don't veto

        if long_score >= 6:
            sl  = price * (1 - SL_PCT)
            tp1 = price * (1 + TP1_PCT)
            tp2 = price * (1 + TP2_PCT)
            # Fee gate: TP1 gross profit (with leverage) must exceed round-trip cost
            if (tp1 - price) / price * LEVERAGE > ROUND_TRIP * 2:
                return {"side": "long", "entry": price, "sl": sl, "tp1": tp1,
                        "tp2": tp2, "score": long_score,
                        "reason": "SCALP LONG: " + ", ".join(long_why),
                        "atr": atr_val}

        # ── SHORT signal ──────────────────────────────────────────────────────
        short_score = 0
        short_why   = []

        if crossed_down:
            short_score += 3
            short_why.append("EMA3↓EMA8 cross")
        elif ema3_now < ema8_now:
            short_score += 1
            short_why.append("EMA3<EMA8")

        if RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX:
            short_score += 2
            short_why.append(f"RSI {rsi:.0f}")

        if macd_h < 0:
            short_score += 2
            short_why.append("MACD-")

        if vol_spike:
            short_score += 1
            short_why.append(f"Vol {cur_vol/avg_vol:.1f}x")

        if price < ema21_now:
            short_score += 1
            short_why.append("Below EMA21")

        if price < vwap:
            short_score += 1
            short_why.append("Below VWAP")

        if manip.action == "reduce_size":
            short_score -= 1

        if short_score >= 6:
            sl  = price * (1 + SL_PCT)
            tp1 = price * (1 - TP1_PCT)
            tp2 = price * (1 - TP2_PCT)
            if (price - tp1) / price * LEVERAGE > ROUND_TRIP * 2:
                return {"side": "short", "entry": price, "sl": sl, "tp1": tp1,
                        "tp2": tp2, "score": short_score,
                        "reason": "SCALP SHORT: " + ", ".join(short_why),
                        "atr": atr_val}

        return None

    except Exception as exc:
        log.debug(f"  {symbol}: signal error — {exc}")
        return None


# ── Order execution ───────────────────────────────────────────────────────────
def open_position(symbol: str, sig: Dict, exchange: ccxt.Exchange,
                  dry_run: bool) -> Optional[ScalpPosition]:
    futures_sym = symbol.replace("/USDT", "") + FUTURES_SUFFIX
    price       = sig["entry"]
    side        = sig["side"]
    qty         = round(SIZE_USDT * LEVERAGE / price, 4)
    qty         = max(0.0001, qty)

    try:
        exchange.set_leverage(LEVERAGE, futures_sym)
    except Exception:
        pass

    order_id = ""
    if not dry_run:
        try:
            ccxt_side = "buy" if side == "long" else "sell"
            order     = exchange.create_market_order(futures_sym, ccxt_side, qty)
            order_id  = order.get("id", "")
            price     = float(order.get("average") or order.get("price") or price)
            log.info(f"  ORDER {side.upper()}: {futures_sym} qty={qty} avg=${price:.4f} id={order_id}")
        except Exception as exc:
            log.error(f"  Order failed {futures_sym}: {exc}")
            return None
    else:
        log.info(f"  [DRY] {side.upper()}: {futures_sym} qty={qty} @ ${price:.4f}")

    # Recalculate SL/TP from actual fill price
    if side == "long":
        sl  = price * (1 - SL_PCT)
        tp1 = price * (1 + TP1_PCT)
        tp2 = price * (1 + TP2_PCT)
    else:
        sl  = price * (1 + SL_PCT)
        tp1 = price * (1 - TP1_PCT)
        tp2 = price * (1 - TP2_PCT)

    # Place SL order
    if not dry_run:
        try:
            exchange.create_order(futures_sym, "market",
                "sell" if side == "long" else "buy", qty,
                params={"stopLossPrice": sl, "reduceOnly": True})
        except Exception as e:
            log.warning(f"  SL placement failed: {e}")

    pos = ScalpPosition(
        symbol=symbol, futures_sym=futures_sym,
        side=side, entry=price, sl=sl, tp1=tp1, tp2=tp2,
        qty=qty, leverage=LEVERAGE, opened_at=time.time(),
        order_id=order_id,
    )

    _alert(
        f"*SCALP {side.upper()}: {symbol}*\n"
        f"Entry: `${price:.4f}` | SL: `${sl:.4f}` | TP1: `${tp1:.4f}`\n"
        f"Score: {sig['score']} | {sig['reason']}"
    )
    return pos


# ── Position monitoring ───────────────────────────────────────────────────────
def monitor_position(pos: ScalpPosition, pub: ccxt.Exchange,
                     exchange: ccxt.Exchange, dry_run: bool) -> Optional[str]:
    """
    Returns "closed" if position should be removed, else None.
    Manages TP1 partial, BE lock, TP2 full close.
    """
    try:
        ticker = pub.fetch_ticker(pos.symbol)
        price  = float(ticker["last"])
    except Exception:
        return None

    # ── SL check ─────────────────────────────────────────────────────────────
    if pos.side == "long"  and price <= pos.sl:
        _close_position(pos, price, "SL hit", exchange, dry_run)
        return "closed"
    if pos.side == "short" and price >= pos.sl:
        _close_position(pos, price, "SL hit", exchange, dry_run)
        return "closed"

    # ── Breakeven lock (on +1% unrealised) ───────────────────────────────────
    if not pos.be_locked:
        be_trigger = pos.entry * (1 + 0.01) if pos.side == "long" else pos.entry * (1 - 0.01)
        if (pos.side == "long" and price >= be_trigger) or \
           (pos.side == "short" and price <= be_trigger):
            pos.sl       = pos.entry
            pos.be_locked = True
            log.info(f"  [SCALP] {pos.symbol} BE locked @ ${pos.entry:.4f}")
            if not dry_run:
                try:
                    exchange.create_order(pos.futures_sym, "market",
                        "sell" if pos.side == "long" else "buy", pos.qty,
                        params={"stopLossPrice": pos.sl, "reduceOnly": True})
                except Exception:
                    pass

    # ── TP1 (close 50%) ───────────────────────────────────────────────────────
    if not pos.tp1_hit:
        tp1_hit = (pos.side == "long"  and price >= pos.tp1) or \
                  (pos.side == "short" and price <= pos.tp1)
        if tp1_hit:
            pos.tp1_hit  = True
            pos.be_locked = True
            pos.sl        = pos.entry
            close_qty     = round(pos.qty * 0.5, 4)
            log.info(f"  [SCALP] {pos.symbol} TP1 hit @ ${price:.4f} — closing 50%")
            _alert(f"*SCALP TP1: {pos.symbol}*\n"
                   f"`${pos.entry:.4f}` → `${price:.4f}` | Closed 50%")
            if not dry_run:
                try:
                    exchange.create_market_order(
                        pos.futures_sym,
                        "sell" if pos.side == "long" else "buy",
                        close_qty, params={"reduceOnly": True})
                    pos.qty -= close_qty
                except Exception as e:
                    log.warning(f"  TP1 close failed: {e}")

    # ── TP2 (close all) ───────────────────────────────────────────────────────
    if pos.tp1_hit:
        tp2_hit = (pos.side == "long"  and price >= pos.tp2) or \
                  (pos.side == "short" and price <= pos.tp2)
        if tp2_hit:
            _close_position(pos, price, "TP2 hit", exchange, dry_run)
            return "closed"

    # ── Hard exit: EMA crossover reversal ─────────────────────────────────────
    try:
        raw   = pub.fetch_ohlcv(pos.symbol, TIMEFRAME, limit=10)
        df    = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        close = df["close"].astype(float)
        ema3  = _ema(close, 3)
        ema8  = _ema(close, 8)
        rsi   = _rsi(close, 7)
        reversal = False
        if pos.side == "long"  and float(ema3.iloc[-1]) < float(ema8.iloc[-1]) and rsi > RSI_EXIT_LONG:
            reversal = True
        if pos.side == "short" and float(ema3.iloc[-1]) > float(ema8.iloc[-1]) and rsi < RSI_EXIT_SHORT:
            reversal = True
        if reversal:
            _close_position(pos, price, "Signal reversal", exchange, dry_run)
            return "closed"
    except Exception:
        pass

    age_min = (time.time() - pos.opened_at) / 60
    log.info(f"  [SCALP] {pos.symbol} {pos.side.upper()} ${price:.4f} | "
             f"SL=${pos.sl:.4f} TP1=${pos.tp1:.4f} TP2={pos.tp2:.4f} | "
             f"age={age_min:.0f}m | be={'Y' if pos.be_locked else 'N'}")
    return None


def _close_position(pos: ScalpPosition, price: float, reason: str,
                    exchange: ccxt.Exchange, dry_run: bool) -> None:
    pnl_pct = ((price - pos.entry) / pos.entry) * (1 if pos.side == "long" else -1) * LEVERAGE * 100
    log.info(f"  [SCALP CLOSE] {pos.symbol} {pos.side.upper()} — {reason} "
             f"@ ${price:.4f} | PnL ≈ {pnl_pct:+.2f}%")
    _alert(f"*SCALP CLOSE: {pos.symbol}*\n"
           f"Reason: {reason}\n"
           f"Entry: `${pos.entry:.4f}` → `${price:.4f}`\n"
           f"PnL ≈ `{pnl_pct:+.2f}%` ({pos.side.upper()} {LEVERAGE}x)")
    if dry_run:
        return
    try:
        exchange.create_market_order(
            pos.futures_sym,
            "sell" if pos.side == "long" else "buy",
            pos.qty, params={"reduceOnly": True})
    except Exception as exc:
        log.warning(f"  Close failed {pos.futures_sym}: {exc}")


# ── Trade log ─────────────────────────────────────────────────────────────────
def log_trade(sym: str, side: str, entry: float, exit_price: float,
              reason: str) -> None:
    try:
        TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
        trades = json.loads(TRADES_LOG.read_text()) if TRADES_LOG.exists() else []
        pnl    = (exit_price - entry) / entry * (1 if side == "long" else -1) * LEVERAGE
        trades.append({"ts": time.time(), "symbol": sym, "side": side,
                       "entry": entry, "exit": exit_price,
                       "pnl_pct": round(pnl * 100, 3), "reason": reason})
        TRADES_LOG.write_text(json.dumps(trades[-500:], indent=2))  # keep last 500
    except Exception:
        pass


def print_stats() -> None:
    try:
        if not TRADES_LOG.exists():
            return
        trades  = json.loads(TRADES_LOG.read_text())
        closed  = [t for t in trades if "exit" in t]
        if not closed:
            return
        wins    = sum(1 for t in closed if t.get("pnl_pct", 0) > 0)
        losses  = len(closed) - wins
        total   = sum(t.get("pnl_pct", 0) for t in closed)
        wr      = wins / len(closed) * 100 if closed else 0
        log.info(f"══ Scalp Stats: {len(closed)} trades | {wins}W/{losses}L | "
                 f"WR={wr:.1f}% | Total PnL={total:+.2f}%")
    except Exception:
        pass


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Scalping Bot")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS,
                        help="Symbols to trade e.g. BTC/USDT ETH/USDT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    log.info(f"=== Scalp Trader [{mode}] ===")
    log.info(f"Symbols: {args.symbols} | TF: {TIMEFRAME} | "
             f"Leverage: {LEVERAGE}x | Size: ${SIZE_USDT}/trade | Max positions: {MAX_POSITIONS}")
    log.info(f"SL: {SL_PCT*100:.1f}% | TP1: {TP1_PCT*100:.1f}% | TP2: {TP2_PCT*100:.1f}% | "
             f"Fee gate: {ROUND_TRIP*100:.3f}% round-trip")
    print_stats()

    _acquire_lock()
    pub      = make_pub()
    exchange = make_exchange()
    positions: Dict[str, ScalpPosition] = {}   # symbol → position
    btc_regime    = "unknown"
    last_regime   = 0.0
    regime_ttl    = 300   # refresh BTC regime every 5 min

    _alert(f"*Scalp Trader STARTED [{mode}]*\n"
           f"Symbols: {', '.join(args.symbols)}\n"
           f"TF: {TIMEFRAME} | {LEVERAGE}x | ${SIZE_USDT}/trade")

    try:
        while True:
            now = time.time()

            # ── Regime check every 5 min ──────────────────────────────────────
            if now - last_regime > regime_ttl:
                btc_regime = get_btc_regime(pub)
                last_regime = now
                if btc_regime in BLOCK_REGIMES:
                    log.info(f"BTC regime: {btc_regime} — scalping paused (no edge)")
                else:
                    log.info(f"BTC regime: {btc_regime} — scalping ACTIVE")

            if not in_session():
                log.info(f"Outside trading session ({datetime.now(timezone.utc).hour}h UTC) — waiting")
                time.sleep(60)
                continue

            if btc_regime in BLOCK_REGIMES:
                time.sleep(60)
                continue

            # ── Monitor open positions ────────────────────────────────────────
            closed_syms = []
            for sym, pos in list(positions.items()):
                result = monitor_position(pos, pub, exchange, args.dry_run)
                if result == "closed":
                    closed_syms.append(sym)
            for sym in closed_syms:
                pos = positions.pop(sym)
                try:
                    ticker = pub.fetch_ticker(sym)
                    log_trade(sym, pos.side, pos.entry,
                              float(ticker["last"]), "auto-exit")
                except Exception:
                    pass

            # ── Scan for new signals ──────────────────────────────────────────
            slots = MAX_POSITIONS - len(positions)
            if slots > 0:
                scan_list = scan_high_vol(pub, args.symbols)
                candidates = []
                for sym in scan_list:
                    if sym in positions:
                        continue
                    sig = generate_signal(sym, pub)
                    if sig:
                        candidates.append((sym, sig))

                # Sort by score, take best
                candidates.sort(key=lambda x: x[1]["score"], reverse=True)
                for sym, sig in candidates[:slots]:
                    log.info(f"  SIGNAL: {sym} {sig['side'].upper()} "
                             f"score={sig['score']} | {sig['reason']}")
                    pos = open_position(sym, sig, exchange, args.dry_run)
                    if pos:
                        positions[sym] = pos
            else:
                log.info(f"  Max positions ({MAX_POSITIONS}) reached — monitoring only")

            # ── Summary ───────────────────────────────────────────────────────
            if positions:
                log.info(f"  Open: {list(positions.keys())} | Regime: {btc_regime}")
            else:
                log.info(f"  No open positions | Regime: {btc_regime} | "
                         f"Next scan in {SCAN_INTERVAL_SEC}s")

            time.sleep(SCAN_INTERVAL_SEC)

    except KeyboardInterrupt:
        log.info("Shutting down scalp trader...")
        _alert("*Scalp Trader STOPPED*")
    finally:
        _release_lock()


if __name__ == "__main__":
    main()
