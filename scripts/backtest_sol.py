"""
scripts/backtest_sol.py
Deep SOL/USDT backtest — full 2025-2026 data, all strategy/leverage combos.
Paginates Binance public OHLCV to get ~15 months of history.

Run: python scripts/backtest_sol.py
"""
from __future__ import annotations

import asyncio
import sys
import os
from datetime import datetime, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import ccxt.async_support as ccxt_async
import pandas as pd

from backtest.engine import BacktestEngine
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from config import settings

STARTING_BALANCE = 50.0
SYMBOL = "SOL/USDT"
START_DATE = "2025-01-01"


async def fetch_full_history(symbol: str, timeframe: str, start: str) -> pd.DataFrame:
    """Paginate Binance to fetch all candles from start date to now."""
    ex = ccxt_async.binance({"enableRateLimit": True})
    since_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)

    all_candles = []
    limit = 1000
    print(f"  Fetching {symbol} {timeframe} from {start}...")

    while True:
        batch = await ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        if not batch:
            break
        all_candles.extend(batch)
        last_ts = batch[-1][0]
        if len(batch) < limit:
            break
        since_ms = last_ts + 1
        await asyncio.sleep(0.2)  # rate limit

    await ex.close()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").reset_index(drop=True)
    print(f"  Got {len(df)} candles: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}")
    return df


CONFIGS = [
    ("MeanRev  1x  no-filter", lambda c: MeanReversionStrategy(c), 1.0, False),
    ("MeanRev  2x  no-filter", lambda c: MeanReversionStrategy(c), 2.0, False),
    ("MeanRev  3x  no-filter", lambda c: MeanReversionStrategy(c), 3.0, False),
    ("MeanRev  5x  no-filter", lambda c: MeanReversionStrategy(c), 5.0, False),
    ("MeanRev  2x  regime",    lambda c: MeanReversionStrategy(c), 2.0, True),
    ("MeanRev  3x  regime",    lambda c: MeanReversionStrategy(c), 3.0, True),
    ("MeanRev  5x  regime",    lambda c: MeanReversionStrategy(c), 5.0, True),
    ("Momentum 1x  no-filter", lambda c: MomentumStrategy(c),      1.0, False),
    ("Momentum 2x  no-filter", lambda c: MomentumStrategy(c),      2.0, False),
    ("Momentum 3x  no-filter", lambda c: MomentumStrategy(c),      3.0, False),
    ("Momentum 5x  no-filter", lambda c: MomentumStrategy(c),      5.0, False),
    ("Momentum 2x  regime",    lambda c: MomentumStrategy(c),      2.0, True),
    ("Momentum 3x  regime",    lambda c: MomentumStrategy(c),      3.0, True),
    ("Momentum 5x  regime",    lambda c: MomentumStrategy(c),      5.0, True),
]

TIMEFRAMES = ["1h", "4h"]


