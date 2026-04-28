"""
scripts/weekly_optimizer.py — Automated weekly parameter optimization.

Runs a grid of backtests, scores by Sharpe × (1-MaxDD) × WinRate,
writes the best strategy/timeframe back to .env, sends Telegram summary.

Triggered from main.py once per week via should_run_weekly() check.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LAST_OPT_FILE = ROOT / "data" / ".last_optimization"

logger = logging.getLogger("cryptobot.optimizer")

_STRATEGIES  = ["regime_momentum", "mean_reversion", "regime_scalping", "momentum"]
_TIMEFRAMES  = ["1h", "4h"]


def should_run_weekly() -> bool:
    """Return True if ≥7 days have elapsed since last optimization."""
    if not LAST_OPT_FILE.exists():
        return True
    try:
        ts   = float(LAST_OPT_FILE.read_text().strip())
        days = (datetime.now(timezone.utc).timestamp() - ts) / 86400
        return days >= 7.0
    except Exception:
        return True


def _mark_ran() -> None:
    LAST_OPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_OPT_FILE.write_text(str(datetime.now(timezone.utc).timestamp()))


async def run_optimization(
    config,
    send_alert: Optional[Callable[[str], None]] = None,
) -> Dict:
    """
    Grid-search strategy × timeframe combinations.
    Returns best params dict {strategy, timeframe} and updates .env.
    """
    from data.fetcher import DataFetcher
    from backtest.engine import BacktestEngine
    from strategies.registry import get_strategy
    from exchange.paper_exchange import PaperExchange

    logger.info("[OPTIMIZER] Weekly optimization starting...")
    if send_alert:
        send_alert("*Weekly Optimizer* — Running backtests, please wait...")

    paper_exc = PaperExchange(config)
    fetcher   = DataFetcher(paper_exc, config)
    engine    = BacktestEngine(config)

    # Fetch data once per symbol
    data_cache: Dict[str, object] = {}
    for sym in config.trading_pairs:
        try:
            df = await fetcher.fetch_ohlcv(sym, "1h", limit=500, use_cache=True)
            if not df.empty:
                data_cache[sym] = df
                logger.info(f"[OPTIMIZER] Fetched {len(df)} candles for {sym}")
        except Exception as exc:
            logger.warning(f"[OPTIMIZER] Could not fetch {sym}: {exc}")

    await paper_exc.close()

    if not data_cache:
        logger.warning("[OPTIMIZER] No data — skipping")
        return {}

    results: List[Dict] = []
    best_score  = float("-inf")
    best_params: Dict = {}

    for strat_name in _STRATEGIES:
        for tf in _TIMEFRAMES:
            for sym, df in data_cache.items():
                try:
                    strategy = get_strategy(strat_name, config)
                    result   = engine.run(
                        strategy=strategy, df=df, symbol=sym,
                        timeframe=tf,
                        initial_balance=config.paper_initial_balance,
                    )
                    # Composite score: penalise drawdown and reward win-rate
                    score = (result.sharpe_ratio
                             * max(0.0, 1.0 - result.max_drawdown_pct / 100)
                             * result.win_rate)
                    results.append({
                        "strategy":   strat_name,
                        "timeframe":  tf,
                        "symbol":     sym,
                        "return_pct": result.total_return_pct,
                        "sharpe":     result.sharpe_ratio,
                        "win_rate":   result.win_rate,
                        "drawdown":   result.max_drawdown_pct,
                        "score":      score,
                    })
                    if score > best_score:
                        best_score  = score
                        best_params = {"strategy": strat_name, "timeframe": tf}
                except Exception as exc:
                    logger.warning(f"[OPTIMIZER] {strat_name}/{tf}/{sym} failed: {exc}")

    if best_params:
        _apply_best_params(best_params)
        top3 = sorted(results, key=lambda x: x["score"], reverse=True)[:3]
        logger.info(f"[OPTIMIZER] Best: {best_params} (score={best_score:.4f})")

        if send_alert:
            lines = ["*Weekly Optimization Complete* \n"]
            for i, r in enumerate(top3, 1):
                lines.append(
                    f"{i}. `{r['strategy']}` {r['timeframe']} — "
                    f"{r['return_pct']:+.1f}% return, {r['win_rate']:.0%} WR, "
                    f"Sharpe {r['sharpe']:.2f}"
                )
            lines.append(
                f"\n_Applied: `{best_params['strategy']}` / `{best_params['timeframe']}`_"
            )
            send_alert("\n".join(lines))

    _mark_ran()
    return best_params


def _apply_best_params(params: Dict) -> None:
    """Overwrite STRATEGY= and TIMEFRAME= lines in .env."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    lines   = env_path.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if line.startswith("STRATEGY="):
            updated.append(f"STRATEGY={params['strategy']}")
        elif line.startswith("TIMEFRAME="):
            updated.append(f"TIMEFRAME={params['timeframe']}")
        else:
            updated.append(line)
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    logger.info(f"[OPTIMIZER] .env updated: {params}")
