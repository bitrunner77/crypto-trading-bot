"""
ai/analyst.py — Claude Opus market analyst: the AI brain of the trading bot.
Performs two-stage analysis: macro context → asset-specific trade decision.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, Optional, Tuple

import anthropic

from ai.prompts import (
    SYSTEM_PROMPT,
    build_asset_prompt,
    build_haiku_screen_prompt,
    build_macro_prompt,
    build_risk_prompt,
)
from ai.schemas import MacroContext, RiskAssessment, TradeDecision
from analysis.market_context import MarketSnapshot

logger = logging.getLogger("cryptobot.analyst")


class AIResponseParseError(ValueError):
    """Raised when the model's text response cannot be coerced to a JSON dict."""


class MarketAnalyst:
    """
    Uses Claude Opus to analyze market conditions and generate trade decisions.
    Two-stage pipeline:
      1. Macro analysis (optional, for BTC pair context)
      2. Asset-level analysis → TradeDecision
      3. Risk validation → RiskAssessment
    """

    def __init__(self, config):
        self._client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self._config = config
        # Configurable via config (defaults preserve current behaviour).
        self._model_opus = getattr(config, "ai_model_opus", "claude-opus-4-7")
        self._model_haiku = getattr(config, "ai_model_haiku", "claude-haiku-4-5-20251001")
        self._max_tokens = getattr(config, "ai_max_tokens", 4096)
        self._screen_tokens = getattr(config, "ai_screen_max_tokens", 256)
        self._escalate_threshold = getattr(config, "ai_escalate_threshold", 0.55)
        logger.info(
            f"Market analyst initialized — "
            f"screener: [bold]{self._model_haiku}[/bold]  "
            f"analyst: [bold]{self._model_opus}[/bold]"
        )

    def analyze(
        self, snapshot: MarketSnapshot, recent_pnl: list = None
    ) -> Tuple[TradeDecision, Optional[RiskAssessment]]:
        """
        Two-stage analysis pipeline:
          Stage 1 — Haiku screener (cheap): is there a signal worth acting on?
          Stage 2 — Opus analyst (expensive): only called when Haiku escalates.
        Returns (decision, risk_assessment).
        """
        logger.info(f"[Haiku] Screening {snapshot.symbol} @ ${snapshot.current_price:,.4f}")

        # ── Stage 1: Haiku fast screen ─────────────────────────────────────────
        screen = self._call_haiku_screen(snapshot)
        signal_strength = screen.get("signal_strength", 0.0)
        escalate = screen.get("escalate", False)
        reason = screen.get("reason", "")

        logger.info(
            f"[Haiku] {snapshot.symbol} → "
            f"signal=[bold]{screen.get('signal','hold').upper()}[/bold] "
            f"strength={signal_strength:.0%}  escalate={escalate}  | {reason}"
        )

        # If Haiku says pass → return hold immediately, no Opus call
        if not escalate or signal_strength < self._escalate_threshold:
            return TradeDecision(
                action="hold",
                symbol=snapshot.symbol,
                size_pct=0.0,
                leverage=1,
                stop_loss_pct=self._config.risk_stop_loss_pct,
                take_profit_pct=self._config.risk_take_profit_pct,
                confidence=signal_strength,
                timeframe=snapshot.timeframe,
                reasoning=f"[Haiku screener] Skipped — {reason}",
                key_signals=[],
                risk_level="low",
                changes_from_last_round="",
            ), None

        # ── Stage 2: Opus deep analysis ────────────────────────────────────────
        logger.info(f"[Opus] Escalated {snapshot.symbol} — running full analysis...")
        decision = self._call_asset_analysis(snapshot, recent_pnl or [])
        logger.info(
            f"[Opus] Decision: [bold]{decision.action.upper()}[/bold] "
            f"(confidence: {decision.confidence:.0%}, size: {decision.size_pct:.0%}, "
            f"leverage: {decision.leverage}x)"
        )

        # ── RULE: State reasoning out loud before every trade ──────────────────
        if decision.action != "hold":
            logger.info(f"\n{'─'*60}")
            logger.info(f"[bold cyan]REASONING (spoken before trade):[/bold cyan]")
            logger.info(f"{decision.reasoning}")
            if decision.changes_from_last_round:
                logger.info(f"\n[bold yellow]CHANGES FROM LAST ROUND:[/bold yellow]")
                logger.info(f"{decision.changes_from_last_round}")
            logger.info(f"{'─'*60}\n")

        # ── Stage 3: Risk validation (only for long/short) ─────────────────────
        risk = None
        if decision.action in ("long", "short"):
            risk = self._call_risk_validation(decision, snapshot)
            if not risk.approved:
                logger.warning(f"Risk manager rejected trade: {risk.concerns}")
                decision = TradeDecision(
                    action="hold",
                    symbol=decision.symbol,
                    size_pct=0.0,
                    leverage=1,
                    stop_loss_pct=decision.stop_loss_pct,
                    take_profit_pct=decision.take_profit_pct,
                    confidence=decision.confidence,
                    timeframe=decision.timeframe,
                    reasoning=f"Blocked by risk manager: {'; '.join(risk.concerns)}",
                    key_signals=decision.key_signals,
                    risk_level="high",
                    changes_from_last_round="",
                )
            else:
                updates = {}
                if risk.adjusted_size_pct is not None:
                    updates["size_pct"] = risk.adjusted_size_pct
                if risk.adjusted_leverage is not None:
                    updates["leverage"] = risk.adjusted_leverage
                if updates:
                    decision = decision.model_copy(update=updates)

        return decision, risk

    def get_macro_context(self, btc_snapshot: MarketSnapshot) -> Optional[MacroContext]:
        """Optional macro context from BTC analysis."""
        try:
            prompt = build_macro_prompt(btc_snapshot.indicators)
            raw = self._call_claude(prompt)
            data = _parse_json(raw)
            return MacroContext(**data)
        except Exception as e:
            logger.warning(f"Macro analysis failed (non-fatal): {e}")
            return None

    # ── Private helpers ────────────────────────────────────────────────────────

    def _call_haiku_screen(self, snapshot: MarketSnapshot) -> dict:
        """Fast cheap Haiku screen — returns escalate bool + signal strength."""
        prompt = build_haiku_screen_prompt(snapshot)
        try:
            raw = self._call_claude(
                prompt, model=self._model_haiku, max_tokens=self._screen_tokens
            )
            return _parse_json(raw)
        except Exception as e:
            logger.warning(f"Haiku screen failed for {snapshot.symbol}: {e} — defaulting to escalate")
            return {"escalate": True, "signal": "hold", "signal_strength": 0.6, "reason": f"screen error: {e}"}

    def _call_asset_analysis(self, snapshot: MarketSnapshot, recent_pnl: list) -> TradeDecision:
        prompt = build_asset_prompt(snapshot, recent_pnl)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            try:
                raw = self._call_claude(prompt)
                data = _parse_json(raw)
                data.setdefault("leverage", 1)
                data.setdefault("changes_from_last_round", "")
                decision = TradeDecision(**data)
                _validate_decision(decision)
                return decision
            except Exception as e:
                last_err = e
                logger.warning(f"Asset analysis attempt {attempt + 1} failed: {e}")
        return self._safe_hold(snapshot, f"AI analysis failed after 3 attempts: {last_err}")

    def _safe_hold(self, snapshot: MarketSnapshot, reason: str) -> TradeDecision:
        """Build a defensive 'hold' decision when AI output cannot be trusted."""
        return TradeDecision(
            action="hold",
            symbol=snapshot.symbol,
            size_pct=0.0,
            leverage=1,
            stop_loss_pct=self._config.risk_stop_loss_pct,
            take_profit_pct=self._config.risk_take_profit_pct,
            confidence=0.0,
            timeframe=snapshot.timeframe,
            reasoning=reason,
            key_signals=[],
            risk_level="high",
        )

    def _call_risk_validation(
        self, decision: TradeDecision, snapshot: MarketSnapshot
    ) -> RiskAssessment:
        initial_value = self._config.paper_initial_balance
        current_value = snapshot.portfolio_value
        drawdown = max(0.0, (initial_value - current_value) / initial_value) if initial_value > 0 else 0.0

        prompt = build_risk_prompt(
            decision=decision.model_dump(),
            portfolio_value=snapshot.portfolio_value,
            cash_balance=snapshot.cash_balance,
            current_drawdown=drawdown,
            config=self._config,
        )
        try:
            raw = self._call_claude(prompt)
            data = _parse_json(raw)
            return RiskAssessment(**data)
        except Exception as e:
            logger.warning(f"Risk validation failed (auto-approve with caution): {e}")
            return RiskAssessment(approved=True, reasoning=f"Validation error: {e}")

    def _call_claude(self, user_prompt: str, model: Optional[str] = None,
                     max_tokens: Optional[int] = None) -> str:
        """Make a synchronous call to Claude and return the text response.
        Records token usage with the global cost tracker so dashboards can
        show running session cost.
        """
        used_model = model or self._model_opus
        message = self._client.messages.create(
            model=used_model,
            max_tokens=max_tokens or self._max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        try:
            from utils.cost_tracker import tracker
            tracker.record(used_model, usage=getattr(message, "usage", None))
        except Exception as e:  # cost tracking must never block trading
            logger.debug(f"cost tracker skipped: {e}")
        return message.content[0].text


def _parse_json(text: str) -> Dict:
    """Extract and parse a JSON object from Claude's response.

    Handles markdown code-block fences and prose around the JSON. Raises
    AIResponseParseError if no JSON object can be located or it doesn't
    parse to a dict.
    """
    if not text or not text.strip():
        raise AIResponseParseError("empty response from model")

    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)

    start = text.find("{")
    end = text.rfind("}") + 1
    if start < 0 or end <= start:
        raise AIResponseParseError(f"no JSON object in response: {text[:120]!r}")

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        raise AIResponseParseError(f"invalid JSON: {e}") from e

    if not isinstance(data, dict):
        raise AIResponseParseError(f"expected JSON object, got {type(data).__name__}")
    return data


def _validate_decision(decision: TradeDecision) -> None:
    """Reject self-contradictory decisions before they can reach the trader."""
    if decision.action in ("long", "short"):
        if decision.size_pct <= 0:
            raise AIResponseParseError(
                f"action={decision.action} requires size_pct>0 (got {decision.size_pct})"
            )
        if decision.leverage < 1:
            raise AIResponseParseError(
                f"action={decision.action} requires leverage>=1 (got {decision.leverage})"
            )