async def main():
    engine = BacktestEngine(settings)

    # Fetch data for both timeframes
    datasets = {}
    for tf in TIMEFRAMES:
        datasets[tf] = await fetch_full_history(SYMBOL, tf, START_DATE)

    results = []
    total = len(TIMEFRAMES) * len(CONFIGS)
    done = 0

    print(f"\nRunning {total} backtests on {SYMBOL} ({START_DATE} to present)...\n")

    for tf in TIMEFRAMES:
        df = datasets[tf]
        candle_hours = int(tf.replace("h", ""))
        candles_per_day = 24 / candle_hours

        for label, strat_fn, leverage, regime in CONFIGS:
            done += 1
            strategy = strat_fn(settings)
            try:
                r = engine.run(
                    strategy=strategy,
                    df=df,
                    symbol=SYMBOL,
                    timeframe=tf,
                    initial_balance=STARTING_BALANCE,
                    regime_filter=regime,
                    leverage=leverage,
                )
                days = len(df) / candles_per_day
                daily_pct = r.total_return_pct / days if days > 0 else 0.0
                daily_dollar = STARTING_BALANCE * daily_pct / 100
                monthly = daily_dollar * 30

                results.append({
                    "tf": tf, "label": label, "leverage": leverage, "regime": regime,
                    "return_pct": r.total_return_pct, "daily_pct": daily_pct,
                    "daily_dollar": daily_dollar, "monthly": monthly,
                    "drawdown": r.max_drawdown_pct, "sharpe": r.sharpe_ratio,
                    "win_rate": r.win_rate, "trades": r.total_trades,
                    "liqs": r.liquidations, "final_bal": r.final_balance,
                    "profit_factor": r.profit_factor,
                })
                print(f"  [{done:>2}/{total}] {tf} | {label:<22} | "
                      f"ret={r.total_return_pct:+7.1f}%  dd={r.max_drawdown_pct:5.1f}%  "
                      f"wr={r.win_rate:.0%}  daily=${daily_dollar:+.3f}  "
                      f"trades={r.total_trades}  liqs={r.liquidations}")
            except Exception as e:
                print(f"  [{done:>2}/{total}] {tf} | {label:<22} | ERROR: {e}")

    # Score and rank
    for r in results:
        dd_pen = 1.0 + r["drawdown"] / 100
        liq_pen = 1.0 if r["liqs"] == 0 else 0.5
        r["score"] = (r["daily_pct"] * r["win_rate"] / dd_pen) * liq_pen

    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n\n" + "=" * 95)
    print(f"  SOL/USDT DEEP BACKTEST — $50 starting balance | {START_DATE} to present")
    print("=" * 95)
    print(f"{'#':>3}  {'TF':<4} {'Strategy':<22} {'Lev':>4} {'Regime':>6}  "
          f"{'Return':>8} {'Daily$':>7} {'Monthly$':>9} {'Win%':>5} {'DD%':>6} "
          f"{'Sharpe':>7} {'Trades':>6} {'Liqs':>5}")
    print("-" * 95)

    for i, r in enumerate(results, 1):
        reg = "YES" if r["regime"] else "no"
        marker = " <-- BEST" if i == 1 else ("  <-- 2nd" if i == 2 else "")
        print(f"{i:>3}  {r['tf']:<4} {r['label']:<22} {r['leverage']:>3.0f}x {reg:>6}  "
              f"{r['return_pct']:>+7.1f}% {r['daily_dollar']:>+6.3f} "
              f"{r['monthly']:>+8.2f}  "
              f"{r['win_rate']:>4.0%} {r['drawdown']:>5.1f}% "
              f"{r['sharpe']:>7.3f} {r['trades']:>6} {r['liqs']:>5}{marker}")

    print("=" * 95)

    best = results[0]
    print(f"""
BEST SOL STRATEGY:
  Config:          {best['tf']} | {best['label']}
  Total return:    {best['return_pct']:+.2f}%  (${STARTING_BALANCE} -> ${best['final_bal']:.2f})
  Daily average:   {best['daily_pct']:+.4f}%  = ${best['daily_dollar']:+.4f}/day
  Monthly est:     ${best['monthly']:.2f}
  Win rate:        {best['win_rate']:.1%}
  Max drawdown:    {best['drawdown']:.2f}%
  Sharpe ratio:    {best['sharpe']:.3f}
  Profit factor:   {best['profit_factor']:.2f}
  Trades:          {best['trades']}
  Liquidations:    {best['liqs']}
""")

    # vs ETH winner
    eth_daily = 0.886
    print(f"  vs ETH winner (+$0.886/day): SOL best = ${best['daily_dollar']:+.3f}/day")
    if best['daily_dollar'] > eth_daily:
        print("  --> SOL BEATS ETH! Switch to SOL.")
    else:
        diff = eth_daily - best['daily_dollar']
        print(f"  --> ETH still wins by ${diff:.3f}/day. Stick with ETH regime momentum.")


if __name__ == "__main__":
    asyncio.run(main())
