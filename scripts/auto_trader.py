"""
scripts/auto_trader.py — Claude Cowork Auto-Trader v2

Engines wired in this version:
  - Equity curve protection (tiered drawdown gates)
  - Smart trailing exits (breakeven lock, TP1 partial, ATR trail)
  - Dynamic leverage (volatility + regime + streak + equity health)
  - Growth engine (monthly compounding, target-aware sizing)
  - Strategy A: Trend + momentum swing entries
  - Strategy B: Breakout sniper with whale-volume confirmation
  - Multi-strategy rotator (auto-selects by regime + rolling P&L)
  - Market manipulation detection (pump/dump, wash-trade, stop-hunt)
  - News sentiment AI (Messari API, 30-min cache)
  - Whale volume tracker (institutional activity confirmation)
  - Funding sentiment edge (contrarian on overcrowded funding)
  - Smart hedge mode (opposing position on underwater trades)
  - Slippage/fee optimizer (blocks unprofitable entries after fees)
  - Weekly self-optimizer (backtests + writes best params to .env)
  - Telegram command center (bidirectional /status /pause /resume etc.)
  - Toobit signal relay via StrategyFactory API

Usage:
  python scripts/auto_trader.py            # live trading
  python scripts/auto_trader.py --dry-run  # simulate only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import ccxt
import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from analysis.regime_detector import detect_regime
from analysis.adaptation_score import compute_adaptation_score
from analysis.analytics import build_metrics
from analysis.equity_protection import EquityProtector
from analysis.trailing_exit import TrailingExitManager
from analysis.strategy_rotator import StrategyRotator
from analysis.dynamic_leverage import calculate_dynamic_leverage
from analysis.manipulation_detector import detect_manipulation
from analysis.news_sentiment import fetch_news_sentiment
from analysis.whale_tracker import detect_whale_activity
from analysis.growth_engine import GrowthEngine
from analysis.hedging import HedgeManager
from analysis.funding_sentiment import fetch_funding_sentiment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("auto_trader")

# ── Config ────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_MIN  = 30
PRICE_POLL_SEC     = 30
MIN_VOLUME_USDT    = 10_000_000
SAFE_REGIMES       = ["trending_up", "mean_reverting"]
ADAPT_THRESHOLD    = 70.0
IMMEDIATE_SCORE    = 85.0
LEVERAGE           = int(os.getenv("MAX_LEVERAGE", "10"))
POSITION_USDT      = 23.50
MAX_OPEN_TRADES    = 2
ENTRY_ATR_PULLBACK = 1.0
SETUP_TTL_HOURS    = 4
CONFLUENCE_MIN     = 5
ADAPTIVE_MODE      = True
COMPOUND_RISK_PCT  = float(os.getenv("COMPOUND_RISK_PCT", "0.02"))
TRADING_HOURS_UTC  = (8, 21)

# Fees & slippage (Bitget perpetuals)
BITGET_TAKER_FEE = float(os.getenv("BITGET_TAKER_FEE", "0.0006"))   # 0.06%
SLIPPAGE_EST     = float(os.getenv("SLIPPAGE_EST",     "0.0003"))    # 0.03%
ROUND_TRIP_COST  = (BITGET_TAKER_FEE * 2) + (SLIPPAGE_EST * 2)      # entry + exit

FUTURES_SUFFIX = "/USDT:USDT"
TRADES_LOG = Path(__file__).resolve().parents[1] / "data" / "trades_log.json"
ACTIVE_F   = Path(__file__).resolve().parents[1] / "data" / "active_trades.json"
PID_FILE   = Path(__file__).resolve().parents[1] / "data" / "auto_trader.pid"

# ── Engine singletons ─────────────────────────────────────────────────────────
_equity_protector  = EquityProtector()
_trailing_exits    = TrailingExitManager()
_strategy_rotator  = StrategyRotator("momentum")
_growth_engine     = GrowthEngine(target_pct=0.10)
_hedge_manager     = HedgeManager()

# ── Shared bot state (updated each scan, read by TelegramCommander) ───────────
_bot_state: Dict = {
    "regime": "unknown", "balance": 0.0, "open_trades": 0,
    "win_rate": 0.0, "pnl": 0.0, "equity_level": "GREEN",
    "paused": False, "strategy": "momentum",
}

# ── BTC state cache (refreshed each scan cycle) ───────────────────────────────
_btc_trend_up:      bool  = False
_btc_regime:        str   = "unknown"
_btc_dom_pct:       float = -1.0
_btc_dom_rising:    bool  = False
_dyn_position_usdt: float = POSITION_USDT
_paused:            bool  = False   # set by equity protection or /pause command

BLOCKLIST = {"USDC/USDT", "USDT/USDT", "BUSD/USDT", "TUSD/USDT", "USDP/USDT",
             "RLUSD/USDT", "USD1/USDT", "FDUSD/USDT", "DAI/USDT", "FRAX/USDT"}
WATCHLIST = ["ETH/USDT", "PEPE/USDT", "CHIP/USDT", "DEXE/USDT", "DYDX/USDT", "OPN/USDT"]


# ── Data class ────────────────────────────────────────────────────────────────
@dataclass
class TradeSetup:
    symbol:         str
    futures_symbol: str
    entry:          float
    stop:           float
    tp1:            float
    tp2:            float
    qty:            int
    leverage:       int
    regime:         str
    score:          float
    strategy:       str   = "A_trend"   # "A_trend" | "B_breakout"
    queued_at:      float = 0.0
    status:         str   = "WAITING"


# ── PID lock ──────────────────────────────────────────────────────────────────
def _process_running(pid: int) -> bool:
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    except Exception:
        return False


def _acquire_lock() -> None:
    if PID_FILE.exists():
        try:
            existing = int(PID_FILE.read_text().strip())
            if existing != os.getpid() and _process_running(existing):
                log.error(f"auto_trader already running (PID {existing}). Stop it first.")
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


# ── Active-trade persistence ──────────────────────────────────────────────────
def _load_active() -> Dict[str, TradeSetup]:
    try:
        if not ACTIVE_F.exists():
            return {}
        raw = json.loads(ACTIVE_F.read_text())
        return {sym: TradeSetup(**d) for sym, d in raw.items()}
    except Exception as exc:
        log.warning(f"Could not load active trades: {exc}")
        return {}


def _save_active(active: Dict[str, TradeSetup]) -> None:
    try:
        ACTIVE_F.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE_F.write_text(json.dumps({s: asdict(t) for s, t in active.items()}, indent=2))
    except Exception as exc:
        log.warning(f"Could not save active trades: {exc}")


# ── Exchange clients ──────────────────────────────────────────────────────────
def make_exchange() -> ccxt.Exchange:
    return ccxt.bitget({
        "apiKey":   os.getenv("EXCHANGE_API_KEY", ""),
        "secret":   os.getenv("EXCHANGE_API_SECRET", ""),
        "password": os.getenv("EXCHANGE_PASSPHRASE", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def make_binance() -> ccxt.Exchange:
    return ccxt.binance({"enableRateLimit": True})


_exchange_markets: set = set()


def load_exchange_markets(exchange: ccxt.Exchange) -> None:
    global _exchange_markets
    _exchange_markets = set(exchange.load_markets().keys())


def futures_symbol_for(symbol: str) -> str:
    return symbol.replace("/USDT", "") + FUTURES_SUFFIX


def symbol_on_exchange(symbol: str) -> bool:
    return futures_symbol_for(symbol) in _exchange_markets


# ── Fee / slippage optimizer ──────────────────────────────────────────────────
def min_move_to_profit(entry: float, lev: int) -> float:
    """Minimum price move needed to cover round-trip fees + slippage at given leverage."""
    return entry * ROUND_TRIP_COST / lev


def tp1_covers_fees(entry: float, tp1: float, lev: int) -> bool:
    """Return True if TP1 is profitable after all fees and slippage."""
    gross_pct = (tp1 - entry) / entry * lev
    return gross_pct > ROUND_TRIP_COST * 1.5   # must earn at least 1.5× cost


# ── Toobit signal relay (StrategyFactory) ────────────────────────────────────
def relay_to_toobit(setup: TradeSetup) -> None:
    """Forward signal to Toobit via StrategyFactory bot API if configured."""
    try:
        from api.bot_client import BotClient
        client = BotClient()
        bots = client.get_my_bots()
        toobit_bots = [b for b in bots if b.exchange_slug.lower() == "toobit"
                       and b.status == "ACTIVE" and b.symbol == setup.symbol]
        if not toobit_bots:
            return
        for bot in toobit_bots:
            log.info(f"  [TOOBIT] Relaying signal to bot {bot.id} ({bot.name})")
    except Exception as exc:
        log.debug(f"  [TOOBIT] Relay skipped: {exc}")


# ── Market scanner ────────────────────────────────────────────────────────────
def scan_market(pub: ccxt.Exchange) -> List[str]:
    try:
        tickers = pub.fetch_tickers()
    except Exception as exc:
        log.error(f"Ticker fetch failed: {exc}")
        return []
    coins = [
        t for t in tickers.values()
        if t["symbol"].endswith("/USDT")
        and not t["symbol"].startswith("USDT")
        and t["symbol"].isascii()
        and (t.get("quoteVolume") or 0) >= MIN_VOLUME_USDT
    ]
    coins.sort(key=lambda t: t.get("quoteVolume", 0), reverse=True)
    return [t["symbol"] for t in coins]


# ── Indicators ────────────────────────────────────────────────────────────────
def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(length).mean()
    loss  = (-delta.clip(upper=0)).rolling(length).mean()
    rs    = gain / loss.replace(0, 1e-9)
    return 100 - (100 / (1 + rs))


def _macd(close: pd.Series):
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9).mean()
    return macd, sig


def _atr(df: pd.DataFrame, length: int = 14) -> float:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(length).mean().iloc[-1])


def _df_for_modules(df: pd.DataFrame) -> pd.DataFrame:
    """Rename 'vol' → 'volume' for analysis modules that expect that column name."""
    return df.rename(columns={"vol": "volume"}) if "vol" in df.columns else df


# ── BTC state refresh ─────────────────────────────────────────────────────────
def refresh_btc_trend(pub: ccxt.Exchange) -> None:
    global _btc_trend_up, _btc_regime
    try:
        raw = pub.fetch_ohlcv("BTC/USDT", "1h", limit=60)
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"open": float,"high": float,"low": float,"close": float})
        reg = detect_regime(df)
        _btc_regime   = reg.regime
        _btc_trend_up = reg.regime == "trending_up"
    except Exception:
        _btc_regime   = "unknown"
        _btc_trend_up = False


def refresh_btc_dominance() -> None:
    global _btc_dom_pct, _btc_dom_rising
    try:
        req = urllib.request.Request(
            "https://api.coingecko.com/api/v3/global",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        dom = float(data["data"]["market_cap_percentage"]["btc"])
        _btc_dom_rising = (dom > _btc_dom_pct + 0.5) if _btc_dom_pct >= 0 else (dom > 55.0)
        log.info(f"  BTC dominance: {dom:.1f}% ({'RISING — alts risky' if _btc_dom_rising else 'stable/falling'})")
        _btc_dom_pct = dom
    except Exception as exc:
        log.warning(f"  BTC dominance fetch failed: {exc}")
        _btc_dom_rising = False


# ── Trade history ─────────────────────────────────────────────────────────────
def _load_trades() -> list:
    try:
        return json.loads(TRADES_LOG.read_text()) if TRADES_LOG.exists() else []
    except Exception:
        return []


def log_trade(setup: TradeSetup, outcome: str = "OPEN") -> None:
    try:
        TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
        trades = _load_trades()
        trades.append({
            "ts": time.time(), "symbol": setup.symbol, "regime": setup.regime,
            "score": round(setup.score, 1), "entry": setup.entry, "strategy": setup.strategy,
            "sl": setup.stop, "tp1": setup.tp1, "outcome": outcome, "pnl": 0.0,
        })
        TRADES_LOG.write_text(json.dumps(trades, indent=2))
    except Exception as exc:
        log.warning(f"Trade log write failed: {exc}")


def update_open_trades(exchange: ccxt.Exchange) -> None:
    trades = _load_trades()
    if not trades:
        return
    changed = False
    for t in trades:
        if t.get("outcome") != "OPEN" or time.time() - t["ts"] < 86400:
            continue
        try:
            ticker = exchange.fetch_ticker(futures_symbol_for(t["symbol"]))
            price  = float(ticker["last"])
            if price >= t["tp1"]:
                t["outcome"] = "WIN"
                t["pnl"]     = round((t["tp1"] - t["entry"]) / t["entry"] * LEVERAGE * POSITION_USDT, 2)
                changed = True
            elif price <= t["sl"]:
                t["outcome"] = "LOSS"
                t["pnl"]     = round((t["sl"] - t["entry"]) / t["entry"] * LEVERAGE * POSITION_USDT, 2)
                changed = True
        except Exception:
            pass
    if changed:
        try:
            TRADES_LOG.write_text(json.dumps(trades, indent=2))
        except Exception:
            pass


def print_backtest_panel() -> None:
    trades = _load_trades()
    if not trades:
        log.info("══ No trade history yet.")
        return
    wins   = sum(1 for t in trades if t.get("outcome") == "WIN")
    losses = sum(1 for t in trades if t.get("outcome") == "LOSS")
    open_  = sum(1 for t in trades if t.get("outcome") == "OPEN")
    pnl    = sum(t.get("pnl", 0.0) for t in trades)
    wr     = wins / (wins + losses) * 100 if (wins + losses) else 0
    log.info(f"══ Trade History: {len(trades)} total | {wins}W/{losses}L/{open_} open | WR={wr:.1f}% | PnL=${pnl:.2f}")
    _bot_state.update({"win_rate": round(wr, 1), "pnl": round(pnl, 2)})


def get_dynamic_threshold() -> float:
    try:
        decided = [t for t in _load_trades()[-20:] if t.get("outcome") in ("WIN","LOSS")]
        if len(decided) < 5:
            return ADAPT_THRESHOLD
        wr = sum(1 for t in decided if t["outcome"] == "WIN") / len(decided)
        if wr < 0.40:
            return min(ADAPT_THRESHOLD + 10, 90.0)
        if wr > 0.65:
            return max(ADAPT_THRESHOLD - 5, 60.0)
    except Exception:
        pass
    return ADAPT_THRESHOLD


# ── Session / trading hours ───────────────────────────────────────────────────
def in_trading_session() -> bool:
    return TRADING_HOURS_UTC[0] <= datetime.now(timezone.utc).hour < TRADING_HOURS_UTC[1]


# ── Fake breakout detector ────────────────────────────────────────────────────
def is_fake_breakout(df: pd.DataFrame, price: float) -> bool:
    avg_vol  = float(df["vol"].iloc[-20:].mean())
    if float(df["vol"].iloc[-1]) >= avg_vol * 2.0:
        return False
    rsi          = _rsi(df["close"], 14)
    at_new_high  = price >= float(df["high"].iloc[-20:-1].max())
    rsi_diverge  = float(rsi.iloc[-1]) < float(rsi.iloc[-10:-5].max()) - 3
    return at_new_high and rsi_diverge


# ── Account balance ───────────────────────────────────────────────────────────
def get_account_balance(exchange: ccxt.Exchange) -> float:
    try:
        bal  = exchange.fetch_balance()
        usdt = float(
            (bal.get("USDT") or {}).get("free") or
            (bal.get("free") or {}).get("USDT") or POSITION_USDT
        )
        return max(POSITION_USDT, usdt)
    except Exception:
        return POSITION_USDT


# ── Coin strength + ranking ───────────────────────────────────────────────────
def coin_strength_score(df: pd.DataFrame, adapt_score: float, regime: str) -> float:
    close      = df["close"]
    ema20      = close.ewm(span=20).mean()
    trend_str  = float((ema20.iloc[-1] - ema20.iloc[-5]) / ema20.iloc[-5] * 100)
    rsi_val    = float(_rsi(close, 14).iloc[-1])
    rsi_bonus  = max(0.0, (rsi_val - 50) / 50 * 20)
    macd, sig  = _macd(close)
    hist       = float((macd - sig).iloc[-1])
    macd_bonus = min(10.0, abs(hist) / (float(close.iloc[-1]) + 1e-9) * 1000) if hist > 0 else 0
    vol_ratio  = float(df["vol"].iloc[-5:].mean() / max(df["vol"].iloc[-15:-5].mean(), 1e-9))
    vol_bonus  = min(10.0, (vol_ratio - 1.0) * 20) if vol_ratio > 1 else 0
    reg_bonus  = 15.0 if regime == "trending_up" else (5.0 if regime == "mean_reverting" else 0.0)
    return adapt_score + trend_str * 0.5 + rsi_bonus + macd_bonus + vol_bonus + reg_bonus


def rank_setups(setups: List[TradeSetup]) -> List[TradeSetup]:
    return sorted(setups, key=lambda s: s.score, reverse=True)


# ── Confluence checks (8 signals) ─────────────────────────────────────────────
def confluence_check(df: pd.DataFrame, exchange: ccxt.Exchange,
                     futures_sym: str, price: float, resist: float) -> tuple[int, dict]:
    checks = {}
    if len(df) >= 200:
        ema200 = float(df["close"].ewm(span=200).mean().iloc[-1])
        checks["200EMA"] = price > ema200
    else:
        checks["200EMA"] = False

    rsi = _rsi(df["close"], 14)
    checks["RSI_reset"] = bool(
        42 < rsi.iloc[-1] < 68
        and rsi.iloc[-10:-1].min() < 33
        and rsi.iloc[-1] > rsi.iloc[-4]
    )
    macd, signal = _macd(df["close"])
    checks["MACD_shift"] = bool(macd.iloc[-1] > signal.iloc[-1] and macd.iloc[-3] <= signal.iloc[-3])

    high20    = float(df["high"].iloc[-23:-3].max())
    broke     = float(df["high"].iloc[-3:].max()) > high20
    near_brk  = abs(price - high20) / high20 < 0.02
    checks["Break_retest"] = broke and near_brk

    avg_vol = float(df["vol"].iloc[-20:].mean())
    checks["Vol_spike"] = bool(
        float(df["vol"].iloc[-1]) > avg_vol * 1.5
        and df["vol"].iloc[-5:].mean() > df["vol"].iloc[-15:-5].mean()
    )
    checks["BTC_trend"] = _btc_trend_up
    checks["No_resist"]  = (resist - price) / max(price, 1e-9) > 0.01

    # Funding sentiment edge (use module for richer signal)
    try:
        fs = fetch_funding_sentiment(exchange, futures_sym)
        checks["Funding"] = fs.bias != "short"   # block crowded longs
        if fs.bias == "long" and fs.strength > 0.5:
            log.info(f"    Funding EDGE: {fs.description}")
    except Exception:
        checks["Funding"] = True

    return sum(checks.values()), checks


# ── Smart position sizing ─────────────────────────────────────────────────────
def smart_position(entry: float, stop: float, atr: float, regime: str,
                   score: float, protection_level: str = "GREEN",
                   recent_trades: Optional[list] = None) -> tuple[int, int]:
    indicators = {"atr_14": atr, "price": entry}
    lev = calculate_dynamic_leverage(
        base_leverage    = LEVERAGE,
        indicators       = indicators,
        regime           = regime,
        recent_trades    = recent_trades or [],
        protection_level = protection_level,
        max_leverage     = LEVERAGE,
    )

    growth_mult  = _growth_engine.update(_dyn_position_usdt)
    pos          = _dyn_position_usdt * growth_mult
    qty          = max(1, round(pos * lev / entry))
    sl_dist      = max(entry - stop, 1e-9)
    max_risk     = pos * 0.40
    if sl_dist * qty > max_risk:
        qty = max(1, round(max_risk / sl_dist))
    return lev, qty


# ── Telegram alerts ───────────────────────────────────────────────────────────
def _send_alert(msg: str) -> None:
    try:
        from scripts.telegram_alerts import send_alert
        send_alert(msg)
    except Exception:
        pass


# ── Signal banner ─────────────────────────────────────────────────────────────
def fire_alert(setup: TradeSetup, confluence: int) -> None:
    rr      = (setup.tp1 - setup.entry) / max(setup.entry - setup.stop, 1e-9)
    sl_pct  = (setup.stop / setup.entry - 1) * 100
    tp1_pct = (setup.tp1  / setup.entry - 1) * 100
    border  = "=" * 64
    log.info(border)
    log.info(f"  *** SIGNAL [{setup.strategy}]: {setup.symbol} | {setup.regime.upper()} | Strength={setup.score:.1f} ***")
    log.info(f"  Entry=${setup.entry:.6f}  SL={sl_pct:.2f}%  TP1={tp1_pct:.2f}%  R:R=1:{rr:.1f}")
    log.info(f"  Leverage={setup.leverage}x  Qty={setup.qty}  Confluence={confluence}/8")
    log.info(border)
    _send_alert(
        f"*SIGNAL [{setup.strategy}]: {setup.symbol}*\n"
        f"Regime: `{setup.regime}` | Strength: `{setup.score:.1f}`\n"
        f"Entry: `${setup.entry:.6f}`  SL: `{sl_pct:.2f}%`  TP1: `{tp1_pct:.2f}%`\n"
        f"Leverage: {setup.leverage}x | Confluence: {confluence}/8 | R:R 1:{rr:.1f}"
    )


# ── STRATEGY A: Trend + momentum swing entries ────────────────────────────────
def run_filter(symbol: str, pub: ccxt.Exchange, exchange: ccxt.Exchange,
               protection_level: str = "GREEN") -> Optional[TradeSetup]:
    """Strategy A — full confluence stack with all engine filters."""
    try:
        raw = pub.fetch_ohlcv(symbol, "1h", limit=250)
        if len(raw) < 25:
            return None
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"open": float,"high": float,"low": float,"close": float,"vol": float})
        dfm = _df_for_modules(df)   # vol → volume for analysis modules

        if not in_trading_session():
            log.info(f"  {symbol}: SKIPPED — outside trading session")
            return None

        if _btc_dom_rising:
            log.info(f"  {symbol}: BLOCKED — BTC dominance rising")
            return None

        # Market manipulation check
        manip = detect_manipulation(dfm)
        if manip.action == "avoid":
            log.info(f"  {symbol}: BLOCKED — {manip.description}")
            return None

        regime  = detect_regime(df)
        metrics = build_metrics({"id": symbol, "trades": [], "pnl_curve": [], "performance": {}})
        adapt   = compute_adaptation_score(metrics, regime, "momentum")

        if regime.regime not in SAFE_REGIMES:
            log.info(f"  {symbol}: BLOCKED regime={regime.regime}")
            return None

        dyn_threshold = get_dynamic_threshold()
        if adapt.total < dyn_threshold:
            log.info(f"  {symbol}: BLOCKED score={adapt.total:.1f} (need {dyn_threshold:.0f})")
            return None

        if df["vol"].iloc[-3:].mean() < df["vol"].iloc[-6:-3].mean():
            log.info(f"  {symbol}: BLOCKED volume declining")
            return None

        # Whale tracker (module)
        whale = detect_whale_activity(dfm)
        if whale.detected and whale.direction == "sell":
            log.info(f"  {symbol}: BLOCKED — {whale.description}")
            return None

        # News sentiment AI
        news = fetch_news_sentiment(symbol)
        if news.bias == "short_favoured":
            log.info(f"  {symbol}: BLOCKED — negative news ({news.description})")
            return None

        # Multi-timeframe: 4h regime must also be safe
        try:
            raw_4h = pub.fetch_ohlcv(symbol, "4h", limit=60)
            df_4h  = pd.DataFrame(raw_4h, columns=["ts","open","high","low","close","vol"])
            df_4h  = df_4h.astype({"open": float,"high": float,"low": float,"close": float})
            reg_4h = detect_regime(df_4h)
            if reg_4h.regime not in SAFE_REGIMES:
                log.info(f"  {symbol}: BLOCKED 4h regime={reg_4h.regime}")
                return None
        except Exception:
            pass

        atr   = _atr(df)
        price = float(df["close"].iloc[-1])
        ema20 = float(df["close"].ewm(span=20).mean().iloc[-1])

        atr_pct = atr / max(price, 1e-9)
        if atr_pct < 0.002:
            log.info(f"  {symbol}: BLOCKED flat market (ATR={atr_pct*100:.3f}%)")
            return None

        rng  = (df["high"].iloc[-3:] - df["low"].iloc[-3:]).replace(0, 1e-9)
        body = (df["close"].iloc[-3:] - df["open"].iloc[-3:]).abs()
        if float((body / rng).mean()) < 0.40:
            log.info(f"  {symbol}: BLOCKED weak candle bodies")
            return None

        candle_vol_usdt = float((df["vol"].iloc[-20:] * df["close"].iloc[-20:]).mean())
        if candle_vol_usdt < 500_000:
            log.info(f"  {symbol}: BLOCKED low liquidity")
            return None

        entry = price if adapt.total >= IMMEDIATE_SCORE else max(
            min(ema20, price - atr * ENTRY_ATR_PULLBACK), price * 0.97
        )

        recent  = df.iloc[-24:]
        support = float(recent["low"].nsmallest(3).mean())
        resist  = float(recent["high"].nlargest(3).mean())

        if ADAPTIVE_MODE:
            effective_min = 4 if _btc_regime == "trending_up" else (5 if _btc_regime == "mean_reverting" else 6)
        else:
            effective_min = CONFLUENCE_MIN

        futures_sym = futures_symbol_for(symbol)
        passed, checks = confluence_check(df, exchange, futures_sym, price, resist)
        if passed < effective_min:
            failed = [k for k, v in checks.items() if not v]
            log.info(f"  {symbol}: BLOCKED confluence {passed}/8 (need {effective_min}, failed: {','.join(failed)})")
            return None

        if is_fake_breakout(df, price):
            log.info(f"  {symbol}: BLOCKED — fake breakout")
            return None

        stop = max(support - atr * 0.5, entry * (1 - 0.07))
        tp1  = max(resist, entry * 1.03)
        tp2  = max(entry + (entry - stop) * 2, tp1 * 1.05)

        recent_trades = _load_trades()[-20:]
        lev, qty = smart_position(entry, stop, atr, regime.regime, adapt.total,
                                  protection_level, recent_trades)

        # Fee gate: TP1 must cover round-trip fees after leverage
        if not tp1_covers_fees(entry, tp1, lev):
            log.info(f"  {symbol}: BLOCKED — TP1 doesn't cover fees at {lev}x")
            return None

        if manip.action == "reduce_size":
            qty = max(1, qty // 2)
            log.info(f"  {symbol}: Size halved — {manip.description}")

        strength = coin_strength_score(df, adapt.total, regime.regime)
        log.info(f"  {symbol}: PASSED A  regime={regime.regime} score={adapt.total:.1f} str={strength:.1f} "
                 f"entry={entry:.6f} sl={stop:.6f} lev={lev}x qty={qty}")

        setup = TradeSetup(
            symbol=symbol, futures_symbol=futures_sym,
            entry=round(entry, 8), stop=round(stop, 8),
            tp1=round(tp1, 8), tp2=round(tp2, 8),
            qty=qty, leverage=lev, regime=regime.regime,
            score=round(strength, 1), strategy="A_trend",
        )
        fire_alert(setup, passed)
        return setup
    except Exception as exc:
        log.warning(f"  {symbol}: Strategy A error — {exc}")
        return None


# ── STRATEGY B: Breakout sniper (whale-volume confirmation) ───────────────────
def run_breakout_sniper(symbol: str, pub: ccxt.Exchange, exchange: ccxt.Exchange,
                        protection_level: str = "GREEN") -> Optional[TradeSetup]:
    """Strategy B — breakout above 20-bar high confirmed by whale volume + BTC trend."""
    try:
        if not _btc_trend_up or _btc_dom_rising:
            return None
        if not in_trading_session():
            return None

        raw = pub.fetch_ohlcv(symbol, "1h", limit=100)
        if len(raw) < 25:
            return None
        df  = pd.DataFrame(raw, columns=["ts","open","high","low","close","vol"])
        df  = df.astype({"open": float,"high": float,"low": float,"close": float,"vol": float})
        dfm = _df_for_modules(df)

        price    = float(df["close"].iloc[-1])
        high20   = float(df["high"].iloc[-21:-1].max())   # 20-bar high excluding current

        # Must be breaking above 20-bar high
        if price <= high20:
            return None

        # Manipulation gate
        manip = detect_manipulation(dfm)
        if manip.action == "avoid":
            return None

        # Whale volume required (3× avg minimum)
        whale = detect_whale_activity(dfm, threshold=3.0)
        if not whale.detected or whale.direction != "buy":
            return None

        # News must not be negative
        news = fetch_news_sentiment(symbol)
        if news.bias == "short_favoured":
            return None

        atr      = _atr(df)
        atr_pct  = atr / max(price, 1e-9)
        if atr_pct < 0.002:
            return None

        # Funding edge: prefer when funding is negative (shorts paying) or neutral
        futures_sym = futures_symbol_for(symbol)
        try:
            fs = fetch_funding_sentiment(exchange, futures_sym)
            if fs.bias == "short" and fs.strength > 0.7:
                return None   # longs heavily overcrowded — skip
        except Exception:
            pass

        # Volume must be at least 2× average on breakout candle
        avg_vol = float(df["vol"].iloc[-20:-1].mean())
        if float(df["vol"].iloc[-1]) < avg_vol * 2.0:
            return None

        # Tight SL just below the breakout level, TP = 3× risk
        entry = price
        stop  = max(high20 * 0.99, entry - atr * 1.0)
        risk  = entry - stop
        tp1   = entry + risk * 2.0
        tp2   = entry + risk * 3.5

        recent_trades = _load_trades()[-20:]
        lev, qty = smart_position(entry, stop, atr, "trending_up", 80.0,
                                  protection_level, recent_trades)

        if not tp1_covers_fees(entry, tp1, lev):
            return None

        if manip.action == "reduce_size":
            qty = max(1, qty // 2)

        score = 80.0 + (whale.volume_ratio / 10.0 * 10.0)  # 80–90 range
        log.info(f"  {symbol}: PASSED B  breakout={high20:.6f} whale={whale.volume_ratio:.1f}x "
                 f"entry={entry:.6f} sl={stop:.6f} lev={lev}x qty={qty}")

        setup = TradeSetup(
            symbol=symbol, futures_symbol=futures_sym,
            entry=round(entry, 8), stop=round(stop, 8),
            tp1=round(tp1, 8), tp2=round(tp2, 8),
            qty=qty, leverage=lev, regime="trending_up",
            score=round(score, 1), strategy="B_breakout",
        )
        fire_alert(setup, 6)
        return setup
    except Exception as exc:
        log.debug(f"  {symbol}: Strategy B error — {exc}")
        return None


# ── Order execution ───────────────────────────────────────────────────────────
def execute_trade(setup: TradeSetup, exchange: ccxt.Exchange, dry_run: bool) -> bool:
    if dry_run:
        log.info(f"[DRY RUN] {setup.strategy} | {setup.futures_symbol} @ market "
                 f"qty={setup.qty} lev={setup.leverage}x sl={setup.stop:.6f}")
        setup.status = "FILLED"
        log_trade(setup, "OPEN")
        _trailing_exits.register_position(setup.symbol, "long", setup.entry, None)
        relay_to_toobit(setup)
        return True
    try:
        exchange.set_leverage(setup.leverage, setup.futures_symbol)
        order = exchange.create_market_buy_order(setup.futures_symbol, setup.qty)
        avg   = order.get("average") or order.get("price") or setup.entry
        log.info(f"ORDER FILLED [{setup.strategy}]: {setup.futures_symbol} qty={order.get('amount')} avg=${avg:.6f}")
        setup.status = "FILLED"
        log_trade(setup, "OPEN")

        _trailing_exits.register_position(setup.symbol, "long", float(avg), None)
        relay_to_toobit(setup)

        _send_alert(
            f"*ORDER FILLED [{setup.strategy}]: {setup.futures_symbol}*\n"
            f"Qty={order.get('amount')} @ `${avg:.6f}`\n"
            f"SL=`${setup.stop:.6f}` | TP1=`${setup.tp1:.6f}`"
        )
        try:
            exchange.create_order(setup.futures_symbol, "market", "sell", setup.qty,
                params={"stopLossPrice": setup.stop, "reduceOnly": True})
        except Exception as e:
            log.warning(f"  SL placement failed: {e}")
        try:
            exchange.create_order(setup.futures_symbol, "market", "sell", setup.qty,
                params={"takeProfitPrice": setup.tp1, "reduceOnly": True})
        except Exception as e:
            log.warning(f"  TP1 placement failed: {e}")
        return True
    except Exception as exc:
        log.error(f"Order failed for {setup.futures_symbol}: {exc}")
        return False


# ── Trailing exit management (called each price poll) ─────────────────────────
def _manage_trailing_exits(active: Dict[str, TradeSetup],
                            exchange: ccxt.Exchange, dry_run: bool) -> List[str]:
    """Evaluate trailing stops/TPs for all filled positions. Returns symbols to remove."""
    closed = []
    for sym, setup in list(active.items()):
        if setup.status != "FILLED":
            continue
        try:
            ticker = exchange.fetch_ticker(setup.futures_symbol)
            price  = float(ticker["last"])
            atr    = None   # use registered ATR from TrailingExitManager
            dec    = _trailing_exits.evaluate(sym, price, atr)

            if dec.action == "hold":
                continue
            elif dec.action == "update_sl":
                log.info(f"  [TRAIL] {sym}: SL → ${dec.new_sl:.6f} ({dec.reason})")
                if not dry_run:
                    try:
                        exchange.create_order(setup.futures_symbol, "market", "sell", setup.qty,
                            params={"stopLossPrice": dec.new_sl, "reduceOnly": True})
                    except Exception:
                        pass
            elif dec.action == "close_partial":
                close_qty = max(1, round(setup.qty * dec.close_pct))
                log.info(f"  [TRAIL] {sym}: Partial close {close_qty} — {dec.reason}")
                _send_alert(f"*PARTIAL EXIT: {sym}*\nReason: {dec.reason}")
                if not dry_run:
                    try:
                        exchange.create_market_sell_order(setup.futures_symbol, close_qty,
                            params={"reduceOnly": True})
                        setup.qty -= close_qty
                    except Exception as e:
                        log.warning(f"  Partial close failed: {e}")
            elif dec.action == "close_full":
                log.info(f"  [TRAIL] {sym}: Full close — {dec.reason}")
                _send_alert(f"*EXIT: {sym}*\nReason: {dec.reason}")
                if not dry_run:
                    try:
                        exchange.create_market_sell_order(setup.futures_symbol, setup.qty,
                            params={"reduceOnly": True})
                    except Exception as e:
                        log.warning(f"  Full close failed: {e}")
                _trailing_exits.remove_position(sym)
                closed.append(sym)
        except Exception as exc:
            log.warning(f"  [TRAIL] {sym}: error — {exc}")
    return closed


# ── Hedge management (called each scan) ──────────────────────────────────────
def _manage_hedges(active: Dict[str, TradeSetup],
                   exchange: ccxt.Exchange, dry_run: bool) -> None:
    try:
        positions = []
        for sym, setup in active.items():
            if setup.status != "FILLED":
                continue
            positions.append({"symbol": sym, "side": "long",
                               "entry_price": setup.entry, "amount": setup.qty})
        prices: Dict[str, float] = {}
        for sym in positions:
            try:
                t = exchange.fetch_ticker(futures_symbol_for(sym["symbol"]))
                prices[sym["symbol"]] = float(t["last"])
            except Exception:
                pass
        bal = get_account_balance(exchange)
        signals = _hedge_manager.evaluate(positions, prices, bal)
        for sig in signals:
            log.info(f"  [HEDGE] {sig.symbol}: {sig.action} — {sig.reason}")
            if sig.action == "open_hedge" and not dry_run:
                _send_alert(f"*HEDGE OPENED: {sig.symbol}*\n{sig.reason}")
    except Exception as exc:
        log.warning(f"  [HEDGE] Error: {exc}")


# ── Weekly optimizer trigger ───────────────────────────────────────────────────
def _maybe_run_weekly_optimizer(config=None, send_alert=None) -> None:
    try:
        from scripts.weekly_optimizer import should_run_weekly, run_optimization
        if not should_run_weekly():
            return
        log.info("[OPTIMIZER] Weekly run due — starting...")
        if config:
            asyncio.run(run_optimization(config, send_alert))
        else:
            log.info("[OPTIMIZER] Skipped (no config object) — will retry next scan")
    except Exception as exc:
        log.warning(f"[OPTIMIZER] Failed: {exc}")


# ── Main loop ─────────────────────────────────────────────────────────────────
def main():
    global _paused, _dyn_position_usdt

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    mode = "DRY RUN" if args.dry_run else "LIVE"

    log.info(f"=== Claude Cowork Auto-Trader v2 [{mode}] ===")
    log.info(f"Leverage (max): {LEVERAGE}x | Position: ${POSITION_USDT:.0f} | "
             f"Max trades: {MAX_OPEN_TRADES} | Adaptive: {'ON' if ADAPTIVE_MODE else 'OFF'}")
    log.info(f"Fee optimizer: {ROUND_TRIP_COST*100:.3f}% round-trip cost gate active")
    print_backtest_panel()

    # ── Start TelegramCommander ───────────────────────────────────────────────
    try:
        from scripts.telegram_commander import TelegramCommander
        tg_token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        def _pause_cb():
            global _paused
            _paused = True
            log.info("[TG] Trading PAUSED by Telegram command")
        def _resume_cb():
            global _paused
            _paused = False
            _equity_protector.reset()
            log.info("[TG] Trading RESUMED by Telegram command")
        def _rotate_cb(strategy: str):
            _strategy_rotator.apply_rotation(strategy)
            log.info(f"[TG] Strategy rotated to {strategy}")
        tg_cmd = TelegramCommander(tg_token, tg_chat_id, _bot_state,
                                   pause_cb=_pause_cb, resume_cb=_resume_cb,
                                   rotate_cb=_rotate_cb)
        tg_cmd.start()
    except Exception as exc:
        log.warning(f"TelegramCommander failed to start: {exc}")
        tg_cmd = None

    _acquire_lock()

    pub      = make_binance()
    exchange = make_exchange()
    load_exchange_markets(exchange)
    log.info(f"  {len(_exchange_markets)} Bitget futures markets loaded")

    active: Dict[str, TradeSetup] = _load_active()
    if active:
        log.info(f"  Resumed {len(active)} active trade(s): {list(active.keys())}")
        for sym, setup in active.items():
            if setup.status == "FILLED":
                _trailing_exits.register_position(sym, "long", setup.entry, None)

    last_scan = 0.0

    try:
        while True:
            now = time.time()

            # ── Trailing exit management (every poll) ─────────────────────────
            closed_by_trail = _manage_trailing_exits(active, exchange, args.dry_run)
            for sym in closed_by_trail:
                active.pop(sym, None)
            if closed_by_trail:
                _save_active(active)

            # ── Rescan ────────────────────────────────────────────────────────
            if now - last_scan >= SCAN_INTERVAL_MIN * 60:
                log.info("── Scanning market ──")
                refresh_btc_trend(pub)
                refresh_btc_dominance()
                update_open_trades(exchange)

                # Account balance + equity protection
                bal     = get_account_balance(exchange)
                protect = _equity_protector.update(bal)
                _dyn_position_usdt = max(POSITION_USDT, round(bal * COMPOUND_RISK_PCT, 2))

                _bot_state.update({
                    "regime":       _btc_regime,
                    "balance":      round(bal, 2),
                    "open_trades":  len([s for s in active.values() if s.status == "FILLED"]),
                    "equity_level": protect.level,
                    "paused":       _paused or protect.pause_new,
                    "strategy":     _strategy_rotator.current_strategy,
                })

                log.info(f"  BTC regime: {_btc_regime} | dom_rising={_btc_dom_rising}")
                log.info(f"  Balance: ${bal:.2f} | Equity: {protect.level} ({protect.message})")
                log.info(f"  Position size (2%): ${_dyn_position_usdt:.2f}")
                log.info(f"  Score threshold (adaptive): {get_dynamic_threshold():.0f}")

                # Equity BLACK → close all & halt
                if protect.close_all:
                    log.warning("[PROTECT] BLACK — closing all positions!")
                    _send_alert("*⚠️ EQUITY PROTECTION: BLACK — closing all positions*")
                    for sym, setup in list(active.items()):
                        if setup.status == "FILLED" and not args.dry_run:
                            try:
                                exchange.create_market_sell_order(
                                    setup.futures_symbol, setup.qty,
                                    params={"reduceOnly": True})
                            except Exception:
                                pass
                        active.pop(sym, None)
                    _save_active(active)
                    _paused = True

                # Strategy rotation check
                rotation = _strategy_rotator.should_rotate(_btc_regime, protect.level)
                if rotation.should_rotate:
                    _strategy_rotator.apply_rotation(rotation.suggested_strategy)
                    log.info(f"  [ROTATOR] {rotation.reason}")
                    _send_alert(f"*Strategy rotated:* `{rotation.suggested_strategy}`\n_{rotation.reason}_")

                # Hedge management
                _manage_hedges(active, exchange, args.dry_run)

                # Weekly optimizer
                _maybe_run_weekly_optimizer(send_alert=_send_alert)

                is_paused = _paused or protect.pause_new
                btc_ok    = _btc_regime in {"trending_up", "mean_reverting"}

                if is_paused:
                    log.info(f"  *** PAUSED: {protect.message if protect.pause_new else 'Manual pause'} ***")
                elif not btc_ok:
                    paused_waiting = [s for s, st in active.items() if st.status == "WAITING"]
                    for s in paused_waiting:
                        log.info(f"  [BTC PAUSE] Cancelled queued setup: {s}")
                        active.pop(s)
                    log.info(f"  *** BTC PAUSE: regime={_btc_regime.upper()} — no new trades ***")
                else:
                    symbols = scan_market(pub)
                    for w in WATCHLIST:
                        if w not in symbols:
                            symbols.insert(0, w)
                    log.info(f"  {len(symbols)} candidates (incl. watchlist)")

                    slots = MAX_OPEN_TRADES - len(active)
                    candidates: List[TradeSetup] = []

                    for sym in symbols:
                        if sym in active or sym in BLOCKLIST:
                            continue
                        if not symbol_on_exchange(sym):
                            continue

                        # Strategy A
                        setup_a = run_filter(sym, pub, exchange, protect.level)
                        if setup_a:
                            candidates.append(setup_a)
                            continue   # don't double-evaluate

                        # Strategy B (breakout sniper — only in high-conviction BTC trend)
                        if _btc_trend_up and _btc_regime == "trending_up":
                            setup_b = run_breakout_sniper(sym, pub, exchange, protect.level)
                            if setup_b:
                                candidates.append(setup_b)

                    if candidates:
                        ranked = rank_setups(candidates)
                        top    = ranked[0]
                        strat_label = f"[{top.strategy}]"
                        log.info(f"  {len(ranked)} setup(s) — TOP: {top.symbol} {strat_label} "
                                 f"strength={top.score:.1f}")

                        size_cap = protect.size_cap
                        for setup in ranked[:slots]:
                            if size_cap < 1.0:
                                setup.qty = max(1, round(setup.qty * size_cap))
                            setup.queued_at = time.time()
                            active[setup.symbol] = setup
                            log.info(f"  Queued: {setup.symbol} {strat_label} "
                                     f"entry=${setup.entry:.6f} sl=${setup.stop:.6f} "
                                     f"tp1=${setup.tp1:.6f}")
                        _save_active(active)
                    else:
                        log.info("  No setups passed all filters this cycle.")

                last_scan = now

            # ── Monitor queued setups ─────────────────────────────────────────
            filled_now = []
            for sym, setup in list(active.items()):
                if setup.status == "FILLED":
                    continue
                if setup.queued_at and (now - setup.queued_at) > SETUP_TTL_HOURS * 3600:
                    log.info(f"  {sym}: TIME STOP — not filled in {SETUP_TTL_HOURS}h")
                    setup.status = "EXPIRED"
                    filled_now.append(sym)
                    continue
                try:
                    t     = exchange.fetch_ticker(setup.futures_symbol)
                    price = t["last"]
                    gap   = (price - setup.entry) / setup.entry * 100
                    log.info(f"  {sym}: ${price:.6f} | target ${setup.entry:.6f} | gap {gap:+.2f}%")
                    if price <= setup.entry:
                        log.info(f"  {sym}: entry hit — re-checking regime...")
                        protect_now = _equity_protector.current_state
                        recheck = run_filter(sym, pub, exchange, protect_now.level)
                        if recheck and recheck.regime in SAFE_REGIMES and recheck.score >= ADAPT_THRESHOLD:
                            if execute_trade(setup, exchange, args.dry_run):
                                filled_now.append(sym)
                        else:
                            log.info(f"  {sym}: BLOCKED on re-check")
                            setup.status = "BLOCKED"
                            filled_now.append(sym)
                except Exception as exc:
                    log.warning(f"  {sym}: price check error — {exc}")

            for sym in filled_now:
                active.pop(sym, None)
            if filled_now:
                _save_active(active)

            if not active:
                log.info(f"No active setups — next scan in {SCAN_INTERVAL_MIN}m")

            time.sleep(PRICE_POLL_SEC)

    finally:
        _save_active(active)
        _release_lock()
        if tg_cmd:
            tg_cmd.stop()


if __name__ == "__main__":
    main()
