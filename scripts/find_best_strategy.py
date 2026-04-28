"""
scripts/find_best_strategy.py
Comprehensive strategy tournament for a $50 account with leverage.
Tests every meaningful combination and ranks by daily profit potential.

Run: python scripts/find_best_strategy.py
"""
from __future__ import annotations

import asyncio
import sys
import os

# Force UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data.fetcher import DataFetcher
from backtest.engine import BacktestEngine
from strategies.mean_reversion import MeanReversionStrategy
from strategies.momentum import MomentumStrategy
from strategies.regime_filtered import RegimeFilteredStrategy
from config import settings

# ── Fetch via Binance public data (no API key needed) ──────────────────────────
import ccxt.async_support as ccxt_async

class PublicFetcher:
    """Uses Binance public OHLCV — no key required."""
    def __init__(self):
        self._ex = ccxt_async.binance({"enableRateLimit": True})

    async def fetch(self, symbol: str, timeframe: str, limit: int = 1500):
        import pandas as pd
        raw = await self._ex.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        # Filter 2025-01-01 onwards
        df = df[df["timestamp"] >= "2025-01-01"].copy()
        df.reset_index(drop=True, inplace=True)
        return df

    async def close(self):
        await self._ex.close()


STARTING_BALANCE = 50.0

CONFIGS = [
    # (label, strategy_factory, leverage, regime_filter)
    ("MeanRev  1x  no-filter",  lambda c: MeanReversionStrategy(c),                                 1.0, False),
    ("MeanRev  2x  no-filter",  lambda c: MeanReversionStrategy(c),                                 2.0, False),
    ("MeanRev  3x  no-filter",  lambda c: MeanReversionStrategy(c),                                 3.0, False),
    ("MeanRev  2x  regime",     lambda c: MeanReversionStrategy(c),                                 2.0, True),
    ("MeanRev  3x  regime",     lambda c: MeanReversionStrategy(c),                                 3.0, True),
    ("MeanRev  5x  regime",     lambda c: MeanReversionStrategy(c),                                 5.0, True),
    ("Momentum 1x  no-filter",  lambda c: MomentumStrategy(c),                                     1.0, False),
    ("Momentum 2x  no-filter",  lambda c: MomentumStrategy(c),                                     2.0, False),
    ("Momentum 3x  no-filter",  lambda c: MomentumStrategy(c),                                     3.0, False),
    ("Momentum 2x  regime",     lambda c: MomentumStrategy(c),                                     2.0, True),
    ("Momentum 3x  regime",     lambda c: MomentumStrategy(c),                                     3.0, True),
    ("Momentum 5x  regime",     lambda c: MomentumStrategy(c),                                     5.0, True),
]

