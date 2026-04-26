"""
data/database.py — SQLite persistence for trades, portfolio snapshots, and OHLCV cache.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Generator, List, Optional

DB_PATH = Path(__file__).parent.parent / "cryptobot.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db() -> Generator[sqlite3.Connection, None, None]:
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables if they don't exist, then run any required migrations."""
    with db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                side        TEXT    NOT NULL,   -- buy | sell | long | short | close
                price       REAL    NOT NULL,
                amount      REAL    NOT NULL,
                cost        REAL    NOT NULL,
                fee         REAL    NOT NULL DEFAULT 0,
                strategy    TEXT    NOT NULL,
                mode        TEXT    NOT NULL,   -- paper | live
                reasoning   TEXT,
                confidence  REAL,
                pnl         REAL
            );

            -- positions: (symbol, side) is the natural unique key so a
            -- hedged long+short on the same symbol can coexist.
            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                side        TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                amount      REAL    NOT NULL,
                leverage    INTEGER NOT NULL DEFAULT 1,
                stop_loss   REAL,
                take_profit REAL,
                opened_at   TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(symbol, side)
            );

            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                total_value REAL    NOT NULL,
                cash_balance REAL   NOT NULL,
                holdings    TEXT    NOT NULL,   -- JSON
                pnl_total   REAL    NOT NULL,
                pnl_pct     REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange    TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                timeframe   TEXT    NOT NULL,
                timestamp   INTEGER NOT NULL,
                open        REAL    NOT NULL,
                high        REAL    NOT NULL,
                low         REAL    NOT NULL,
                close       REAL    NOT NULL,
                volume      REAL    NOT NULL,
                UNIQUE(exchange, symbol, timeframe, timestamp)
            );

            CREATE INDEX IF NOT EXISTS idx_trades_symbol     ON trades(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_timestamp  ON trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup      ON ohlcv_cache(exchange, symbol, timeframe, timestamp);
        """)
    _migrate_positions_schema()


def _migrate_positions_schema() -> None:
    """If the legacy positions table (UNIQUE(symbol), no leverage column)
    exists, rebuild it with the new (symbol, side) uniqueness and leverage
    column. Idempotent: a no-op once the new schema is in place.
    """
    with db() as conn:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(positions)")}
        # The new schema is detected by the presence of the `leverage` column.
        if "leverage" in cols:
            return

        old_rows = conn.execute("SELECT * FROM positions").fetchall()
        conn.execute("ALTER TABLE positions RENAME TO positions_legacy")
        conn.execute("""
            CREATE TABLE positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT    NOT NULL,
                side        TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                amount      REAL    NOT NULL,
                leverage    INTEGER NOT NULL DEFAULT 1,
                stop_loss   REAL,
                take_profit REAL,
                opened_at   TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(symbol, side)
            )
        """)
        for r in old_rows:
            conn.execute(
                """INSERT INTO positions
                   (symbol, side, entry_price, amount, leverage, stop_loss,
                    take_profit, opened_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    r["symbol"], r["side"], r["entry_price"], r["amount"], 1,
                    r["stop_loss"], r["take_profit"], r["opened_at"], r["updated_at"],
                ),
            )
        conn.execute("DROP TABLE positions_legacy")


# ── Trade helpers ──────────────────────────────────────────────────────────────

