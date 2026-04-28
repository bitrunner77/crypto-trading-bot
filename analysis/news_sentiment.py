"""
analysis/news_sentiment.py — News sentiment signal using Messari API.

Fetches recent headlines for a coin and scores them positive/negative/neutral.
A strongly negative news cycle → avoid longs. Strongly positive → favour longs.

Messari API endpoint: GET https://data.messari.io/api/v1/news/{asset}
No authentication needed for the public endpoint (key gives higher rate limits).
"""
from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger("cryptobot.news")

# Sentiment word banks (simple lexicon-based scoring)
_POSITIVE = {
    "bullish", "surge", "rally", "breakout", "gain", "record", "high",
    "adoption", "partnership", "upgrade", "launch", "growth", "buy",
    "accumulate", "moon", "pump", "green", "rise", "soar", "all-time",
    "institutional", "approval", "etf", "halving",
}
_NEGATIVE = {
    "bearish", "crash", "dump", "hack", "exploit", "scam", "fraud",
    "ban", "regulation", "lawsuit", "sell", "drop", "plunge", "loss",
    "liquidation", "delisting", "vulnerability", "attack", "fear",
    "fud", "collapse", "bankrupt", "shutdown", "warning",
}

# Cache to avoid hammering the API (30-min TTL per asset)
_cache: Dict[str, tuple] = {}   # asset → (score, ts, description)
_CACHE_TTL = 1800               # seconds


@dataclass
class SentimentSignal:
    asset:       str
    score:       float    # -1.0 (very negative) to +1.0 (very positive)
    label:       str      # "positive" | "negative" | "neutral"
    articles:    int      # number of recent articles scored
    description: str
    bias:        str      # "long_favoured" | "short_favoured" | "neutral"


def fetch_news_sentiment(symbol: str, api_key: str = "") -> SentimentSignal:
    """
    Fetch recent news for a symbol and return a sentiment score.
    symbol: CCXT format like 'BTC/USDT:USDT' → asset key 'bitcoin'
    """
    asset = _symbol_to_asset(symbol)

    # Check cache
    cached = _cache.get(asset)
    if cached:
        score, ts, desc = cached
        if (datetime.now(timezone.utc).timestamp() - ts) < _CACHE_TTL:
            label = _label(score)
            return SentimentSignal(asset, score, label, 0, desc, _bias(score))

    try:
        url = f"https://data.messari.io/api/v1/news/{asset}"
        headers = {"User-Agent": "cryptobot/1.0"}
        if api_key:
            headers["x-messari-api-key"] = api_key

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        articles = data.get("data", [])[:10]   # last 10 articles
        score = _score_articles(articles)
        label = _label(score)
        n = len(articles)
        desc = f"News sentiment [{asset}]: {label} (score {score:+.2f}, {n} articles)"
        _cache[asset] = (score, datetime.now(timezone.utc).timestamp(), desc)
        return SentimentSignal(asset, score, label, n, desc, _bias(score))

    except Exception as exc:
        logger.debug(f"[NEWS] Failed for {asset}: {exc}")
        return SentimentSignal(asset, 0.0, "neutral", 0,
                               f"News unavailable for {asset}", "neutral")


# ── internals ─────────────────────────────────────────────────────────────────

def _score_articles(articles: list) -> float:
    if not articles:
        return 0.0
    total = 0.0
    for a in articles:
        title = (a.get("title") or "").lower()
        words = set(title.split())
        pos = len(words & _POSITIVE)
        neg = len(words & _NEGATIVE)
        total += (pos - neg) / max(len(words), 1)
    return max(-1.0, min(1.0, total / len(articles) * 10))


def _label(score: float) -> str:
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def _bias(score: float) -> str:
    if score > 0.20:
        return "long_favoured"
    if score < -0.20:
        return "short_favoured"
    return "neutral"


# Map common CCXT symbols to Messari asset slugs
_SLUG_MAP = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "xrp", "DOGE": "dogecoin", "ADA": "cardano",
    "BNB": "binance-coin", "AVAX": "avalanche", "DOT": "polkadot",
    "LINK": "chainlink", "MATIC": "polygon", "UNI": "uniswap",
    "ATOM": "cosmos", "LTC": "litecoin", "BCH": "bitcoin-cash",
    "NEAR": "near-protocol", "ARB": "arbitrum", "OP": "optimism",
    "SUI": "sui", "APT": "aptos", "INJ": "injective",
    "PEPE": "pepe", "SHIB": "shiba-inu",
}


def _symbol_to_asset(symbol: str) -> str:
    base = symbol.split("/")[0].upper()
    return _SLUG_MAP.get(base, base.lower())
