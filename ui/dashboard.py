"""
ui/dashboard.py - Live terminal dashboard powered by Rich.
Displays prices, positions, P&L, recent trades, and AI reasoning.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

logger = logging.getLogger("cryptobot.dashboard")
console = Console(legacy_windows=False)


def _color_pnl(value: float) -> str:
    if value > 0:
        return f"[bold green]+${value:,.2f}[/bold green]"
    elif value < 0:
        return f"[bold red]-${abs(value):,.2f}[/bold red]"
    return f"[white]${value:,.2f}[/white]"


def _color_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    color = "green" if value >= 0 else "red"
    return f"[{color}]{sign}{value * 100:.2f}%[/{color}]"


def _color_action(action: str) -> str:
    colors = {"buy": "bold green", "sell": "bold red", "hold": "yellow"}
    return f"[{colors.get(action, 'white')}]{action.upper()}[/{colors.get(action, 'white')}]"


class Dashboard:
    """Rich live dashboard for the trading bot."""

    def __init__(self, config):
        self._config = config
        self._prices: Dict[str, float] = {}
        self._portfolio: Dict = {}
        self._recent_trades: List[Dict] = []
        self._last_decision: Optional[Dict] = None
        self._risk_summary: Dict = {}
        self._perf_metrics: Dict = {}
        self._mode = config.trading_mode.upper()
        self._strategy = config.strategy

    def update(
        self,
        prices: Dict[str, float],
        portfolio: Dict,
        recent_trades: List[Dict],
        last_decision: Optional[Dict] = None,
        risk_summary: Optional[Dict] = None,
        perf_metrics: Optional[Dict] = None,
    ) -> None:
        self._prices = prices
        self._portfolio = portfolio
        self._recent_trades = recent_trades
        self._last_decision = last_decision
        self._risk_summary = risk_summary or {}
        self._perf_metrics = perf_metrics or {}

    def render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._header_panel(), size=4),
            Layout(name="main"),
            Layout(self._status_panel(), size=6),
        )
        layout["main"].split_row(
            Layout(self._portfolio_panel(), ratio=2),
            Layout(name="right", ratio=3),
        )
        layout["main"]["right"].split_column(
            Layout(self._prices_panel()),
            Layout(self._trades_panel()),
        )
        return layout

    def _header_panel(self) -> Panel:
        mode_color = "yellow" if self._mode == "PAPER" else "bold red"
        title = Text()
        title.append("[BOT] CRYPTO TRADING BOT  ", style="bold white")
        title.append(f"[{self._mode}]", style=mode_color)
        title.append(f"  Strategy: {self._strategy}", style="cyan")
        title.append(f"  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", style="dim")
        return Panel(title, box=box.DOUBLE_EDGE, style="bold")

    def _portfolio_panel(self) -> Panel:
        p = self._portfolio
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Key", style="dim")
        table.add_column("Value", justify="right")

        total = p.get("total_value", 0)
        initial = p.get("initial_value", total)
        pnl = p.get("pnl_total", 0)
        pnl_pct = p.get("pnl_pct", 0)
        drawdown = p.get("drawdown", 0)
        cash = p.get("cash_balance", 0)

        table.add_row("Portfolio Value", f"[bold white]${total:,.2f}[/bold white]")
        table.add_row("Initial Balance", f"${initial:,.2f}")
        table.add_row("Cash (USDT)", f"${cash:,.2f}")
        table.add_row("Total P&L", _color_pnl(pnl))
        table.add_row("Return", _color_pct(pnl_pct))
        table.add_row("Max Drawdown", f"[{'red' if drawdown > 0.05 else 'green'}]{drawdown * 100:.2f}%[/{'red' if drawdown > 0.05 else 'green'}]")

        # Performance
        if self._perf_metrics:
            table.add_row("", "")
            table.add_row("[dim]-- Performance --[/dim]", "")
            table.add_row("Win Rate", f"{self._perf_metrics.get('win_rate', 0):.1%}")
            table.add_row("Total Trades", str(self._perf_metrics.get("total_trades", 0)))
            if "sharpe_ratio" in self._perf_metrics:
                table.add_row("Sharpe", f"{self._perf_metrics['sharpe_ratio']:.3f}")

        # Open positions
        positions = p.get("positions", [])
        if positions:
            table.add_row("", "")
            table.add_row("[dim]-- Open Positions --[/dim]", "")
            for pos in positions:
                sym = pos.get("symbol", "?")
                entry = pos.get("entry_price", 0)
                amt = pos.get("amount", 0)
                current_price = self._prices.get(sym, entry)
                unrealized = (current_price - entry) * amt
                table.add_row(sym, _color_pnl(unrealized))

        # Risk
        if self._risk_summary:
            halted = self._risk_summary.get("halted", False)
            if halted:
                table.add_row("", "")
                table.add_row("[bold red]TRADING HALTED[/bold red]",
                              f"[red]{self._risk_summary.get('halt_reason', '')}[/red]")

        return Panel(table, title="[bold]Portfolio[/bold]", box=box.ROUNDED)

    def _prices_panel(self) -> Panel:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
        table.add_column("Symbol", style="white")
        table.add_column("Price", justify="right")
        table.add_column("24h Change", justify="right")

        for symbol, price in self._prices.items():
            table.add_row(symbol, f"${price:,.4f}", "[dim]-[/dim]")

        # Last AI decision
        if self._last_decision:
            d = self._last_decision
            action = d.get("action", "hold")
            confidence = d.get("confidence", 0)
            reasoning = d.get("reasoning", "")[:80]

            decision_text = Text()
            decision_text.append("\nLast AI Decision: ", style="dim")
            decision_text.append(f"{action.upper()} ", style="bold green" if action == "buy" else "bold red" if action == "sell" else "yellow")
            decision_text.append(f"({confidence:.0%} confidence)\n", style="dim")
            decision_text.append(f"{reasoning}...", style="italic dim")

            return Panel(
                Table.grid(padding=1).add_row(table, decision_text),
                title="[bold]Live Prices & AI Signal[/bold]",
                box=box.ROUNDED,
            )

        return Panel(table, title="[bold]Live Prices[/bold]", box=box.ROUNDED)

    def _trades_panel(self) -> Panel:
        table = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
        table.add_column("Time", style="dim", width=10)
        table.add_column("Symbol", width=10)
        table.add_column("Side", width=6)
        table.add_column("Price", justify="right", width=12)
        table.add_column("Amount", justify="right", width=10)
        table.add_column("P&L", justify="right", width=10)

        for trade in self._recent_trades[:8]:
            ts = str(trade.get("timestamp", ""))[:10]
            sym = trade.get("symbol", "")
            side = trade.get("side", "")
            price = trade.get("price", 0)
            amount = trade.get("amount", 0)
            pnl = trade.get("pnl")

            side_fmt = "[green]BUY[/green]" if side == "buy" else "[red]SELL[/red]"
            pnl_fmt = _color_pnl(pnl) if pnl is not None else "[dim]-[/dim]"

            table.add_row(ts, sym, side_fmt, f"${price:,.2f}", f"{amount:.4f}", pnl_fmt)

        return Panel(table, title="[bold]Recent Trades[/bold]", box=box.ROUNDED)

    def _status_panel(self) -> Panel:
        rs = self._risk_summary
        parts = [
            f"Exchange: [cyan]{self._config.exchange}[/cyan]",
            f"Pairs: [cyan]{', '.join(self._config.trading_pairs)}[/cyan]",
            f"Timeframe: [cyan]{self._config.timeframe}[/cyan]",
            f"Daily P&L: {_color_pnl(rs.get('daily_pnl', 0))}",
            f"Drawdown: [{'red' if rs.get('drawdown_pct', 0) > 0.05 else 'green'}]{rs.get('drawdown_pct', 0) * 100:.2f}%[/{'red' if rs.get('drawdown_pct', 0) > 0.05 else 'green'}]",
            f"Status: {'[bold red]HALTED[/bold red]' if rs.get('halted') else '[bold green]ACTIVE[/bold green]'}",
        ]
        return Panel(" | ".join(parts), box=box.SIMPLE, style="dim")


def print_startup_banner(config) -> None:
    """Print a startup banner with configuration summary."""
    console.print(Panel(
        f"[bold yellow]>>> CRYPTO TRADING BOT - STARTING UP[/bold yellow]\n\n"
        f"Mode:       [{'yellow' if config.trading_mode == 'paper' else 'bold red'}]{config.trading_mode.upper()}[/{'yellow' if config.trading_mode == 'paper' else 'bold red'}]\n"
        f"Strategy:   [cyan]{config.strategy}[/cyan]\n"
        f"Exchange:   [cyan]{config.exchange}[/cyan]\n"
        f"Pairs:      [cyan]{', '.join(config.trading_pairs)}[/cyan]\n"
        f"Timeframe:  [cyan]{config.timeframe}[/cyan]\n"
        f"AI Model:   [cyan]claude-opus-4-6[/cyan]\n"
        f"Loop:       every {config.loop_interval_seconds}s\n",
        box=box.DOUBLE_EDGE,
    ))