def log_trade(
    symbol: str,
    side: str,
    price: float,
    amount: float,
    strategy: str,
    mode: str,
    reasoning: str = "",
    confidence: float = 0.0,
    pnl: Optional[float] = None,
    fee: float = 0.0,
) -> int:
    cost = price * amount
    with db() as conn:
        cur = conn.execute(
            """INSERT INTO trades
               (timestamp, symbol, side, price, amount, cost, fee, strategy, mode, reasoning, confidence, pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), symbol, side, price, amount, cost, fee,
             strategy, mode, reasoning, confidence, pnl),
        )
        return cur.lastrowid


def get_recent_trades(symbol: Optional[str] = None, limit: int = 20) -> List[Dict]:
    with db() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT * FROM trades WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_trade_stats(symbol: Optional[str] = None) -> Dict:
    """Return win rate, average P&L, and total trades."""
    with db() as conn:
        clause = "WHERE symbol=?" if symbol else ""
        params = (symbol,) if symbol else ()
        row = conn.execute(
            f"""SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                AVG(pnl) AS avg_pnl,
                SUM(pnl) AS total_pnl
               FROM trades WHERE pnl IS NOT NULL {('AND symbol=?' if symbol else '')}""",
            params,
        ).fetchone()
        if not row or row["total"] == 0:
            return {"total": 0, "wins": 0, "win_rate": 0.0, "avg_pnl": 0.0, "total_pnl": 0.0}
        total = row["total"] or 0
        wins = row["wins"] or 0
        return {
            "total": total,
            "wins": wins,
            "win_rate": wins / total if total > 0 else 0.0,
            "avg_pnl": row["avg_pnl"] or 0.0,
            "total_pnl": row["total_pnl"] or 0.0,
        }


# ── Position helpers ───────────────────────────────────────────────────────────

def upsert_position(
    symbol: str,
    side: str,
    entry_price: float,
    amount: float,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    leverage: int = 1,
) -> None:
    now = datetime.utcnow().isoformat()
    with db() as conn:
        conn.execute(
            """INSERT INTO positions
                   (symbol, side, entry_price, amount, leverage,
                    stop_loss, take_profit, opened_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol, side) DO UPDATE SET
                   entry_price=excluded.entry_price,
                   amount=excluded.amount,
                   leverage=excluded.leverage,
                   stop_loss=excluded.stop_loss,
                   take_profit=excluded.take_profit,
                   updated_at=excluded.updated_at""",
            (symbol, side, entry_price, amount, leverage,
             stop_loss, take_profit, now, now),
        )


def delete_position(symbol: str, side: Optional[str] = None) -> None:
    """Delete position(s) for `symbol`. If `side` is given, only that side."""
    with db() as conn:
        if side is None:
            conn.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
        else:
            conn.execute(
                "DELETE FROM positions WHERE symbol=? AND side=?", (symbol, side)
            )


def get_positions() -> List[Dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM positions").fetchall()]


# ── Portfolio snapshot ─────────────────────────────────────────────────────────

def save_portfolio_snapshot(
    total_value: float,
    cash_balance: float,
    holdings: Dict,
    pnl_total: float,
    pnl_pct: float,
) -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO portfolio_snapshots (timestamp, total_value, cash_balance, holdings, pnl_total, pnl_pct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (datetime.utcnow().isoformat(), total_value, cash_balance,
             json.dumps(holdings), pnl_total, pnl_pct),
        )


def get_portfolio_history(limit: int = 100) -> List[Dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["holdings"] = json.loads(d["holdings"])
            result.append(d)
        return result


# ── OHLCV cache ────────────────────────────────────────────────────────────────

def cache_ohlcv(exchange: str, symbol: str, timeframe: str, candles: List[List]) -> None:
    with db() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO ohlcv_cache (exchange, symbol, timeframe, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [(exchange, symbol, timeframe, c[0], c[1], c[2], c[3], c[4], c[5]) for c in candles],
        )


def get_cached_ohlcv(
    exchange: str, symbol: str, timeframe: str,
    since_ms: Optional[int] = None, limit: int = 500,
) -> List[List]:
    with db() as conn:
        if since_ms:
            rows = conn.execute(
                """SELECT timestamp, open, high, low, close, volume FROM ohlcv_cache
                   WHERE exchange=? AND symbol=? AND timeframe=? AND timestamp >= ?
                   ORDER BY timestamp ASC LIMIT ?""",
                (exchange, symbol, timeframe, since_ms, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT timestamp, open, high, low, close, volume FROM ohlcv_cache
                   WHERE exchange=? AND symbol=? AND timeframe=?
                   ORDER BY timestamp DESC LIMIT ?""",
                (exchange, symbol, timeframe, limit),
            ).fetchall()
        return [list(r) for r in rows]
