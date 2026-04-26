"""
main.py — Entry point for the Ultimate Cryptocurrency Trading Bot.

Rules in effect:
  - Grow balance as much as possible
  - Long or short on BTC, ETH, SOL perpetuals
  - Max 20x leverage | Max 50% of remaining budget per trade
  - Track every trade, track running balance
  - If balance hits $0, you're out
  - Before every trade: state reasoning out loud
  - After each round: tell what changed and why

Usage:
  python main.py --mode paper --strategy ai_driven
  python main.py --mode live  --strategy ai_driven
  python main.py --mode backtest --strategy momentum --start 2024-01-01
"""
from __future__ import annotations

import sys

# Force UTF-8 output on Windows so Unicode (emojis, box-drawing chars) works
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich import box

console = Console(legacy_windows=False)
logger = logging.getLogger("cryptobot.main")

# ── Round-summary state ────────────────────────────────────────────────────────
_round_decisions: List[Dict] = []
_prev_balance: float = 0.0


def _print_round_summary(round_num: int, balance_before: float, balance_after: float,
                          decisions: List[Dict]) -> None:
    """Rule: after each round, tell what you're changing and why."""
    change = balance_after - balance_before
    pct = (change / balance_before * 100) if balance_before > 0 else 0.0
    sign = "+" if change >= 0 else ""
    color = "green" if change >= 0 else "red"

    lines = [
        f"[bold]Round {round_num} Summary[/bold]",
        f"Balance: [bold]${balance_after:,.2f}[/bold]  "
        f"([{color}]{sign}${change:,.2f} ({sign}{pct:.2f}%)[/{color}] this round)",
        "",
    ]

    for d in decisions:
        action = d.get("action", "hold")
        symbol = d.get("symbol", "?")
        if action == "hold":
            lines.append(f"  {symbol}: HOLD — {d.get('reasoning', '')[:80]}")
        else:
            lev = d.get("leverage", 1)
            size = d.get("size_pct", 0)
            lines.append(
                f"  {symbol}: [bold]{action.upper()}[/bold] "
                f"({size:.0%} margin × {lev}x lev)"
            )
            reasoning = d.get("reasoning", "")
            if reasoning:
                lines.append(f"    Reasoning: {reasoning[:120]}")
            changes = d.get("changes_from_last_round", "")
            if changes:
                lines.append(f"    Changed:   {changes[:120]}")

    console.print(Panel("\n".join(lines), box=box.ROUNDED, border_style="cyan"))


# ── Arg parsing ────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Ultimate AI-Powered Cryptocurrency Trading Bot (Claude Opus)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode", choices=["paper", "live", "backtest"],
                        default=None)
    parser.add_argument("--strategy",
                        choices=["ai_driven", "momentum", "mean_reversion", "grid", "dca"],
                        default=None)
    parser.add_argument("--pairs", nargs="+")
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--balance", type=float, default=None)
    return parser.parse_args()


def build_config(args):
    from config import settings
    if args.mode and args.mode != "backtest":
        settings.trading_mode = args.mode
    if args.strategy:
        settings.strategy = args.strategy
    if args.pairs:
        settings.trading_pairs = args.pairs
    if args.timeframe:
        settings.timeframe = args.timeframe
    if args.balance:
        settings.paper_initial_balance = args.balance
    if args.start:
        settings.backtest_start = args.start
    if args.end:
        settings.backtest_end = args.end
    return settings


def make_exchange(config):
    if config.trading_mode == "paper":
        from exchange.paper_exchange import PaperExchange
        return PaperExchange(config)
    from exchange.ccxt_client import CCXTClient
    return CCXTClient(config)


# ── LIVE / PAPER TRADING LOOP ──────────────────────────────────────────────────

