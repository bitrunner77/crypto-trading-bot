"""
scripts/news_feed.py — Crypto news from CoinDesk, CoinTelegraph, Decrypt.

Filters headlines for coins currently in the auto_trader watchlist.

Usage:
  python scripts/news_feed.py            # all news
  python scripts/news_feed.py --watch    # only watchlist coins
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FEEDS = {
    "CoinDesk":      "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
    "Decrypt":       "https://decrypt.co/feed",
}

WATCHLIST_COINS = [
    "BOME", "FLOKI", "DEXE", "HUMA", "DYDX", "OPN",
    "BTC", "BITCOIN", "ETH", "ETHEREUM", "SOL", "SOLANA",
    "DOGE", "XRP", "BNB", "LINK", "ORDI", "ZEC",
]


def fetch_news(watch_only: bool = False, limit: int = 30) -> list[dict]:
    articles = []
    for source, url in FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:limit]:
                title   = entry.get("title", "").strip()
                link    = entry.get("link", "")
                pubdate = entry.get("published", entry.get("updated", ""))
                summary = entry.get("summary", "")[:200]

                if watch_only:
                    text = (title + " " + summary).upper()
                    if not any(coin in text for coin in WATCHLIST_COINS):
                        continue

                articles.append({
                    "source":  source,
                    "title":   title,
                    "url":     link,
                    "date":    pubdate[:16] if pubdate else "",
                    "summary": summary,
                })
        except Exception as e:
            print(f"  [{source}] fetch error: {e}")

    articles.sort(key=lambda x: x["date"], reverse=True)
    return articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="Only show watchlist coin news")
    parser.add_argument("--limit", type=int, default=10, help="Max articles to show")
    args = parser.parse_args()

    label = "WATCHLIST" if args.watch else "LATEST"
    print(f"\n{'='*70}")
    print(f"CRYPTO NEWS — {label} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*70}")

    articles = fetch_news(watch_only=args.watch)[:args.limit]
    if not articles:
        print("  No articles found.")
    else:
        for a in articles:
            print(f"\n[{a['source']}] {a['date']}")
            print(f"  {a['title']}")
            print(f"  {a['url']}")

    print(f"\n{'='*70}\n")


if __name__ == "__main__":
    main()
