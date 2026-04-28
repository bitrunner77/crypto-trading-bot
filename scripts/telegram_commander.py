"""
scripts/telegram_commander.py — Bidirectional Telegram command center.

Replaces the one-way telegram_alerts.py with a full command bot.
Runs as a daemon thread started from main.py.

Commands (send from the configured Telegram chat):
  /status    — live overview
  /balance   — portfolio summary
  /positions — open positions
  /pause     — pause trading
  /resume    — resume trading
  /strategy <name> — rotate strategy
  /help      — list commands
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("cryptobot.telegram_cmd")


class TelegramCommander:
    """
    Long-polls Telegram for incoming commands and responds inline.
    `state` is a shared dict written by the main bot each round.
    """

    def __init__(
        self,
        token:     str,
        chat_id:   str,
        state:     Dict[str, Any],
        pause_cb:  Optional[Callable[[], None]]      = None,
        resume_cb: Optional[Callable[[], None]]      = None,
        rotate_cb: Optional[Callable[[str], None]]   = None,
    ) -> None:
        self._token    = token
        self._chat_id  = chat_id
        self._state    = state
        self._pause    = pause_cb
        self._resume   = resume_cb
        self._rotate   = rotate_cb
        self._offset   = 0
        self._running  = False

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._token or not self._chat_id:
            logger.info("Telegram commander: TOKEN/CHAT_ID not set — skipped")
            return
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        logger.info("Telegram command center started")
        self._send("*Bot started* — command center online\n/help for commands")

    def stop(self) -> None:
        self._running = False

    def send(self, message: str) -> None:
        """Send an alert (called from the main bot for trade notifications)."""
        self._send(message)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while self._running:
            try:
                for upd in self._get_updates():
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    text    = msg.get("text", "").strip()
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    if chat_id == self._chat_id and text.startswith("/"):
                        self._handle(text)
            except Exception as exc:
                logger.debug(f"Telegram poll error: {exc}")
            time.sleep(2)

    def _handle(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd   = parts[0].lower().split("@")[0]
        arg   = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/help":
            self._send(
                "*Commands*\n"
                "/status — live overview\n"
                "/balance — portfolio balance\n"
                "/positions — open positions\n"
                "/coins — coin rotation rankings\n"
                "/protect — equity protection level\n"
                "/leverage — current dynamic leverage info\n"
                "/session — best trading hours learned\n"
                "/hedge — active hedges\n"
                "/news — news sentiment signals\n"
                "/portfolio — slot allocation + BTC dominance\n"
                "/growth — monthly growth progress\n"
                "/pause — pause trading\n"
                "/resume — resume trading\n"
                "/strategy \\<name\\> — rotate strategy\n"
                "/help — this message"
            )

        elif cmd in ("/status", "/balance"):
            s    = self._state
            port = s.get("portfolio", {})
            risk = s.get("risk_summary", {})
            perf = s.get("perf_metrics", {})
            total    = port.get("total_value", 0)
            pnl      = port.get("pnl_total", 0)
            pnl_pct  = port.get("pnl_pct", 0) * 100
            wr       = (perf.get("win_rate") or 0) * 100
            strategy = s.get("strategy", "unknown")
            mode     = s.get("mode", "unknown").upper()
            halted   = "HALTED" if risk.get("halted") else "ACTIVE"
            self._send(
                f"*Bot Status [{halted}]*\n"
                f"Mode: `{mode}` | Strategy: `{strategy}`\n"
                f"Balance: `${total:,.2f}`\n"
                f"P&L: `{pnl:+.2f}` ({pnl_pct:+.2f}%)\n"
                f"Win Rate: `{wr:.1f}%` | Round: `{s.get('round', 0)}`"
            )

        elif cmd == "/positions":
            positions = self._state.get("positions", [])
            if not positions:
                self._send("No open positions")
            else:
                lines = ["*Open Positions*"]
                for p in positions:
                    lines.append(
                        f"`{p.get('symbol')}` {(p.get('side') or '').upper()} "
                        f"@ ${p.get('entry_price', 0):.4f}"
                    )
                self._send("\n".join(lines))

        elif cmd == "/pause":
            if self._pause:
                self._pause()
                self._send("Trading *paused* — /resume to restart")
            else:
                self._send("Pause not available in current mode")

        elif cmd == "/resume":
            if self._resume:
                self._resume()
                self._send("Trading *resumed*")
            else:
                self._send("Resume not available")

        elif cmd == "/coins":
            scores = self._state.get("coin_scores", [])
            if not scores:
                self._send("No coin rankings yet — runs every 6 rounds")
            else:
                lines = ["*Coin Rankings*"]
                for s in scores:
                    star = "⭐" if s.get("recommended") else "  "
                    lines.append(
                        f"{star} `{s.get('symbol','?')}` — score `{s.get('composite',0):.3f}` "
                        f"mom `{s.get('momentum',0):+.1f}%`"
                    )
                self._send("\n".join(lines))

        elif cmd == "/protect":
            level   = self._state.get("protection", "GREEN")
            sigs    = self._state.get("signals", {})
            prot    = sigs.get("protection", {})
            dd      = prot.get("drawdown", "0.0%")
            msg     = prot.get("message", "Equity healthy")
            self._send(f"*Equity Protection*\nLevel: `{level}`\nDrawdown: `{dd}`\n_{msg}_")

        elif cmd == "/leverage":
            sigs = self._state.get("signals", {})
            rot  = sigs.get("rotation", {})
            self._send(
                f"*Dynamic Leverage*\n"
                f"Current strategy: `{rot.get('current', '?')}`\n"
                f"Regime: `{rot.get('regime', '?')}`\n"
                f"Protection: `{self._state.get('protection', 'GREEN')}`\n"
                f"_(leverage auto-adjusts each trade)_"
            )

        elif cmd == "/session":
            best = self._state.get("session_best", [])
            if not best:
                self._send("No session data learned yet — need 20+ trades per hour")
            else:
                hours_str = ", ".join(f"`{h:02d}:00 UTC`" for h in best)
                self._send(f"*Best Trading Hours*\n{hours_str}\n_(learned from trade history)_")

        elif cmd == "/growth":
            sigs = self._state.get("signals", {})
            growth = sigs.get("growth", "No growth data yet")
            self._send(f"*Monthly Growth Engine*\n`{growth}`")

        elif cmd == "/news":
            sigs = self._state.get("signals", {})
            lines = ["*News Sentiment*"]
            for sym, s in sigs.items():
                if isinstance(s, dict) and s.get("news"):
                    lines.append(f"`{sym}`: {s['news']}")
            if len(lines) == 1:
                lines.append("No news signals this round")
            self._send("\n".join(lines))

        elif cmd == "/portfolio":
            sigs   = self._state.get("signals", {})
            alloc  = sigs.get("allocation", "Not yet allocated")
            dom    = next((v.get("dominance") for v in sigs.values()
                           if isinstance(v, dict) and v.get("dominance")), None)
            self._send(
                f"*Portfolio Slots*\n`{alloc}`"
                + (f"\n\n*BTC Dominance*\n`{dom}`" if dom else "")
            )

        elif cmd == "/hedge":
            hedges = self._state.get("signals", {}).get("hedges", [])
            if not hedges:
                self._send("No active hedges")
            else:
                self._send(f"*Active Hedges*\n" + "\n".join(f"  `{h}`" for h in hedges))

        elif cmd == "/strategy":
            valid = ("ai_driven", "momentum", "mean_reversion",
                     "regime_momentum", "regime_scalping", "dca")
            if arg and self._rotate:
                self._rotate(arg)
                self._send(f"Strategy rotated to `{arg}`")
            else:
                self._send(f"Usage: /strategy \\<name\\>\nAvailable: {', '.join(f'`{s}`' for s in valid)}")

        else:
            self._send(f"Unknown command `{cmd}` — /help")

    def _get_updates(self) -> list:
        url    = f"https://api.telegram.org/bot{self._token}/getUpdates"
        params = {"offset": self._offset, "timeout": 1, "limit": 10}
        with urllib.request.urlopen(
            url + "?" + urllib.parse.urlencode(params), timeout=5
        ) as resp:
            return json.loads(resp.read()).get("result", [])

    def _send(self, message: str) -> None:
        if not self._token or not self._chat_id:
            return
        try:
            url     = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = json.dumps({
                "chat_id":    self._chat_id,
                "text":       message,
                "parse_mode": "Markdown",
            }).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:
            logger.debug(f"Telegram send failed: {exc}")


# ── Module-level send_alert for backwards compatibility ──────────────────────

def send_alert(message: str) -> None:
    """Drop-in replacement for the old telegram_alerts.send_alert."""
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        url     = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": message,
                               "parse_mode": "Markdown"}).encode()
        req     = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
