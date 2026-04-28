"""
scripts/backtest_scalping.py
Scalping tournament: 5m and 15m timeframes, all assets, multiple leverage levels.
Goal: find the highest daily return possible on $50.

Run: python scripts/backtest_scalping.py
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
from strategies.scalping import ScalpingStrategy
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from config import settings

STARTING_BALANCE = 50.0
START_DATE = "2025-01-01"
ASSETS = ["SOL/USDT", "ETH/USDT", "BTC/USDT"]
TIMEFRAMES = ["5m", "15m", "1h"]


async def fetch_history(symbol: str, timeframe: str, start: str) -> pd.DataFrame:
    ex = ccxt_async.binance({"enableRateLimit": True})
    since_ms = int(datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    all_candles = []
    # Cap at 10000 candles per asset/tf to keep backtest fast
    limit = 1000
    fetched = 0
    max_candles = 10000

    while fetched < max_candles:
        batch = await ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=limit)
        if not batch:
            break
        all_candles.extend(batch)
        fetched += len(batch)
        if len(batch) < limit:
            break
        since_ms = batch[-1][0] + 1
        await asyncio.sleep(0.15)

    await ex.close()
    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").reset_index(drop=True)
    return df


CONFIGS = [
    ("Scalp    3x  no-filter", lambda c: ScalpingStrategy(c),    3.0, False),
    ("Scalp    5x  no-filter", lambda c: ScalpingStrategy(c),    5.0, False),
    ("Scalp   10x  no-filter", lambda c: ScalpingStrategy(c),   10.0, False),
    ("Scalp   15x  no-filter", lambda c: ScalpingStrategy(c),   15.0, False),
    ("Scalp    5x  regime",    lambda c: ScalpingStrategy(c),    5.0, True),
    ("Scalp   10x  regime",    lambda c: ScalpingStrategy(c),   10.0, True),
    ("Scalp   15x  regime",    lambda c: ScalpingStrategy(c),   15.0, True),
    ("Momentum 5x  regime",    lambda c: MomentumStrategy(c),    5.0, True),
    ("Momentum10x  regime",    lambda c: MomentumStrategy(c),   10.0, True),
    ("MeanRev  5x  regime",    lambda c: MeanReversionStrategy(c), 5.0, True),
    ("MeanRev 10x  regime",    lambda c: MeanReversionStrategy(c), 10.0, True),
]


async def main():
    engine = BacktestEngine(settings)

    print(f"Fetching data ({START_DATE} to present, up to 10k candles per tf)...")
    datasets = {}
    for asset in ASSETS:
        for tf in TIMEFRAMES:
            key = f"{asset}|{tf}"
            try:
                df = await fetch_history(asset, tf, START_DATE)
                datasets[key] = df
                print(f"  {key}: {len(df)} candles "
                      f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()})")
            except Exception as e:
                print(f"  {key}: FAILED ({e})")
                datasets[key] = None

    results = []
    total = len(ASSETS) * len(TIMEFRAMES) * len(CONFIGS)
    done = 0

    print(f"\nRunning {total} backtests...\n")

    for asset in ASSETS:
        for tf in TIMEFRAMES:
            df = datasets.get(f"{asset}|{tf}")
            if df is None or len(df) < 250:
                done += len(CONFIGS)
                continue

            mins_per_candle = {"5m": 5, "15m": 15, "1h": 60}.get(tf, 60)
            candles_per_day = (24 * 60) / mins_per_candle

            for label, strat_fn, leverage, regime in CONFIGS:
                done += 1
                strategy = strat_fn(settings)
                try:
                    r = engine.run(
                        strategy=strategy, df=df, symbol=asset, timeframe=tf,
                        initial_balance=STARTING_BALANCE,
                        regime_filter=regime, leverage=leverage,
                    )
                    days = len(df) / candles_per_day
                    daily_pct = r.total_return_pct / days if days > 0 else 0.0
                    daily_dollar = STARTING_BALANCE * daily_pct / 100

                    results.append({
                        "asset": asset, "tf": tf, "label": label,
                        "leverage": leverage, "regime": regime,
                        "return_pct": r.total_return_pct,
                        "daily_pct": daily_pct, "daily_dollar": daily_dollar,
                        "drawdown": r.max_drawdown_pct, "sharpe": r.sharpe_ratio,
                        "win_rate": r.win_rate, "trades": r.total_trades,
                        "liqs": r.liquidations, "final_bal": r.final_balance,
                        "profit_factor": r.profit_factor,
                    })
                    print(f"  [{done:>3}/{total}] {asset} {tf} | {label:<22} | "
                          f"ret={r.total_return_pct:+7.1f}%  dd={r.max_drawdown_pct:5.1f}%  "
                          f"wr={r.win_rate:.0%}  daily=${daily_dollar:+.3f}  "
                          f"trades={r.total_trades}  liqs={r.liquidations}")
                except Exception as e:
                    done_str = f"[{done:>3}/{total}]"
                    print(f"  {done_str} {asset} {tf} | {label:<22} | ERROR: {e}")

    # Score and rank
    for r in results:
        dd_pen  = 1.0 + r["drawdown"] / 100
        liq_pen = 1.0 if r["liqs"] == 0 else 0.4
        r["score"] = (r["daily_pct"] * max(r["win_rate"], 0.01) / dd_pen) * liq_pen

    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n\n" + "=" * 110)
    print(f"  SCALPING TOURNAMENT — $50 | {START_DATE} to present")
    print("=" * 110)
    print(f"{'#':>3}  {'Asset':<10} {'TF':<4} {'Strategy':<22} {'Lev':>4} {'Reg':>4}  "
          f"{'Return':>8} {'Daily$':>7} {'Win%':>5} {'DD%':>6} {'Sharpe':>7} "
          f"{'Trades':>7} {'Liqs':>5}")
    print("-" * 110)

    for i, r in enumerate(results[:15], 1):
        reg = "Y" if r["regime"] else "n"
        mark = " <--" if i == 1 else ""
        print(f"{i:>3}  {r['asset']:<10} {r['tf']:<4} {r['label']:<22} "
              f"{r['leverage']:>3.0f}x {reg:>4}  "
              f"{r['return_pct']:>+7.1f}% {r['daily_dollar']:>+6.3f} "
              f"{r['win_rate']:>4.0%} {r['drawdown']:>5.1f}% "
              f"{r['sharpe']:>7.3f} {r['trades']:>7} {r['liqs']:>5}{mark}")

    print("=" * 110)

    if not results:
        print("No results.")
        return

    best = results[0]
    daily = best['daily_dollar']
    monthly = daily * 30

    print(f"""
