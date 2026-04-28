"""
scripts/backtest_2025_2026.py
Fetches real 4h OHLCV from Binance (public) and runs all strategies over
2025 + 2026 YTD comparing 1x vs 2x leverage, with regime filtering.

Usage (from project root):
    .venv/Scripts/python.exe scripts/backtest_2025_2026.py
"""
from __future__ import annotations

import sys, os, time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import ccxt

from backtest.engine import BacktestEngine, BacktestResult
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.grid import GridStrategy

CFG = SimpleNamespace(
    risk_max_position_pct=0.50,
    risk_stop_loss_pct=0.03,
    risk_take_profit_pct=0.06,
    risk_max_drawdown_pct=0.90,
    risk_max_daily_loss_pct=0.50,
    grid_levels=10,
    grid_spread_pct=0.01,
    dca_interval_hours=24,
    dca_amount_usdt=100.0,
)

INITIAL_BALANCE = 10_000.0
TIMEFRAME       = "4h"
SYMBOLS         = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
START_DATE      = "2025-01-01T00:00:00Z"
STRATEGY_NAMES  = ["momentum", "mean_reversion", "grid"]


def fetch_ohlcv_full(exchange, symbol, timeframe, start_iso):
    since = exchange.parse8601(start_iso)
    all_candles = []
    limit = 1000
    print(f"  Fetching {symbol} {timeframe} ...", end="", flush=True)
    while True:
        candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not candles:
            break
        all_candles.extend(candles)
        if len(candles) < limit:
            break
        since = candles[-1][0] + 1
        time.sleep(exchange.rateLimit / 1000)
        print(".", end="", flush=True)
    print(f" {len(all_candles)} candles")
    df = pd.DataFrame(all_candles, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)


def buy_and_hold(df, window=200):
    entry = float(df.iloc[window]["close"])
    exit_ = float(df.iloc[-1]["close"])
    return (exit_ - entry) / entry * 100


def fresh_strategies():
    return {
        "momentum":       MomentumStrategy(CFG),
        "mean_reversion": MeanReversionStrategy(CFG),
        "grid":           GridStrategy(CFG),
    }


def run_combo(engine, data, regime_filter, leverage):
    label = f"{'REGIME' if regime_filter else 'NO-FILTER'} | {leverage:.0f}x leverage"
    print(f"\n--- {label} ---")
    results = {}
    for sym in SYMBOLS:
        results[sym] = {}
        strats = fresh_strategies()
        for name, strat in strats.items():
            if name == "grid":
                strat = GridStrategy(CFG)
            print(f"  [{sym}] {name:17s}", end=" ", flush=True)
            try:
                r = engine.run(
                    strat, data[sym], sym, TIMEFRAME,
                    initial_balance=INITIAL_BALANCE,
                    regime_filter=regime_filter,
                    leverage=leverage,
                )
                results[sym][name] = r
                liq_info = f"  liq={r.liquidations}" if r.liquidations else ""
                print(f"return={r.total_return_pct:+.2f}%  trades={r.total_trades}{liq_info}")
            except Exception as exc:
                print(f"ERROR: {exc}")
                results[sym][name] = _dummy(sym, name, regime_filter, leverage)
    return results


def _dummy(sym, name, regime_filter, leverage):
    return BacktestResult(
        symbol=sym, strategy=name, timeframe=TIMEFRAME,
        start_date="N/A", end_date="N/A",
        initial_balance=INITIAL_BALANCE, final_balance=INITIAL_BALANCE,
        total_return_pct=0.0, max_drawdown_pct=0.0, sharpe_ratio=0.0,
        win_rate=0.0, total_trades=0, winning_trades=0, losing_trades=0,
        avg_win_pct=0.0, avg_loss_pct=0.0, profit_factor=0.0,
        regime_filter=regime_filter, leverage=leverage,
    )


