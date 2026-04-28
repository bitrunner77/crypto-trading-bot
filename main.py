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

# Windows: aiohttp's async DNS resolver breaks on ProactorEventLoop (Python 3.8+).
# SelectorEventLoop uses the system resolver reliably.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from typing import Dict, List

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
                        choices=["ai_driven", "momentum", "mean_reversion", "grid", "dca",
                                 "regime_mean_reversion", "regime_momentum"],
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
    from analysis.whale_tracker import detect_whale_activity
    from analysis.funding_sentiment import fetch_funding_sentiment
    from analysis.breakout_validator import validate_breakout
    from analysis.session_timer import get_session_info
    from analysis.trailing_exit import TrailingExitManager
    from analysis.equity_optimizer import analyze_equity
    from analysis.strategy_rotator import StrategyRotator
    from analysis.regime_detector import detect_regime
    from analysis.coin_rotator import CoinRotator
    from analysis.hedging import HedgeManager
    from analysis.equity_protection import EquityProtector
    from analysis.manipulation_detector import detect_manipulation
    from analysis.dynamic_leverage import calculate_dynamic_leverage
    from analysis.session_learner import SessionLearner
    from analysis.news_sentiment import fetch_news_sentiment
    from analysis.btc_dominance import fetch_btc_dominance, alt_size_multiplier
    from analysis.portfolio_allocator import PortfolioAllocator
    from analysis.growth_engine import GrowthEngine
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

    from scripts.telegram_commander import TelegramCommander
    from scripts.weekly_optimizer import should_run_weekly, run_optimization

    exchange = make_exchange(config)
    if hasattr(exchange, "init_session"):
        await exchange.init_session()
    fetcher = DataFetcher(exchange, config)
    strategy = get_strategy(config.strategy, config)
    risk_mgr = RiskManager(config)
    portfolio = PortfolioTracker(exchange, config, db_mod)
    dashboard = Dashboard(config)

    # ── Advanced feature modules ──────────────────────────────────────────────
    trailing_exits  = TrailingExitManager()
    equity_curve:   List[float] = []
    rotator         = StrategyRotator(config.strategy)
    coin_rotator    = CoinRotator(max_active=config.coin_rotation_max_active)
    hedge_mgr       = HedgeManager(config.hedge_trigger_pct, config.hedge_ratio)
    eq_protector    = EquityProtector()
    sess_learner    = SessionLearner()
    allocator       = PortfolioAllocator(n_slots=config.portfolio_slots)
    growth_engine   = GrowthEngine(target_pct=config.monthly_growth_target)
    _signals:       Dict = {}

    _tg_state: Dict = {
        "strategy": config.strategy, "mode": config.trading_mode,
        "portfolio": {}, "risk_summary": {}, "perf_metrics": {},
        "positions": [], "round": 0, "signals": {},
        "coin_scores": [], "protection": "GREEN",
    }
    telegram = TelegramCommander(
        token=config.telegram_bot_token,
        chat_id=config.telegram_chat_id,
        state=_tg_state,
        pause_cb=lambda: risk_mgr._halt("Paused via Telegram"),
        resume_cb=risk_mgr.resume_trading,
        rotate_cb=lambda s: rotator.apply_rotation(s),
    )
    telegram.start()

    # Price feed
    price_feed = PriceFeed(exchange, config.trading_pairs, poll_interval=5.0)
    await price_feed.start()
    await asyncio.sleep(3)

    shutdown_event = asyncio.Event()

    def _shutdown(_sig, _frame):
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
                    if hasattr(exchange, "check_liquidations"):
                        liquidated = exchange.check_liquidations(prices)
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

                            # ── Signal sources ────────────────────────────────
                            session_info = get_session_info()
                            whale_sig    = detect_whale_activity(df, config.whale_volume_threshold)
                            manip_sig    = (detect_manipulation(df, config.whale_volume_threshold)
                                            if config.manipulation_detection else None)
                            news_sig     = (fetch_news_sentiment(symbol, config.messari_api_key)
                                            if config.news_sentiment_enabled else None)
                            dom_sig      = (fetch_btc_dominance()
                                            if config.btc_dominance_enabled else None)
                            try:
                                funding_sig = fetch_funding_sentiment(exchange, symbol)
                            except Exception:
                                funding_sig = None
                            _signals[symbol] = {
                                "session":  session_info.description,
                                "whale":    whale_sig.description if whale_sig.detected else None,
                                "funding":  funding_sig.description if funding_sig else None,
                                "manip":    manip_sig.description if (manip_sig and manip_sig.detected) else None,
                                "news":     news_sig.description if news_sig else None,
                                "dominance": dom_sig.description if dom_sig else None,
                            }

                            # ── Manipulation guard ─────────────────────────────
                            if manip_sig and manip_sig.action == "avoid":
                                logger.info(f"[MANIP] {symbol}: {manip_sig.description}")
                                continue

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
                                # ── Equity protection gate ─────────────────────
                                prot = eq_protector.current_state
                                if prot.pause_new:
                                    logger.info(f"[PROTECT] {prot.level}: new trades paused")
                                    continue

                                # ── Fake-breakout filter (scalping/grid only) ──
                                # regime_momentum uses EMA cross — already filtered.
                                # Only block on obvious pump/dump (vol 10×+ with reversal).
                                if config.strategy not in ("regime_momentum", "momentum",
                                                            "regime_mean_reversion"):
                                    direction = "up" if action == "long" else "down"
                                    bk = validate_breakout(df, direction, indicators)
                                    if not bk.is_real:
                                        logger.info(f"[BREAKOUT] Filtered: {bk.description}")
                                        continue

                                # ── Dynamic leverage ───────────────────────────
                                if config.dynamic_leverage_enabled:
                                    leverage = calculate_dynamic_leverage(
                                        base_leverage=leverage,
                                        indicators=indicators,
                                        regime=_signals.get("rotation", {}).get("regime", "neutral"),
                                        recent_trades=db_mod.get_recent_trades(limit=10),
                                        protection_level=prot.level,
                                        max_leverage=config.max_leverage,
                                    )

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
                                    # ── Composite size multiplier ─────────────
                                    size_mult = 1.0
                                    if config.session_sniper_mode:
                                        size_mult *= session_info.size_multiplier
                                    if config.session_learning_enabled:
                                        size_mult *= sess_learner.get_multiplier()
                                    if config.equity_protection_enabled:
                                        size_mult *= prot.size_cap
                                    if manip_sig and manip_sig.action == "reduce_size":
                                        size_mult *= 0.5
                                    # News sentiment filter
                                    if news_sig and news_sig.bias == "short_favoured" and action == "long":
                                        size_mult *= 0.5
                                    elif news_sig and news_sig.bias == "long_favoured" and action == "long":
                                        size_mult *= 1.2
                                    # BTC dominance alt filter
                                    if dom_sig and config.btc_dominance_enabled:
                                        size_mult *= alt_size_multiplier(dom_sig, symbol)
                                    # Monthly growth engine
                                    if config.growth_engine_enabled:
                                        size_mult *= growth_engine.update(
                                            port_snapshot["total_value"]
                                        )
                                    adj_size = min(
                                        adj_size * size_mult,
                                        config.risk_max_position_pct,
                                    )

                                    margin = port_snapshot["cash_balance"] * adj_size
                                    current_price = indicators.get("price", 0)
                                    atr = indicators.get("atr_14")

                                    sl = risk_mgr.compute_stop_loss(
                                        current_price, action, adj_lev, atr
                                    )
                                    tp = risk_mgr.compute_take_profit(
                                        current_price, action, sl
                                    )

                                    order = await exchange.create_order(
                                        symbol, action, margin,
                                        price=current_price, leverage=adj_lev,
                                        stop_loss=sl, take_profit=tp,
                                    )

                                    db_mod.upsert_position(
                                        symbol, action, current_price, margin, sl, tp
                                    )
                                    db_mod.log_trade(
                                        symbol=symbol, side=action,
                                        price=current_price, amount=margin,
                                        strategy=config.strategy,
                                        mode=config.trading_mode,
                                        reasoning=signal_obj.reasoning,
                                        confidence=signal_obj.confidence,
                                    )
                                    trailing_exits.register_position(
                                        symbol, action, current_price, atr
                                    )
                                    if config.portfolio_allocator_enabled:
                                        allocator.mark_open(symbol, action, current_price)
                                    telegram.send(
                                        f"*NEW TRADE* {symbol} {action.upper()}\n"
                                        f"Entry `${current_price:,.2f}` | "
                                        f"${margin:,.2f} × {adj_lev}x\n"
                                        f"_{signal_obj.reasoning[:100]}_"
                                    )
                                    recent_pnl.append({
                                        "round": round_num, "symbol": symbol,
                                        "action": action, "price": current_price,
                                        "leverage": adj_lev, "margin": margin,
                                    })
                                else:
                                    logger.info(f"Trade blocked by risk: {reason}")

                            elif action == "close":
                                # ── Close existing position ──────────────────
                                pos_list = [p for p in db_mod.get_positions() if p["symbol"] == symbol]
                                if pos_list:
                                    current_price = indicators.get("price", 0)
                                    console.print(
                                        f"[yellow]CLOSING {symbol} @ ${current_price:,.2f} — "
                                        f"{signal_obj.reasoning[:80]}[/yellow]"
                                    )
                                    order = await exchange.create_order(
                                        symbol, "close", 0, price=current_price
                                    )
                                    pnl = order.get("pnl", 0.0)
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
                                    if config.session_learning_enabled:
                                        sess_learner.record_trade(pnl)
                                    if config.portfolio_allocator_enabled:
                                        allocator.mark_closed(symbol, pnl)

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
                        signals=_signals,
                    )

                    # ── Trailing exit checks ──────────────────────────────────
                    for pos in db_mod.get_positions():
                        sym = pos["symbol"]
                        px  = prices.get(sym)
                        if not px:
                            continue
                        dec = trailing_exits.evaluate(sym, float(px))
                        if dec.action in ("close_full", "close_partial"):
                            try:
                                order = await exchange.create_order(
                                    sym, "close", 0, price=float(px)
                                )
                                pnl = order.get("pnl", 0.0)
                                risk_mgr.record_pnl(pnl)
                                if dec.action == "close_full":
                                    db_mod.delete_position(sym)
                                    trailing_exits.remove_position(sym)
                                db_mod.log_trade(
                                    symbol=sym, side="close", price=float(px),
                                    amount=0, strategy=config.strategy,
                                    mode=config.trading_mode,
                                    reasoning=f"Trail: {dec.reason}",
                                    confidence=1.0, pnl=pnl,
                                )
                                telegram.send(
                                    f"*TRAIL EXIT* `{sym}`: {dec.reason}\nPnL `${pnl:+.2f}`"
                                )
                            except Exception as exc:
                                logger.error(f"Trail exit failed {sym}: {exc}")

                    # ── Equity protection (tiered) ────────────────────────────
                    equity_curve.append(port_snapshot["total_value"])
                    if config.equity_protection_enabled:
                        prot_state = eq_protector.update(port_snapshot["total_value"])
                        _signals["protection"] = {
                            "level":   prot_state.level,
                            "drawdown": f"{prot_state.drawdown:.1%}",
                            "message": prot_state.message,
                        }
                        if prot_state.level in ("RED", "BLACK"):
                            telegram.send(
                                f"*EQUITY PROTECTION {prot_state.level}*\n"
                                f"{prot_state.message}\nDrawdown: `{prot_state.drawdown:.1%}`"
                            )
                        if prot_state.close_all:
                            for pos in db_mod.get_positions():
                                sym = pos["symbol"]
                                px  = prices.get(sym)
                                if px:
                                    try:
                                        await exchange.create_order(sym, "close", 0, price=float(px))
                                        db_mod.delete_position(sym)
                                    except Exception:
                                        pass

                    # ── Hedging ───────────────────────────────────────────────
                    if config.hedging_enabled:
                        hedge_signals = hedge_mgr.evaluate(
                            db_mod.get_positions(), prices, port_snapshot["cash_balance"]
                        )
                        for hsig in hedge_signals:
                            if hsig.action == "open_hedge":
                                try:
                                    margin = port_snapshot["cash_balance"] * hsig.size_pct
                                    px = float(prices.get(hsig.symbol) or 0)
                                    if px > 0 and margin > 1:
                                        await exchange.create_order(
                                            hsig.symbol, hsig.side, margin, price=px, leverage=1
                                        )
                                        telegram.send(f"*HEDGE OPENED* `{hsig.symbol}`\n_{hsig.reason}_")
                                except Exception as exc:
                                    logger.error(f"Hedge open failed: {exc}")
                            elif hsig.action == "close_hedge":
                                logger.info(f"[HEDGE] Closing hedge on {hsig.symbol}: {hsig.reason}")
                                telegram.send(f"*HEDGE CLOSED* `{hsig.symbol}` — {hsig.reason}")
                        _signals["hedges"] = hedge_mgr.active_hedges

                    # ── Portfolio allocator rebalance ─────────────────────────
                    if config.portfolio_allocator_enabled and round_num % 6 == 0:
                        ranked = [s.symbol for s in _signals.get("coin_scores", [])] \
                                 or config.trading_pairs
                        alloc_plan = allocator.allocate(
                            port_snapshot["cash_balance"], ranked
                        )
                        _signals["allocation"] = alloc_plan.description
                        for slot in allocator.slots:
                            px = prices.get(slot.symbol)
                            if px:
                                allocator.update_unrealised(slot.symbol, float(px))

                    # ── Growth engine status ──────────────────────────────────
                    if config.growth_engine_enabled:
                        _signals["growth"] = growth_engine.status_line()

                    # ── Coin rotation ─────────────────────────────────────────
                    if config.coin_rotation_enabled and round_num % 6 == 0:
                        try:
                            scores = await coin_rotator.rank(fetcher, config.timeframe)
                            _signals["coin_scores"] = [
                                {"symbol": s.symbol, "composite": s.composite,
                                 "momentum": s.momentum, "recommended": s.recommended}
                                for s in scores
                            ]
                            top = coin_rotator.top_pairs
                            if top:
                                logger.info(f"[COIN ROT] Top pairs: {top}")
                        except Exception as exc:
                            logger.debug(f"Coin rotation error: {exc}")

                    # ── Equity curve optimizer ────────────────────────────────
                    if config.equity_optimizer_enabled:
                        advice = analyze_equity(
                            equity_curve, db_mod.get_recent_trades(limit=20)
                        )
                        _signals["equity"] = {
                            "health": advice.health,
                            "note":   advice.suggestions[0] if advice.suggestions else "",
                            "adj":    advice.size_adjustment,
                        }
                        if advice.health == "critical":
                            telegram.send(f"*EQUITY ALERT* {advice.suggestions[0]}")

                    # ── Multi-strategy rotation ───────────────────────────────
                    if config.strategy_rotation_enabled:
                        try:
                            reg = detect_regime(df)
                            eq_health = _signals.get("equity", {}).get("health", "good")
                            rot = rotator.should_rotate(reg.regime, eq_health)
                            round_pnl = (equity_curve[-1] - equity_curve[-2]
                                         if len(equity_curve) >= 2 else 0.0)
                            rotator.record_round_pnl(rotator.current_strategy, round_pnl)
                            if rot.should_rotate:
                                rotator.apply_rotation(rot.suggested_strategy)
                                strategy = get_strategy(rotator.current_strategy, config)
                                telegram.send(
                                    f"*Strategy Rotated* → `{rotator.current_strategy}`\n"
                                    f"_{rot.reason}_"
                                )
                            _signals["rotation"] = {
                                "current": rotator.current_strategy,
                                "regime":  reg.regime,
                            }
                        except Exception as exc:
                            logger.debug(f"Rotation check error: {exc}")

                    # ── Weekly optimizer (background task) ────────────────────
                    if config.auto_optimize_weekly and should_run_weekly():
                        asyncio.create_task(
                            run_optimization(config, send_alert=telegram.send)
                        )

                    # ── Sync Telegram commander state ─────────────────────────
                    _tg_state.update({
                        "portfolio":    port_snapshot,
                        "risk_summary": risk_sum,
                        "perf_metrics": perf,
                        "positions":    db_mod.get_positions(),
                        "round":        round_num,
                        "strategy":     rotator.current_strategy,
                        "signals":      _signals,
                        "coin_scores":  _signals.get("coin_scores", []),
                        "protection":   eq_protector.current_state.level,
                        "session_best": sess_learner.best_hours(3),
                    })

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
        telegram.stop()
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
