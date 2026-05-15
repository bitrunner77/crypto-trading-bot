"""
scripts/mlb_polymarket_runner.py — Scan Polymarket MLB markets and place
paper bets whenever the strategy finds an edge.

Usage:
    python -m scripts.mlb_polymarket_runner --once
    python -m scripts.mlb_polymarket_runner --interval 300

This script is read-only against Polymarket; all stakes are recorded in an
in-process PolymarketPaperBook. No real funds are touched.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Dict

from rich.console import Console
from rich.table import Table

from config import settings
from exchange.polymarket_client import (
    PolymarketClient,
    PolymarketPaperBook,
)
from strategies.mlb_polymarket import MLBPolymarketStrategy
from utils.helpers import setup_logging

console = Console()


async def scan_once(client: PolymarketClient,
                    strategy: MLBPolymarketStrategy,
                    book: PolymarketPaperBook,
                    place_bets: bool) -> None:
    markets = await client.fetch_mlb_markets(limit=settings.polymarket_market_limit)
    console.print(f"[cyan]Fetched {len(markets)} MLB markets[/cyan]")
    if not markets:
        return

    table = Table(title="MLB Polymarket Signals")
    for col in ("Market", "Outcome", "Kind", "Price", "Stake", "Edge", "Reason"):
        table.add_column(col, overflow="fold")

    for m in markets:
        books: Dict[str, Dict] = {}
        for tid in m.token_ids:
            try:
                books[tid] = await client.fetch_orderbook(tid)
            except Exception as exc:
                logging.getLogger("cryptobot.polymarket").debug(
                    f"orderbook fetch failed for {tid}: {exc}")
        signals = strategy.evaluate(m, books, bankroll_usd=book.balance)
        for sig in signals:
            table.add_row(
                m.question[:50],
                sig.outcome,
                sig.kind,
                f"{sig.limit_price:.3f}",
                f"${sig.stake_usd:.2f}",
                f"{sig.edge*100:+.2f}%",
                sig.reasoning,
            )
            if place_bets:
                try:
                    book.place_bet(m, sig.outcome_index, sig.limit_price, sig.stake_usd)
                except ValueError as exc:
                    logging.getLogger("cryptobot.polymarket").warning(
                        f"skipped bet on {m.id}: {exc}")

    if table.row_count:
        console.print(table)
    else:
        console.print("[dim]No edges detected this scan.[/dim]")
    console.print(f"[bold]Paper bankroll:[/bold] ${book.balance:,.2f} | "
                  f"open bets: {len(book.open_bets())}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Polymarket MLB scanner (paper mode)")
    parser.add_argument("--once", action="store_true", help="run a single scan and exit")
    parser.add_argument("--interval", type=int, default=300,
                        help="seconds between scans when running continuously")
    parser.add_argument("--no-bets", action="store_true",
                        help="print signals only, do not record paper bets")
    args = parser.parse_args()

    setup_logging(settings.log_level)
    book = PolymarketPaperBook(balance=settings.polymarket_paper_bankroll)
    strategy = MLBPolymarketStrategy(settings)

    async with PolymarketClient() as client:
        while True:
            try:
                await scan_once(client, strategy, book, place_bets=not args.no_bets)
            except Exception as exc:
                logging.getLogger("cryptobot.polymarket").exception(
                    f"scan failed: {exc}")
            if args.once:
                return
            await asyncio.sleep(args.interval)


if __name__ == "__main__":
    asyncio.run(main())