BEST SCALPING STRATEGY:
  {best['asset']} | {best['tf']} | {best['label']} | {best['leverage']:.0f}x leverage
  Total return:   {best['return_pct']:+.2f}%  (${STARTING_BALANCE} -> ${best['final_bal']:.2f})
  Daily average:  {best['daily_pct']:+.4f}%  = ${daily:+.4f}/day on $50
  Monthly est:    ${monthly:.2f}
  Win rate:       {best['win_rate']:.1%}
  Max drawdown:   {best['drawdown']:.2f}%
  Sharpe:         {best['sharpe']:.3f}
  Trades:         {best['trades']}
  Liquidations:   {best['liqs']}
""")

    # Reality check
    target_pct = 25.0
    target_dollar = STARTING_BALANCE * target_pct / 100
    print(f"  TARGET: 25%/day = ${target_dollar:.2f}/day on $50")
    print(f"  BEST:   {best['daily_pct']:+.4f}%/day = ${daily:+.4f}/day")
    gap = target_dollar - daily
    if gap > 0:
        needed_lev = best['leverage'] * (target_dollar / max(daily, 0.001))
        print(f"  GAP:    ${gap:.2f}/day short — would need ~{needed_lev:.0f}x leverage (liquidation near-certain)")
    else:
        print(f"  RESULT: BEATS TARGET by ${abs(gap):.2f}/day!")
    print()


if __name__ == "__main__":
    asyncio.run(main())