def print_comparison(all_results, bnh):
    """
    all_results: dict keyed by (regime_filter, leverage) -> {sym -> {strat -> result}}
    """
    configs = [
        (False, 1.0, "1x No-Filter"),
        (True,  1.0, "1x Regime"),
        (True,  2.0, "2x Regime"),
    ]
    sep = "=" * 96

    print(f"\n{sep}")
    print("  BACKTEST RESULTS -- 2025 + 2026 YTD | 4h | $10,000 | 0.1% fee/side")
    print(f"  Regime: Bull (EMA50>EMA200) = longs only | Bear (EMA50<EMA200) = shorts only")
    print(sep)

    for sym in SYMBOLS:
        bnh_pct = bnh[sym]
        print(f"\n  {sym}   Buy-and-Hold: {bnh_pct:+.2f}%\n")

        for name in STRATEGY_NAMES:
            print(f"  Strategy: {name}")
            header = f"    {'Metric':<22}"
            for _, _, lbl in configs:
                header += f" {lbl:>14}"
            print(header)
            print(f"    {'-'*22}" + f" {'-'*14}" * len(configs))

            def fmt_pf(r):
                pf = r.profit_factor
                return "inf" if pf == float("inf") else f"{pf:.2f}"

            rows = [
                ("Total Return %",  lambda r: f"{r.total_return_pct:+.2f}%"),
                ("Final Balance",   lambda r: f"${r.final_balance:,.0f}"),
                ("Max Drawdown %",  lambda r: f"{r.max_drawdown_pct:.2f}%"),
                ("Sharpe Ratio",    lambda r: f"{r.sharpe_ratio:.3f}"),
                ("Win Rate",        lambda r: f"{r.win_rate:.1%}"),
                ("Total Trades",    lambda r: str(r.total_trades)),
                ("Liquidations",    lambda r: str(r.liquidations)),
                ("Profit Factor",   lambda r: fmt_pf(r)),
                ("Beat B&H?",       lambda r: "YES *" if r.total_return_pct > bnh_pct else "no"),
                (">=25% target?",   lambda r: "YES *" if r.total_return_pct >= 25.0    else "no"),
            ]

            for label, fmt in rows:
                row = f"    {label:<22}"
                for rf, lv, _ in configs:
                    r = all_results[(rf, lv)][sym][name]
                    row += f" {fmt(r):>14}"
                print(row)
            print()

    print(sep)


def verdict(all_results):
    configs = [
        (False, 1.0, "1x no-filter"),
        (True,  1.0, "1x regime"),
        (True,  2.0, "2x regime"),
    ]
    print("VERDICT")
    print("-------")
    any_25 = False
    for sym in SYMBOLS:
        for name in STRATEGY_NAMES:
            for rf, lv, tag in configs:
                r = all_results[(rf, lv)][sym][name]
                hit = r.total_return_pct >= 25.0
                if hit:
                    any_25 = True
                status = "TARGET MET *" if hit else "below 25%"
                liq_note = f"  ({r.liquidations} liq)" if r.liquidations else ""
                print(f"  {name:17s} | {sym} | {tag:13s}: {r.total_return_pct:+.2f}%  {status}{liq_note}")
    print()
    if any_25:
        print("At least one configuration hit >=25% after fees.")
    else:
        print("No configuration hit >=25% after fees over this period.")
    print()


def main():
    print("\n=== Crypto Backtest 2025+2026 YTD | Regime + 2x Leverage ===\n")

    exchange = ccxt.binance({"enableRateLimit": True})
    exchange.load_markets()

    print("Downloading data ...")
    data = {sym: fetch_ohlcv_full(exchange, sym, TIMEFRAME, START_DATE) for sym in SYMBOLS}
    bnh  = {sym: buy_and_hold(data[sym], BacktestEngine.WINDOW) for sym in SYMBOLS}
    print(f"\n  BTC buy-and-hold (from backtest start): {bnh['BTC/USDT']:+.2f}%")
    print(f"  ETH buy-and-hold (from backtest start): {bnh['ETH/USDT']:+.2f}%")

    engine = BacktestEngine(CFG)
    all_results = {}

    # 1) No filter, 1x
    all_results[(False, 1.0)] = run_combo(engine, data, regime_filter=False, leverage=1.0)
    # 2) Regime filter, 1x
    all_results[(True, 1.0)]  = run_combo(engine, data, regime_filter=True,  leverage=1.0)
    # 3) Regime filter, 2x
    all_results[(True, 2.0)]  = run_combo(engine, data, regime_filter=True,  leverage=2.0)

    print("\n\n--- Regime + 2x Leverage — Full Summaries ---")
    for sym in SYMBOLS:
        for name in STRATEGY_NAMES:
            print(all_results[(True, 2.0)][sym][name].summary())

    print_comparison(all_results, bnh)
    verdict(all_results)


if __name__ == "__main__":
    main()