ASSETS = ["SOL/USDT", "BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["1h", "4h"]


async def main():
    fetcher = PublicFetcher()
    engine  = BacktestEngine(settings)

    # Pre-fetch all data
    print("Fetching historical data (2025-present)...")
    datasets = {}
    for asset in ASSETS:
        for tf in TIMEFRAMES:
            key = f"{asset}|{tf}"
            limit = 1500 if tf == "1h" else 600
            try:
                df = await fetcher.fetch(asset, tf, limit=limit)
                datasets[key] = df
                print(f"  {key}: {len(df)} candles "
                      f"({df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()})")
            except Exception as e:
                print(f"  {key}: FAILED ({e})")
                datasets[key] = None

    await fetcher.close()

    # ── Run tournament ─────────────────────────────────────────────────────────
    results = []
    total   = len(ASSETS) * len(TIMEFRAMES) * len(CONFIGS)
    done    = 0

    for asset in ASSETS:
        for tf in TIMEFRAMES:
            df = datasets.get(f"{asset}|{tf}")
            if df is None or len(df) < 250:
                continue

            for label, strat_fn, leverage, regime in CONFIGS:
                done += 1
                strategy = strat_fn(settings)
                try:
                    r = engine.run(
                        strategy=strategy,
                        df=df,
                        symbol=asset,
                        timeframe=tf,
                        initial_balance=STARTING_BALANCE,
                        regime_filter=regime,
                        leverage=leverage,
                    )

                    # Daily profit estimate
                    candle_hours = int(tf.replace("h", "")) if "h" in tf else 24
                    candles_per_day = 24 / candle_hours
                    days = len(df) / candles_per_day
                    daily_pct = r.total_return_pct / days if days > 0 else 0.0
                    daily_dollar = STARTING_BALANCE * daily_pct / 100

                    results.append({
                        "asset":        asset,
                        "tf":           tf,
                        "label":        label,
                        "leverage":     leverage,
                        "regime":       regime,
                        "return_pct":   r.total_return_pct,
                        "daily_pct":    daily_pct,
                        "daily_dollar": daily_dollar,
                        "drawdown":     r.max_drawdown_pct,
                        "sharpe":       r.sharpe_ratio,
                        "win_rate":     r.win_rate,
                        "trades":       r.total_trades,
                        "liqs":         r.liquidations,
                        "final_bal":    r.final_balance,
                        "profit_factor": r.profit_factor,
                    })
                    print(f"  [{done}/{total}] {asset} {tf} | {label} | "
                          f"ret={r.total_return_pct:+.1f}% dd={r.max_drawdown_pct:.1f}% "
                          f"wr={r.win_rate:.0%} daily=${daily_dollar:+.3f}")
                except Exception as e:
                    print(f"  [{done}/{total}] {asset} {tf} | {label} | ERROR: {e}")

    # ── Rank and print ─────────────────────────────────────────────────────────
    # Score = daily_pct * win_rate / (1 + drawdown/100) * (1 if liqs==0 else 0.5)
    for r in results:
        dd_pen = 1.0 + r["drawdown"] / 100
        liq_pen = 1.0 if r["liqs"] == 0 else 0.6
        r["score"] = (r["daily_pct"] * r["win_rate"] / dd_pen) * liq_pen

    results.sort(key=lambda x: x["score"], reverse=True)

    print("\n\n" + "=" * 100)
    print(f"  STRATEGY TOURNAMENT — Starting balance: ${STARTING_BALANCE}  |  Period: 2025-present")
    print("=" * 100)
    print(f"{'#':>3}  {'Asset':<10} {'TF':<4} {'Strategy':<22} {'Lev':>4} {'Regime':>6} "
          f"{'Return':>8} {'Daily$':>8} {'Win%':>6} {'DD%':>6} {'Sharpe':>7} "
          f"{'Trades':>6} {'Liqs':>5} {'Score':>7}")
    print("-" * 100)

    top10 = results[:10]
    for i, r in enumerate(top10, 1):
        reg_str = "YES" if r["regime"] else "no"
        print(f"{i:>3}  {r['asset']:<10} {r['tf']:<4} {r['label']:<22} "
              f"{r['leverage']:>3.0f}x {reg_str:>6} "
              f"{r['return_pct']:>+7.1f}% {r['daily_dollar']:>+7.3f} "
              f"{r['win_rate']:>5.0%} {r['drawdown']:>5.1f}% "
              f"{r['sharpe']:>7.3f} {r['trades']:>6} {r['liqs']:>5} "
              f"{r['score']:>7.4f}")

    print("=" * 100)

    # ── Winner deep-dive ───────────────────────────────────────────────────────
    winner = results[0]
    daily_dollar_at_50 = winner["daily_dollar"]
    monthly = daily_dollar_at_50 * 30
    yearly  = daily_dollar_at_50 * 365

    print(f"""
WINNER: {winner['asset']} | {winner['tf']} | {winner['label']}
  Total return:    {winner['return_pct']:+.2f}%  (final balance: ${winner['final_bal']:.2f})
  Daily avg:       {winner['daily_pct']:+.4f}%  =  ${daily_dollar_at_50:+.4f}/day on $50
  Projected monthly:  ${monthly:.2f}
  Projected yearly:   ${yearly:.2f}
  Win rate:        {winner['win_rate']:.1%}
  Max drawdown:    {winner['drawdown']:.2f}%
  Sharpe ratio:    {winner['sharpe']:.3f}
  Profit factor:   {winner['profit_factor']:.2f}
  Trades:          {winner['trades']}
  Liquidations:    {winner['liqs']}
""")

    # .env settings to use
    reg_str  = "regime_mean_reversion" if (winner["regime"] and "MeanRev" in winner["label"]) else \
               "regime_momentum"       if (winner["regime"] and "Momentum" in winner["label"]) else \
               "mean_reversion"        if "MeanRev" in winner["label"] else "momentum"
    lev_int  = int(winner["leverage"])

    print("  --> To run this strategy live, update .env:")
    print(f"      STRATEGY={reg_str}")
    print(f"      TIMEFRAME={winner['tf']}")
    print(f"      TRADING_PAIRS=[\"{winner['asset']}:USDT\"]")
    print(f"      MAX_LEVERAGE={lev_int}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
