"""
scripts/daily_runner.py - StrategyFactory Bot Manager master orchestrator.

Scheduled via cron (0 10 * * *) - fires daily at 10:00 AM.

Flow:
  Phase 1  -> fetch bots + HOT strategy scan
  Phase 2  -> regime detection + adaptation score + hindsight
  Phase 3  -> base verdict -> learning override -> final verdict
  Phase 4  -> execute (pause/reactivate) + JSON report + HTML dashboard

Usage:
  python scripts/daily_runner.py                  # dry-run, print verdicts
  python scripts/daily_runner.py --execute        # apply verdicts via API
  python scripts/daily_runner.py --dump-raw       # also dump raw API JSON
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.bot_client import BotAPIClient, BotInfo, StrategyData
from analysis.analytics import build_metrics
from analysis.regime_detector import detect_regime, _unknown_regime
from analysis.adaptation_score import compute_adaptation_score
from analysis.decision_engine import evaluate_bot, enhanced_verdict, PAUSE, REACTIVATE
from analysis.hindsight import evaluate_hindsight, record_decision, update_pnl_since
from scripts.generate_dashboard import generate_outputs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("daily_runner")

STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "learning_state.json"


def load_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {
        "regime_history": [], "transition_matrix": {},
        "hindsight_records": [], "calibration": {
            "pause_threshold": 40.0, "switch_threshold": 30.0, "last_updated": None
        }
    }


def save_state(state: Dict[str, Any]) -> None:
    state["calibration"]["last_updated"] = datetime.utcnow().isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _normalize_symbol(raw: str) -> str:
    """Convert bare asset names (BTC, DOGE) to ccxt spot format (BTC/USDT)."""
    raw = raw.strip().upper()
    if not raw or "/" in raw:
        return raw
    for quote in ("USDT", "USDC", "BTC", "ETH", "BNB"):
        if raw.endswith(quote) and len(raw) > len(quote):
            return f"{raw[:-len(quote)]}/{quote}"
    return f"{raw}/USDT"


def fetch_ohlcv_for_symbol(symbol: str):
    """Fetch 1h OHLCV from public APIs (no auth) for regime detection. Tries Binance then Bybit."""
    if not symbol:
        return None
    norm = _normalize_symbol(symbol)
    if not norm:
        return None
    import ccxt
    import pandas as pd
    for exchange_id in ("binance", "bybit", "kucoin"):
        try:
            exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
            raw = exchange.fetch_ohlcv(norm, "1h", limit=200)
            if raw:
                df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
                return df
        except Exception:
            continue
    log.warning(f"Could not fetch OHLCV for {symbol} ({norm}): no exchange had it")
    return None


def _infer_strategy_type(name: str) -> str:
    n = name.lower().replace("_", "").replace(" ", "")
    for key in ["momentum", "meanreversion", "scalping", "grid", "dca", "aidriven"]:
        if key in n:
            return key.replace("reversion", "_reversion").replace("driven", "_driven")
    return "ai_driven"


def scan_hot_strategies(client: BotAPIClient) -> List[Dict]:
    """Score all HOT strategies using live performance fields. Returns ranked viable list."""
    try:
        hot = client.get_hot_strategies(limit=50)
    except Exception as exc:
        log.error(f"HOT strategy scan failed: {exc}")
        return []

    results = []
    for s in hot:
        slug = s.get("slug", "")
        if not slug:
            continue
        try:
            raw = client._get(f"/api/user-api/strategies/{slug}")["data"]["strategy"]
            wr  = float(raw.get("winRateLive") or 0)
            pf  = float(raw.get("profitFactorLive") or 0)
            dd  = float(raw.get("maxDrawdownLive") or 100)
            tot = int(raw.get("tradesLive") or 0)
            p7  = float(raw.get("pct7Days") or 0)
            p30 = float(raw.get("pct30Days") or 0)
            viable = wr >= 52 and pf >= 1.1 and dd <= 40 and tot >= 20
            results.append({
                "slug": slug,
                "name": raw.get("name", slug),
                "win_rate": wr,
                "profit_factor": pf,
                "max_drawdown": dd,
                "trades": tot,
                "pct_7d": p7,
                "pct_30d": p30,
                "viable": viable,
            })
        except Exception:
            continue

    viable = [r for r in results if r["viable"]]
    viable.sort(key=lambda x: (x["win_rate"], x["profit_factor"], -x["max_drawdown"]), reverse=True)
    for i, r in enumerate(viable):
        r["rank"] = i + 1
    return viable


def process_bot(
    bot: BotInfo,
    strategy_data: StrategyData,
    state: Dict[str, Any],
    execute: bool,
    client: BotAPIClient,
) -> Dict[str, Any]:
    raw = {
        "id": strategy_data.id,
        "trades": strategy_data.trades,
        "pnl_curve": strategy_data.pnl_curve,
        "performance": strategy_data.performance,
    }

    metrics = build_metrics(raw)
    has_data = len(strategy_data.trades) >= 10

    symbol = bot.symbol or strategy_data.symbol
    df = fetch_ohlcv_for_symbol(symbol) if symbol else None
    if df is not None and not df.empty:
        regime = detect_regime(df)
    else:
        regime = _unknown_regime()
        log.warning(f"No OHLCV for '{symbol}', using default regime.")

    strategy_type = _infer_strategy_type(strategy_data.name)
    breakdown = compute_adaptation_score(metrics, regime, strategy_type)

    if strategy_data.trades:
        last_pnl = float(strategy_data.trades[-1].get("pnl", 0.0))
        update_pnl_since(state, bot.id, last_pnl)

    history = state.setdefault("regime_history", [])
    prev_regime = history[-1]["regime"] if history else None
    history.append({
        "timestamp": datetime.utcnow().isoformat(),
        "bot_id": bot.id,
        "regime": regime.regime,
        "confidence": regime.confidence,
    })
    state["regime_history"] = history[-1000:]

    # Update Markov transition matrix from observed regime sequences
    if prev_regime:
        tm = state.setdefault("transition_matrix", {})
        row = tm.setdefault(prev_regime, {})
        row[regime.regime] = row.get(regime.regime, 0) + 1

    is_paused = bot.status.upper() == "PAUSED"
    base = evaluate_bot(metrics, is_paused=is_paused, has_sufficient_data=has_data)

    hindsight_snap = evaluate_hindsight(state)
    # Use the hindsight-calibrated adaptation threshold (closes feedback loop)
    adapt_threshold = state.get("calibration", {}).get("pause_threshold", 70.0)
    verdict_result = enhanced_verdict(
        base_verdict=base,
        adaptation_score=breakdown.total,
        regret_rate=hindsight_snap.regret_rate,
        adapt_override_threshold=adapt_threshold,
    )
    final_verdict = verdict_result.verdict

    record_decision(state, bot.id, final_verdict, regime.regime, breakdown.total)

    applied = False
    if execute:
        if final_verdict == PAUSE and not is_paused:
            applied = client.pause_bot(bot.id)
            log.info(f"  [{bot.name}] pause_bot() applied={applied}")
        elif final_verdict == REACTIVATE and is_paused:
            applied = client.reactivate_bot(bot.id)
            log.info(f"  [{bot.name}] reactivate_bot() applied={applied}")

    return {
        "bot_id": bot.id,
        "bot_name": bot.name,
        "symbol": bot.symbol,
        "strategy_type": strategy_type,
        "regime": regime.regime,
        "regime_confidence": round(regime.confidence, 3),
        "metrics": metrics.to_dict(),
        "adaptation": breakdown.to_dict(),
        "base_verdict": base,
        "override_applied": verdict_result.override_applied,
        "override_reason": verdict_result.override_reason,
        "verdict": final_verdict,
        "status_applied": applied,
    }


def main():
    parser = argparse.ArgumentParser(description="StrategyFactory Daily Bot Manager")
    parser.add_argument("--execute", action="store_true", help="Apply verdicts via API")
    parser.add_argument("--dump-raw", action="store_true", help="Print raw API JSON")
    args = parser.parse_args()

    log.info("=== StrategyFactory Daily Runner - %s ===",
             datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))

    state = load_state()
    client = BotAPIClient()

    # Phase 1a: Fetch bots
    log.info("Phase 1a: Fetching bots...")
    try:
        bots: List[BotInfo] = client.get_my_bots()
    except Exception as exc:
        log.error(f"Failed to fetch bots: {exc}")
        sys.exit(1)
    log.info(f"  Found {len(bots)} bot(s)")

    # Phase 1b: HOT strategy scan
    log.info("Phase 1b: Scanning HOT strategies...")
    hot_results = scan_hot_strategies(client)
    log.info(f"  {len(hot_results)} viable HOT strategies found")
    for h in hot_results[:5]:
        log.info(
            f"    [{h['rank']:>2}] WR={h['win_rate']:.0f}%  PF={h['profit_factor']:.2f}"
            f"  DD={h['max_drawdown']:.0f}%  7d={h['pct_7d']:+.1f}%"
            f"  {h['name'][:30]}  [{h['slug']}]"
        )

    # Phase 2-3: Process each bot
    results = []
    for bot in bots:
        strategy_ref = bot.strategy_id or bot.strategy_slug
        if not strategy_ref:
            # Try name-match against HOT strategies
            bot_name_lower = bot.name.lower()
            for h in hot_results:
                if h["name"].lower() in bot_name_lower or bot_name_lower in h["name"].lower():
                    strategy_ref = h["slug"]
                    log.info(f"  Resolved '{bot.name}' -> {strategy_ref} via name match")
                    break
        if not strategy_ref:
            log.warning(f"  Skipping '{bot.name}' - could not resolve strategy slug")
            continue

        log.info(f"Processing: {bot.name} (strategy={strategy_ref})")
        try:
            strategy_data: StrategyData = client.get_strategy(strategy_ref)
        except Exception as exc:
            log.error(f"  Failed to fetch strategy {strategy_ref}: {exc}")
            continue

        if args.dump_raw:
            print(json.dumps({
                "bot": bot.__dict__,
                "strategy": {
                    "id": strategy_data.id,
                    "trades_sample": strategy_data.trades[:3],
                    "performance": strategy_data.performance,
                },
            }, indent=2))

        result = process_bot(bot, strategy_data, state, args.execute, client)
        results.append(result)

        override_tag = "OVERRIDE->" if result["override_applied"] else ""
        log.info(
            f"  [{bot.name}] regime={result['regime']} | "
            f"score={result['adaptation']['total']:.1f}/100 | "
            f"base={result['base_verdict']} | "
            f"{override_tag}verdict={result['verdict']}"
        )

    # Phase 3 final: Hindsight calibration
    log.info("Evaluating hindsight & updating calibration...")
    hindsight = evaluate_hindsight(state)
    state["calibration"]["pause_threshold"] = hindsight.new_pause_threshold
    log.info(
        f"  regret_rate={hindsight.regret_rate:.1%} | "
        f"missed_pnl=${hindsight.missed_pnl:.2f} | "
        f"pause_threshold -> {hindsight.new_pause_threshold}"
    )
    save_state(state)

    # Phase 4: Output
    log.info("Generating outputs...")
    hindsight_dict = {
        "regret_rate": hindsight.regret_rate,
        "missed_pnl": hindsight.missed_pnl,
        "calibration_delta": hindsight.calibration_delta,
        "new_pause_threshold": hindsight.new_pause_threshold,
        "records_evaluated": hindsight.records_evaluated,
    }
    generate_outputs(results, hindsight_dict, state)

    # Summary
    mode = "EXECUTE" if args.execute else "DRY RUN"
    print("\n" + "=" * 70)
    print(f"DAILY SUMMARY - {datetime.utcnow().strftime('%Y-%m-%d')}  [{mode}]")
    print("=" * 70)

    print("\nYOUR BOTS:")
    if results:
        for r in results:
            ov = " [OVERRIDE]" if r["override_applied"] else ""
            print(
                f"  {r['bot_name']:25s} | {r['regime']:15s} | "
                f"score={r['adaptation']['total']:5.1f} | "
                f"{r['base_verdict']:12s} -> {r['verdict']}{ov}"
            )
    else:
        print("  No bots processed (strategy slugs unresolvable)")

    print(f"\nTOP 10 HOT STRATEGIES:")
    print(f"  {'#':>2}  {'WR':>5}  {'PF':>5}  {'DD':>5}  {'7d':>6}  {'30d':>6}  Name")
    for h in hot_results[:10]:
        print(
            f"  {h['rank']:>2}  {h['win_rate']:>4.0f}%  {h['profit_factor']:>5.2f}"
            f"  {h['max_drawdown']:>4.0f}%  {h['pct_7d']:>+5.1f}%  {h['pct_30d']:>+5.1f}%"
            f"  {h['name']}"
        )

    print(f"\n  Hindsight: regret_rate={hindsight.regret_rate:.1%} | "
          f"pause_threshold={hindsight.new_pause_threshold:.1f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
