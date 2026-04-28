"""
scripts/binance_scan.py — Scan all Binance USDT coins through regime+adaptation filter.

Usage:
  python scripts/binance_scan.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ccxt
import pandas as pd

from analysis.regime_detector import detect_regime
from analysis.adaptation_score import compute_adaptation_score
from analysis.analytics import build_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("binance_scan")

SAFE_REGIMES   = {"trending_up", "mean_reverting", "low_vol"}
ADAPT_MIN      = 40.0
MIN_VOL_USDT   = 10_000_000  # $10M


def main():
    binance = ccxt.binance({"enableRateLimit": True})

    log.info("Fetching Binance tickers...")
    tickers = binance.fetch_tickers()

    usdt = [
        t for t in tickers.values()
        if t["symbol"].endswith("/USDT")
        and not t["symbol"].startswith("USDT")
        and t["symbol"].isascii()
        and (t.get("quoteVolume") or 0) >= MIN_VOL_USDT
    ]
    usdt.sort(key=lambda t: t.get("quoteVolume", 0), reverse=True)
    top = usdt[:80]
    log.info(f"  {len(top)} coins with >${MIN_VOL_USDT/1e6:.0f}M volume — scanning regimes...")

    results = []
    for t in top:
        sym = t["symbol"]
        try:
            raw = binance.fetch_ohlcv(sym, "1h", limit=60)
            if len(raw) < 25:
                continue
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "vol"])
            df = df.astype({"open": float, "high": float, "low": float, "close": float})
            regime  = detect_regime(df)
            metrics = build_metrics({"id": sym, "trades": [], "pnl_curve": [], "performance": {}})
            adapt   = compute_adaptation_score(metrics, regime, "momentum")
            pct     = t.get("percentage") or 0
            vol     = t.get("quoteVolume") or 0
            tradeable = regime.regime in SAFE_REGIMES and adapt.total >= ADAPT_MIN
            results.append({
                "symbol": sym, "regime": regime.regime,
                "score": adapt.total, "pct": pct,
                "vol_m": vol / 1e6, "tradeable": tradeable,
            })
            if tradeable:
                log.info(f"  PASS  {sym:18s} | {regime.regime:15s} | score={adapt.total:.1f} | {pct:+.1f}% | ${vol/1e6:.0f}M")
        except Exception:
            continue

    tradeable = [r for r in results if r["tradeable"]]
    tradeable.sort(key=lambda x: (x["regime"] == "trending_up", x["score"], x["pct"]), reverse=True)

    print("\n" + "=" * 70)
    print(f"TRADEABLE: {len(tradeable)} / {len(results)} scanned")
    print("=" * 70)
    print(f"  {'Symbol':18s}  {'Regime':15s}  {'Score':>6}  {'24h%':>6}  {'Vol($M)':>8}")
    for r in tradeable:
        print(f"  {r['symbol']:18s}  {r['regime']:15s}  {r['score']:>6.1f}  {r['pct']:>+5.1f}%  ${r['vol_m']:>7.0f}M")
    if not tradeable:
        print("  No coins passed the filter — market broadly unfavorable.")
    print("=" * 70)


if __name__ == "__main__":
    main()
