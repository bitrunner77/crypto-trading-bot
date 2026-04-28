"""
scripts/exit_manager.py — Auto exit manager for open Toobit futures positions.

Exit rules per position:
  - Stop Loss  : close 100% if price hits SL
  - Target 1   : close 50% at TP1, move SL to breakeven
  - Target 2   : close remaining 50% at TP2

Usage:
  python scripts/exit_manager.py            # live
  python scripts/exit_manager.py --dry-run  # simulate
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

import ccxt
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("exit_manager")

POLL_SEC       = 20
TRAIL_PCT      = 0.03   # trail SL at 3% below price after TP1
TRAIL_TP_PCT   = 0.06   # trailing TP2 stays 6% ahead of price (lets winners run)
BE_TRIGGER_PCT = 0.02   # move SL to breakeven when trade is +2% in profit

@dataclass
class ExitPlan:
    symbol: str
    side: str          # long | short
    entry: float
    sl: float
    tp1: float
    tp2: float
    size: float        # total contracts
    tp1_hit: bool = False
    be_moved: bool = False   # SL moved to breakeven after TP1

# ── Known exit plans (populated from open positions + auto_trader levels) ─────
# These are updated dynamically from open positions; SL/TP derived from ATR rules
MANUAL_PLANS: Dict[str, ExitPlan] = {
    # Add manually opened positions here as needed
}

SYNC_BLOCKLIST = {"USDC/USDT:USDT", "USDT/USDT:USDT", "BUSD/USDT:USDT",
                  "TUSD/USDT:USDT", "FDUSD/USDT:USDT", "DAI/USDT:USDT"}


def make_exchange() -> ccxt.Exchange:
    return ccxt.bitget({
        "apiKey":   os.getenv("EXCHANGE_API_KEY", ""),
        "secret":   os.getenv("EXCHANGE_API_SECRET", ""),
        "password": os.getenv("EXCHANGE_PASSPHRASE", ""),
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def close_position(exchange: ccxt.Exchange, plan: ExitPlan, qty: float,
                   reason: str, dry_run: bool) -> bool:
    side = "sell" if plan.side == "long" else "buy"
    if dry_run:
        log.info(f"[DRY RUN] Would {side} {qty} {plan.symbol} — {reason}")
        return True
    try:
        order = exchange.create_market_order(plan.symbol, side, qty, params={"reduceOnly": True})
        avg = order.get("average") or order.get("price", 0)
        log.info(f"EXIT {reason}: {plan.symbol} {side} {qty} @ ${avg:.6f} id={order.get('id')}")
        try:
            from scripts.telegram_alerts import send_alert
            pnl = (avg - plan.entry) * qty * (1 if plan.side == "long" else -1)
            send_alert(
                f"*EXIT — {reason}*\n"
                f"{plan.symbol} | {qty} contracts @ `${avg:.6f}`\n"
                f"PnL~`${pnl:.2f}`"
            )
        except Exception:
            pass
        return True
    except Exception as exc:
        log.error(f"Exit order failed for {plan.symbol}: {exc}")
        return False


def sync_positions(exchange: ccxt.Exchange, plans: Dict[str, ExitPlan]) -> None:
    """Add any new open positions not already in plans using ATR-based levels."""
    try:
        positions = exchange.fetch_positions()
        for p in positions:
            sym = p["symbol"]
            size = float(p.get("contracts") or 0)
            if size <= 0 or sym in plans or sym in SYNC_BLOCKLIST:
                continue
            entry = float(p.get("entryPrice") or 0)
            side = p.get("side", "long")
            # SL at 7% — safely above 10x liquidation (~10% from entry)
            if side == "long":
                sl  = entry * 0.93
                tp1 = entry * 1.10
                tp2 = entry * 1.20
            else:
                sl  = entry * 1.07
                tp1 = entry * 0.90
                tp2 = entry * 0.80
            plans[sym] = ExitPlan(symbol=sym, side=side, entry=entry,
                                  sl=sl, tp1=tp1, tp2=tp2, size=size)
            log.info(f"  Auto-registered {sym} {side} entry={entry:.6f} sl={sl:.6f} tp1={tp1:.6f} tp2={tp2:.6f}")
    except Exception as exc:
        log.warning(f"Position sync error: {exc}")


def check_exit(price: float, plan: ExitPlan, exchange: ccxt.Exchange, dry_run: bool) -> bool:
    """Evaluate exit conditions. Returns True if position fully closed."""
    if plan.side == "long":
        # ── Stop loss ──────────────────────────────────────────────────────────
        if price <= plan.sl:
            log.info(f"  [SL HIT] {plan.symbol}: ${price:.6f} <= ${plan.sl:.6f}")
            close_position(exchange, plan, plan.size, "STOP LOSS", dry_run)
            return True

        # ── Early breakeven — protect capital at +2% before TP1 ───────────────
        if not plan.tp1_hit and not plan.be_moved and price >= plan.entry * (1 + BE_TRIGGER_PCT):
            plan.sl = plan.entry
            plan.be_moved = True
            log.info(f"  [BE] {plan.symbol}: SL locked to ${plan.entry:.6f} (+{BE_TRIGGER_PCT*100:.0f}% trigger)")

        # ── TP1 — close 50% ────────────────────────────────────────────────────
        if not plan.tp1_hit and price >= plan.tp1:
            half = round(plan.size * 0.5, 2)
            log.info(f"  [TP1] {plan.symbol}: ${price:.6f} — closing {half} contracts (50%)")
            if close_position(exchange, plan, half, "TP1 (50%)", dry_run):
                plan.tp1_hit  = True
                plan.size    -= half
                plan.sl       = plan.entry
                plan.be_moved = True
                log.info(f"    SL → breakeven ${plan.entry:.6f} | trailing profit mode ON")

        # ── Trailing SL + dynamic TP2 (trailing profit mode) ──────────────────
        if plan.tp1_hit:
            new_sl = max(plan.entry, price * (1 - TRAIL_PCT))
            if new_sl > plan.sl:
                plan.sl = new_sl
                log.info(f"    Trail SL → ${plan.sl:.6f}")
            new_tp2 = price * (1 + TRAIL_TP_PCT)   # extend TP2 as price climbs
            if new_tp2 > plan.tp2:
                plan.tp2 = new_tp2
                log.info(f"    Trail TP2 → ${plan.tp2:.6f}")

        # ── TP2 — close remainder ──────────────────────────────────────────────
        if plan.tp1_hit and price >= plan.tp2:
            log.info(f"  [TP2] {plan.symbol}: ${price:.6f} — closing {plan.size} contracts (100%)")
            close_position(exchange, plan, plan.size, "TP2 (100%)", dry_run)
            return True

    else:  # short
        # ── Stop loss ──────────────────────────────────────────────────────────
        if price >= plan.sl:
            log.info(f"  [SL HIT] {plan.symbol}: ${price:.6f} >= ${plan.sl:.6f}")
            close_position(exchange, plan, plan.size, "STOP LOSS", dry_run)
            return True

        # ── Early breakeven ────────────────────────────────────────────────────
        if not plan.tp1_hit and not plan.be_moved and price <= plan.entry * (1 - BE_TRIGGER_PCT):
            plan.sl = plan.entry
            plan.be_moved = True
            log.info(f"  [BE] {plan.symbol}: SL locked to ${plan.entry:.6f} (+{BE_TRIGGER_PCT*100:.0f}% trigger)")

        # ── TP1 — close 50% ────────────────────────────────────────────────────
        if not plan.tp1_hit and price <= plan.tp1:
            half = round(plan.size * 0.5, 2)
            log.info(f"  [TP1] {plan.symbol}: ${price:.6f} — closing {half} contracts (50%)")
            if close_position(exchange, plan, half, "TP1 (50%)", dry_run):
                plan.tp1_hit  = True
                plan.size    -= half
                plan.sl       = plan.entry
                plan.be_moved = True
                log.info(f"    SL → breakeven ${plan.entry:.6f} | trailing profit mode ON")

        # ── Trailing SL + dynamic TP2 ─────────────────────────────────────────
        if plan.tp1_hit:
            new_sl = min(plan.entry, price * (1 + TRAIL_PCT))
            if new_sl < plan.sl:
                plan.sl = new_sl
                log.info(f"    Trail SL → ${plan.sl:.6f}")
            new_tp2 = price * (1 - TRAIL_TP_PCT)   # extend TP2 downward as price falls
            if new_tp2 < plan.tp2:
                plan.tp2 = new_tp2
                log.info(f"    Trail TP2 → ${plan.tp2:.6f}")

        # ── TP2 — close remainder ──────────────────────────────────────────────
        if plan.tp1_hit and price <= plan.tp2:
            log.info(f"  [TP2] {plan.symbol}: ${price:.6f} — closing {plan.size} contracts (100%)")
            close_position(exchange, plan, plan.size, "TP2 (100%)", dry_run)
            return True

    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    log.info(f"=== Exit Manager [{mode}] ===")

    exchange = make_exchange()
    plans = dict(MANUAL_PLANS)

    sync_positions(exchange, plans)
    if plans:
        for sym, p in plans.items():
            log.info(f"Watching {sym} {p.side} | entry={p.entry:.6f} sl={p.sl:.6f} tp1={p.tp1:.6f} tp2={p.tp2:.6f}")
    else:
        log.info("No open positions yet — polling for new ones...")

    last_sync = time.time()

    # Run indefinitely — picks up new positions from auto_trader as they open
    while True:
        if time.time() - last_sync >= 60:
            sync_positions(exchange, plans)
            for sym, p in plans.items():
                log.info(f"Watching {sym} {p.side} | entry={p.entry:.6f} sl={p.sl:.6f} tp1={p.tp1:.6f} tp2={p.tp2:.6f}")
            last_sync = time.time()

        if not plans:
            time.sleep(POLL_SEC)
            continue

        closed = []
        for sym, plan in list(plans.items()):
            try:
                t = exchange.fetch_ticker(sym)
                price = float(t["last"])
                pnl   = (price - plan.entry) * plan.size * (1 if plan.side == "long" else -1)
                phase = "TRAIL" if plan.tp1_hit else ("BE-LOCK" if plan.be_moved else "OPEN")
                log.info(
                    f"  {sym} [{phase}] ${price:.6f} | "
                    f"SL=${plan.sl:.6f} | TP2=${plan.tp2:.6f} | PnL~${pnl:.2f}"
                )
                done = check_exit(price, plan, exchange, args.dry_run)
                if done:
                    closed.append(sym)
            except Exception as exc:
                log.warning(f"{sym} price check error: {exc}")

        for sym in closed:
            plans.pop(sym, None)
            log.info(f"Position {sym} fully closed — removed from watch.")

        if not plans:
            log.info("All positions closed — waiting for new ones...")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
