import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ccxt.async_support as ccxt_async
import pandas as pd
from datetime import datetime, timezone
from backtest.engine import BacktestEngine
from strategies.scalping import ScalpingStrategy
from config import settings

async def main():
    ex = ccxt_async.binance({"enableRateLimit": True})
    since_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    all_candles = []
    print("Fetching SOL/USDT 5m...", flush=True)
    while len(all_candles) < 10000:
        batch = await ex.fetch_ohlcv("SOL/USDT", "5m", since=since_ms, limit=1000)
        if not batch:
            break
        all_candles.extend(batch)
        if len(batch) < 1000:
            break
        since_ms = batch[-1][0] + 1
        await asyncio.sleep(0.15)
    await ex.close()

    df = pd.DataFrame(all_candles, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.drop_duplicates("timestamp").reset_index(drop=True)
    start = str(df["timestamp"].iloc[0].date())
    end   = str(df["timestamp"].iloc[-1].date())
    print(f"Got {len(df)} candles: {start} -> {end}", flush=True)

    engine = BacktestEngine(settings)
    candles_per_day = 288  # 24*60/5

    configs = [
        ("3x  no-filter", 3.0,  False),
        ("5x  no-filter", 5.0,  False),
        ("10x no-filter", 10.0, False),
        ("15x no-filter", 15.0, False),
        ("5x  regime",    5.0,  True),
        ("10x regime",    10.0, True),
        ("15x regime",    15.0, True),
    ]

    best = None
    print()
    for label, lev, regime in configs:
        try:
            r = engine.run(
                strategy=ScalpingStrategy(settings),
                df=df, symbol="SOL/USDT", timeframe="5m",
                initial_balance=50.0, regime_filter=regime, leverage=lev,
            )
            days = len(df) / candles_per_day
            daily_pct = r.total_return_pct / days
            daily_usd = 50.0 * daily_pct / 100
            score = daily_pct * max(r.win_rate, 0.01) * (1 if r.liquidations == 0 else 0.3)
            if best is None or score > best["score"]:
                best = {"label": label, "lev": lev, "regime": regime, "r": r,
                        "daily_pct": daily_pct, "daily_usd": daily_usd, "score": score}
            print(f"  Scalp {label:<14}  ret={r.total_return_pct:+7.1f}%  "
                  f"dd={r.max_drawdown_pct:5.1f}%  wr={r.win_rate:.0%}  "
                  f"daily=${daily_usd:+.3f}  trades={r.total_trades}  liqs={r.liquidations}", flush=True)
        except Exception as e:
            print(f"  Scalp {label:<14}  ERROR: {e}", flush=True)

    if best:
        r = best["r"]
        print(f"""
BEST: SOL/USDT 5m | Scalp {best['label']}
  $50 -> ${r.final_balance:.2f}  ({r.total_return_pct:+.2f}%)
  Daily avg:  {best['daily_pct']:+.4f}% = ${best['daily_usd']:+.4f}/day
  Monthly:    ${best['daily_usd']*30:.2f}
  Win rate:   {r.win_rate:.1%}
  Drawdown:   {r.max_drawdown_pct:.2f}%
  Trades:     {r.total_trades}  |  Liqs: {r.liquidations}
  Regime:     {'YES' if best['regime'] else 'no'}
""")

asyncio.run(main())
