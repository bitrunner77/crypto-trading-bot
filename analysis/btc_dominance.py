"""
analysis/btc_dominance.py — BTC dominance as a market-wide regime signal.

Source: CoinGecko public API (no key required).
Cache: 15 minutes (dominance moves slowly).

Interpretation:
  > 60%  — BTC season: altcoins weak, only trade BTC or sit out alts
  55–60% — BTC trending: slight bias toward majors
  50–55% — Mixed: normal conditions
  45–50% — Alt momentum building: alts may outperform BTC
  < 45%  — Alt season: alts surging, higher allocations viable
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger("cryptobot.btc_dom")

_CACHE: dict = {}
_CACHE_TTL = 900   # 15 minutes


@dataclass
class DominanceSignal:
    dominance:      float   # 0–100 percent
    label:          str     # "btc_season" | "mixed" | "alt_season"
    alt_bias:       float   # -1.0 (avoid alts) to +1.0 (favour alts)
    description:    str


def fetch_btc_dominance() -> DominanceSignal:
    """Fetch BTC dominance from CoinGecko and return an alt-allocation bias."""
    cached = _CACHE.get("dom")
    if cached:
        sig, ts = cached
        if (datetime.now(timezone.utc).timestamp() - ts) < _CACHE_TTL:
            return sig

    try:
        url = "https://api.coingecko.com/api/v3/global"
        req = urllib.request.Request(
            url, headers={"User-Agent": "cryptobot/1.0", "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        dom = float(data["data"]["market_cap_percentage"].get("btc", 50.0))
        sig = _interpret(dom)
        _CACHE["dom"] = (sig, datetime.now(timezone.utc).timestamp())
        logger.debug(f"[BTC_DOM] {dom:.1f}% → {sig.label}")
        return sig

    except Exception as exc:
        logger.debug(f"[BTC_DOM] Fetch failed: {exc}")
        return DominanceSignal(50.0, "mixed", 0.0, "BTC dominance unavailable — using neutral")


def _interpret(dom: float) -> DominanceSignal:
    if dom > 60:
        return DominanceSignal(dom, "btc_season", -0.8,
            f"BTC dominance {dom:.1f}% — BTC season, avoid alts")
    if dom > 55:
        return DominanceSignal(dom, "btc_leading", -0.3,
            f"BTC dominance {dom:.1f}% — majors preferred")
    if dom > 50:
        return DominanceSignal(dom, "mixed", 0.0,
            f"BTC dominance {dom:.1f}% — mixed market")
    if dom > 45:
        return DominanceSignal(dom, "alt_building", 0.4,
            f"BTC dominance {dom:.1f}% — alt momentum building")
    return DominanceSignal(dom, "alt_season", 0.8,
        f"BTC dominance {dom:.1f}% — alt season, alts may outperform")


def alt_size_multiplier(signal: DominanceSignal, symbol: str) -> float:
    """
    Returns a size multiplier for a given symbol based on BTC dominance.
    BTC/ETH are majors and stay near 1.0. Alts scale by alt_bias.
    """
    base = symbol.split("/")[0].upper()
    is_major = base in ("BTC", "ETH")
    if is_major:
        return 1.0
    # Alt multiplier: neutral at 0.5, up to 1.5 in alt season, down to 0.3 in BTC season
    return round(max(0.3, min(1.5, 0.9 + signal.alt_bias * 0.6)), 2)
