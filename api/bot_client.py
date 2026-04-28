"""
api/bot_client.py — StrategyFactory User API client.

Auth: x-user-api-key + x-user-api-secret headers (from .env)
Base: https://app.strategyfactory.ai

Endpoints:
  GET  /api/user-api/strategies/hot        → get_hot_strategies()
  GET  /api/user-api/strategies/hot/:id    → get_hot_strategy(id)
  GET  /api/user-api/strategies            → get_strategies()
  GET  /api/user-api/strategies/:id        → get_strategy(id)
  GET  /api/user-api/exchanges             → get_exchanges()
  GET  /api/user-api/bots                  → get_my_bots()
  POST /api/user-api/bots                  → create_bot(...)
  PATCH /api/user-api/bots/:id             → patch_bot(id, ...)

Limits: 120 req/min global | PATCH bots: 60/min, 10s cooldown per bot
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

BASE_URL = "https://app.strategyfactory.ai"
_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "bot_strategy_map.json"


def _load_strategy_map() -> Dict[str, str]:
    if _MAP_PATH.exists():
        import json
        return json.loads(_MAP_PATH.read_text(encoding="utf-8"))
    return {}


def _save_strategy_map(m: Dict[str, str]) -> None:
    import json
    _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MAP_PATH.write_text(json.dumps(m, indent=2), encoding="utf-8")


@dataclass
class BotInfo:
    id: str
    name: str
    status: str           # ACTIVE | PAUSED | DRAFT | DISABLED
    strategy_id: str
    strategy_slug: str
    symbol: str
    exchange_id: str
    exchange_name: str
    exchange_slug: str    # e.g. "toobit"
    legacy: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyData:
    id: str
    name: str
    slug: str
    symbol: str
    trades: List[Dict[str, Any]] = field(default_factory=list)
    pnl_curve: List[float] = field(default_factory=list)
    performance: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExchangeInfo:
    id: str
    name: str
    exchange: str   # e.g. "toobit"
    created_on: str


class SFClient:
    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.base = BASE_URL
        self.session = requests.Session()
        self.session.headers.update({
            "x-user-api-key":    api_key    or os.getenv("BOT_API_TOKEN", ""),
            "x-user-api-secret": api_secret or os.getenv("BOT_SECRET_API", ""),
            "Content-Type":      "application/json",
        })

    def _get(self, path: str, params: Dict = None) -> Dict:
        r = self.session.get(f"{self.base}{path}", params=params, timeout=15)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: Dict) -> Dict:
        r = self.session.post(f"{self.base}{path}", json=body, timeout=15)
        r.raise_for_status()
        return r.json()

    def _patch(self, path: str, body: Dict) -> Dict:
        r = self.session.patch(f"{self.base}{path}", json=body, timeout=15)
        r.raise_for_status()
        return r.json()

    # ── Strategies ────────────────────────────────────────────────────────────

    def get_hot_strategies(self, limit: int = 5000) -> List[Dict]:
        """GET /api/user-api/strategies/hot — HOT strategies summary list."""
        data = self._get("/api/user-api/strategies/hot", {"limit": limit})
        return data.get("data", {}).get("items", [])

    def get_hot_strategy(self, strategy_id: str) -> Dict:
        """GET /api/user-api/strategies/hot/:id — one HOT strategy (live-focused)."""
        data = self._get(f"/api/user-api/strategies/hot/{strategy_id}")
        return data.get("data", {})

    def get_strategies(self, full: bool = False, all_rows: bool = False) -> List[Dict]:
        """GET /api/user-api/strategies — strategy list (summary or full)."""
        params: Dict = {}
        if full:
            params["include"] = "full"
        if all_rows:
            params["all"] = "1"
        data = self._get("/api/user-api/strategies", params)
        return data.get("data", {}).get("items", [])

    def get_strategy(self, strategy_id: str) -> StrategyData:
        """GET /api/user-api/strategies/:id — full strategy with diagnostics."""
        data = self._get(f"/api/user-api/strategies/{strategy_id}")
        s = data.get("data", {}).get("strategy", {})
        diag = data.get("data", {}).get("diagnostics", {})

        perf = s.get("performanceForwardtest") or s.get("performance") or {}
        trades = s.get("liveTradesJson") or s.get("trades") or []
        pnl = s.get("equityCurve") or []

        return StrategyData(
            id=s.get("id", strategy_id),
            name=s.get("name", ""),
            slug=s.get("slug", ""),
            symbol=s.get("pair", s.get("coinPair", s.get("asset", s.get("symbol", "")))),
            trades=trades if isinstance(trades, list) else [],
            pnl_curve=pnl if isinstance(pnl, list) else [],
            performance=perf if isinstance(perf, dict) else {},
            diagnostics=diag,
        )

    # ── Exchanges ─────────────────────────────────────────────────────────────

    def get_exchanges(self) -> List[ExchangeInfo]:
        """GET /api/user-api/exchanges — owned exchange metadata (no secrets)."""
        data = self._get("/api/user-api/exchanges")
        return [
            ExchangeInfo(
                id=e["id"],
                name=e.get("name", ""),
                exchange=e.get("exchange", ""),
                created_on=e.get("createdOn", ""),
            )
            for e in data.get("data", {}).get("items", [])
        ]

    # ── Bots ──────────────────────────────────────────────────────────────────

    def get_my_bots(self) -> List[BotInfo]:
        """GET /api/user-api/bots — all bots with status and exchange metadata."""
        data = self._get("/api/user-api/bots")
        strategy_map = _load_strategy_map()
        bots = []
        for b in data.get("data", {}).get("items", []):
            exc = b.get("exchange", {})
            bot_id = b["id"]
            slug = (b.get("strategySlug") or b.get("strategyId")
                    or strategy_map.get(bot_id, ""))
            bots.append(BotInfo(
                id=bot_id,
                name=b.get("name", bot_id),
                status=b.get("status", "UNKNOWN"),
                strategy_id=slug,
                strategy_slug=slug,
                symbol=b.get("asset", b.get("symbol", b.get("data", {}).get("coinPair", ""))),
                exchange_id=exc.get("id", ""),
                exchange_name=exc.get("name", ""),
                exchange_slug=exc.get("slug", ""),
                legacy=b.get("legacy", {}),
            ))
        return bots

    def create_bot(
        self,
        strategy_id: str,
        exchange_id: str,
        position_type: str,   # "percentage" | "usdt"
        amount: float,
        leverage: Optional[int] = None,
        dynamic_leverage: bool = True,
        bot_name: Optional[str] = None,
    ) -> Dict:
        """POST /api/user-api/bots — create a copy bot."""
        body: Dict[str, Any] = {
            "botId":       strategy_id,
            "exchangeId":  exchange_id,
            "position":    position_type,
            "amount":      amount,
        }
        if dynamic_leverage:
            body["dynamicLeverage"] = True
        elif leverage is not None:
            body["leverage"] = leverage
        if bot_name:
            body["botName"] = bot_name
        result = self._post("/api/user-api/bots", body)
        data = result.get("data", {})
        bot_id = data.get("bot", {}).get("id") or data.get("id", "")
        if bot_id:
            m = _load_strategy_map()
            m[bot_id] = strategy_id
            _save_strategy_map(m)
        return data

    def patch_bot(self, bot_id: str, **kwargs) -> Dict:
        """PATCH /api/user-api/bots/:id — update bot fields (rate-limited: 10s cooldown)."""
        return self._patch(f"/api/user-api/bots/{bot_id}", kwargs).get("data", {})

    def pause_bot(self, bot_id: str) -> bool:
        try:
            self.patch_bot(bot_id, action="pause")
            return True
        except Exception:
            return False

    def reactivate_bot(self, bot_id: str) -> bool:
        try:
            self.patch_bot(bot_id, action="reactivate")
            return True
        except Exception:
            return False


# Backwards-compatible alias used by daily_runner
BotAPIClient = SFClient