async def run_trading(config):
    global _prev_balance

    from utils.helpers import setup_logging
    from data.database import init_db
    import data.database as db_mod
    from data.fetcher import DataFetcher
    from exchange.websocket_manager import PriceFeed
    from analysis.indicators import compute_indicators
    from portfolio.tracker import PortfolioTracker
    from risk.manager import RiskManager
    from strategies.registry import get_strategy
    from ui.dashboard import Dashboard, print_startup_banner
    from ui.web_dashboard import start_in_thread, update_state
    from rich.live import Live

    setup_logging(config.log_level)
    print_startup_banner(config)
    init_db()
    start_in_thread(port=8080)

    exchange = make_exchange(config)
    fetcher = DataFetcher(exchange, config)
    strategy = get_strategy(config.strategy, config)
    risk_mgr = RiskManager(config)
    portfolio = PortfolioTracker(exchange, config, db_mod)
    dashboard = Dashboard(config)

    # Price feed
    price_feed = PriceFeed(exchange, config.trading_pairs, poll_interval=5.0)
    await price_feed.start()
    await asyncio.sleep(3)

    shutdown_event = asyncio.Event()

    def _shutdown(sig, frame):
        logger.info("Shutdown signal received...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(f"Trading loop started — interval: {config.loop_interval_seconds}s")

    # Track P&L across rounds
    recent_pnl: List[Dict] = []

    try:
        with Live(refresh_per_second=1, console=None) as live:
            while not shutdown_event.is_set():
                loop_start = asyncio.get_event_loop().time()

                try:
                    port_snapshot = await portfolio.refresh()
                    prices = price_feed.get_all_prices()
                    portfolio.update_prices(prices)

                    balance_at_round_start = port_snapshot["cash_balance"]
                    if _prev_balance == 0:
                        _prev_balance = balance_at_round_start

                    round_num = risk_mgr.increment_round()
                    round_decisions: List[Dict] = []
                    last_decision = None

                    # ── Bankruptcy check ───────────────────────────────────────
                    if risk_mgr.check_bankruptcy(port_snapshot["cash_balance"]):
                        console.print(Panel(
                            "[bold red]💀 BALANCE REACHED $0 — YOU'RE OUT.[/bold red]\n"
                            "The game is over. Final balance: "
                            f"${port_snapshot['cash_balance']:,.2f}",
                            box=box.DOUBLE_EDGE, border_style="red"
                        ))
                        break

                    # ── Check for liquidations ────────────────────────────────
                    try:
                        liquidated = await exchange.check_liquidations(prices)
                    except Exception as e:
                        logger.warning(f"Liquidation check failed: {e}")
                        liquidated = []
                    for sym in liquidated:
                        db_mod.delete_position(sym)
                        logger.error(f"Position liquidated: {sym}")

                    # ── Per-symbol analysis ────────────────────────────────────
                    for symbol in config.trading_pairs:
                        try:
                            df = await fetcher.fetch_ohlcv(symbol, config.timeframe, limit=200)
                            if df.empty:
                                continue

                            indicators = compute_indicators(df)
                            positions = db_mod.get_positions()
                            recent_trades = db_mod.get_recent_trades(symbol=symbol, limit=5)
                            trade_stats = db_mod.get_trade_stats(symbol=symbol)

                            signal_obj = strategy.generate_signal(
                                symbol=symbol,
                                df=df,
                                indicators=indicators,
                                portfolio_value=port_snapshot["total_value"],
                                cash_balance=port_snapshot["cash_balance"],
                                open_positions=positions,
                                recent_trades=recent_trades,
                                trade_stats=trade_stats,
                                timeframe=config.timeframe,
                            )

                            # Build decision record for round summary
                            decision_record = {
                                "action": signal_obj.action,
                                "symbol": symbol,
                                "confidence": signal_obj.confidence,
                                "reasoning": signal_obj.reasoning,
                                "size_pct": signal_obj.size_pct,
                                "leverage": getattr(signal_obj, "leverage", 1),
                                "changes_from_last_round": getattr(signal_obj, "changes_from_last_round", ""),
                            }
                            round_decisions.append(decision_record)
                            last_decision = decision_record

                            action = signal_obj.action
                            leverage = getattr(signal_obj, "leverage", 1)

                            if action in ("long", "short"):
                                # ── RULE: State reasoning out loud before trade ──
                                console.print(Panel(
                                    f"[bold cyan]📢 BEFORE TRADE — {symbol} {action.upper()}[/bold cyan]\n\n"
                                    f"{signal_obj.reasoning}\n\n"
                                    f"[dim]Leverage: {leverage}x | Size: {signal_obj.size_pct:.0%} of margin | "
                                    f"Confidence: {signal_obj.confidence:.0%}[/dim]",
                                    box=box.ROUNDED, border_style="cyan"
                                ))

                                approved, adj_size, adj_lev, reason = risk_mgr.validate_trade(
                                    action=action,
                                    symbol=symbol,
                                    size_pct=signal_obj.size_pct,
                                    leverage=leverage,
                                    portfolio_value=port_snapshot["total_value"],
                                    cash_balance=port_snapshot["cash_balance"],
                                    initial_balance=config.paper_initial_balance,
                                    open_positions=positions,
                                )

                                if approved and adj_size > 0:
                                    margin = port_snapshot["cash_balance"] * adj_size
                                    current_price = indicators.get("price", 0)
                                    atr = indicators.get("atr_14")

                                    sl = risk_mgr.compute_stop_loss(
                                        current_price, action, adj_lev, atr
                                    )
                                    tp = risk_mgr.compute_take_profit(
                                        current_price, action, sl
                                    )

                                    try:
                                        order = await exchange.create_order(
                                            symbol, action, margin,
                                            price=current_price, leverage=adj_lev
                                        )
                                    except Exception as oe:
                                        logger.error(
                                            f"create_order FAILED for {symbol} {action}: {oe}",
                                            exc_info=True,
                                        )
                                        order = None

                                    if order and order.get("status") in ("closed", "filled"):
                                        fill_price = (
                                            order.get("average")
                                            or order.get("price")
                                            or current_price
                                        )
                                        db_mod.upsert_position(
                                            symbol, action, fill_price, margin, sl, tp,
                                            leverage=adj_lev,
                                        )
                                        db_mod.log_trade(
                                            symbol=symbol, side=action,
                                            price=fill_price, amount=margin,
                                            strategy=config.strategy,
                                            mode=config.trading_mode,
                                            reasoning=signal_obj.reasoning,
                                            confidence=signal_obj.confidence,
                                        )
                                        recent_pnl.append({
                                            "round": round_num, "symbol": symbol,
                                            "action": action, "price": fill_price,
                                            "leverage": adj_lev, "margin": margin,
                                        })
                                    else:
                                        logger.warning(
                                            f"Order for {symbol} {action} not filled "
                                            f"(status={order.get('status') if order else 'error'}); "
                                            "skipping persistence."
                                        )
                                else:
                                    logger.info(f"Trade blocked by risk: {reason}")

                            elif action == "close":
                                # ── Close existing position ──────────────────
                                pos_list = [p for p in db_mod.get_positions() if p["symbol"] == symbol]
                                if pos_list:
                                    pos_record = pos_list[0]
                                    current_price = indicators.get("price", 0)
                                    console.print(
                                        f"[yellow]CLOSING {symbol} @ ${current_price:,.2f} — "
                                        f"{signal_obj.reasoning[:80]}[/yellow]"
                                    )
                                    try:
                                        order = await exchange.create_order(
                                            symbol, "close", 0, price=current_price
                                        )
                                    except Exception as oe:
                                        logger.error(
                                            f"close order FAILED for {symbol}: {oe}",
                                            exc_info=True,
                                        )
                                        order = None

                                    if order and order.get("status") in ("closed", "filled"):
                                        pnl = PortfolioTracker.realized_pnl_from_order(
                                            order, pos_record
                                        )
                                        risk_mgr.record_pnl(pnl)
                                        db_mod.delete_position(symbol)
                                        db_mod.log_trade(
                                            symbol=symbol, side="close",
                                            price=current_price, amount=0,
                                            strategy=config.strategy,
                                            mode=config.trading_mode,
                                            reasoning=signal_obj.reasoning,
                                            confidence=signal_obj.confidence,
                                            pnl=pnl,
                                        )
                                        recent_pnl.append({
                                            "round": round_num, "symbol": symbol,
                                            "action": "close", "pnl": pnl,
                                        })
                                    else:
                                        logger.warning(
                                            f"Close order for {symbol} not filled; "
                                            "DB position retained."
                                        )

                        except Exception as e:
                            logger.error(f"Error processing {symbol}: {e}", exc_info=True)

                    # ── RULE: After each round — print what changed and why ─────
                    new_balance = port_snapshot["cash_balance"]
                    if hasattr(exchange, "get_cash_balance"):
                        new_balance = exchange.get_cash_balance()

                    _print_round_summary(round_num, _prev_balance, new_balance, round_decisions)
                    _prev_balance = new_balance

                    # Update dashboard
                    perf = portfolio.get_performance_metrics()
                    risk_sum = risk_mgr.get_risk_summary(
                        port_snapshot["total_value"], config.paper_initial_balance
                    )
                    dashboard.update(
                        prices=prices,
                        portfolio=port_snapshot,
                        recent_trades=db_mod.get_recent_trades(limit=10),
                        last_decision=last_decision,
                        risk_summary=risk_sum,
                        perf_metrics=perf,
                    )
                    live.update(dashboard.render())
                    # Push to web dashboard
                    update_state(
                        prices=prices,
                        portfolio=port_snapshot,
                        last_decision=last_decision,
                        risk_summary=risk_sum,
                        perf_metrics=perf,
                        round=round_num,
                    )

                except Exception as e:
                    logger.error(f"Trading loop error: {e}", exc_info=True)

                elapsed = asyncio.get_event_loop().time() - loop_start
                sleep_time = max(0, config.loop_interval_seconds - elapsed)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass

    finally:
        logger.info("Shutting down...")
        await price_feed.stop()
        await exchange.close()
        logger.info("Bot stopped cleanly.")


# ── BACKTESTING ────────────────────────────────────────────────────────────────

async def run_backtest(config, args):
    from utils.helpers import setup_logging
    from data.database import init_db
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from strategies.registry import get_strategy

    setup_logging(config.log_level)
    init_db()

    strategy_name = args.strategy or config.strategy
    strategy = get_strategy(strategy_name, config)
    exchange = make_exchange(config)
    fetcher = DataFetcher(exchange, config)
    engine = BacktestEngine(config)

    for symbol in config.trading_pairs:
        console.print(f"\n[bold cyan]Fetching historical data for {symbol}...[/bold cyan]")
        try:
            df = await fetcher.fetch_ohlcv(symbol, config.timeframe, limit=1000, use_cache=False)
            if df.empty:
                console.print(f"[red]No data for {symbol}, skipping.[/red]")
                continue
            result = engine.run(
                strategy=strategy, df=df, symbol=symbol,
                timeframe=config.timeframe,
                initial_balance=config.paper_initial_balance,
            )
            console.print(result.summary())
        except Exception as e:
            console.print(f"[red]Backtest failed for {symbol}: {e}[/red]")
            raise

    await exchange.close()


# ── ENTRY POINT ────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    config = build_config(args)
    mode = args.mode or config.trading_mode

    if mode == "backtest":
        asyncio.run(run_backtest(config, args))
    else:
        asyncio.run(run_trading(config))


if __name__ == "__main__":
    main()
