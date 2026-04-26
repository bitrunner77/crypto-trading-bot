"""
tests/test_database.py — covers SQLite persistence and the schema migration.
"""
import sqlite3

import pytest


def test_init_db_idempotent(tmp_db):
    import data.database as db
    db.init_db()  # second call is a no-op
    db.init_db()


def test_log_trade_roundtrip(tmp_db):
    import data.database as db
    tid = db.log_trade(
        symbol="BTC/USDT:USDT", side="long", price=50_000, amount=100,
        strategy="test", mode="paper", reasoning="why", confidence=0.7,
    )
    assert tid is not None
    rows = db.get_recent_trades(symbol="BTC/USDT:USDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC/USDT:USDT"
    assert rows[0]["confidence"] == pytest.approx(0.7)


def test_upsert_position_with_leverage(tmp_db):
    import data.database as db
    db.upsert_position(
        symbol="BTC/USDT:USDT", side="long", entry_price=50_000,
        amount=100, stop_loss=49_000, take_profit=52_000, leverage=10,
    )
    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["leverage"] == 10
    assert positions[0]["side"] == "long"


def test_hedged_long_and_short_coexist(tmp_db):
    """The whole point of dropping UNIQUE(symbol): allow long+short on same asset."""
    import data.database as db
    db.upsert_position(symbol="BTC/USDT:USDT", side="long",
                       entry_price=50_000, amount=100, leverage=5)
    db.upsert_position(symbol="BTC/USDT:USDT", side="short",
                       entry_price=50_500, amount=200, leverage=3)
    positions = db.get_positions()
    sides = sorted(p["side"] for p in positions)
    assert sides == ["long", "short"]


def test_upsert_replaces_same_symbol_side(tmp_db):
    import data.database as db
    db.upsert_position(symbol="BTC/USDT:USDT", side="long",
                       entry_price=50_000, amount=100)
    db.upsert_position(symbol="BTC/USDT:USDT", side="long",
                       entry_price=51_000, amount=200)
    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["entry_price"] == pytest.approx(51_000)


def test_delete_position_by_symbol_and_side(tmp_db):
    import data.database as db
    db.upsert_position("BTC/USDT:USDT", "long", 50_000, 100)
    db.upsert_position("BTC/USDT:USDT", "short", 50_500, 200)
    db.delete_position("BTC/USDT:USDT", "long")
    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["side"] == "short"


def test_trade_stats_handles_empty_history(tmp_db):
    import data.database as db
    stats = db.get_trade_stats()
    assert stats["total"] == 0
    assert stats["win_rate"] == 0.0


def test_legacy_schema_migrates(tmp_path, monkeypatch):
    """A pre-migration DB with UNIQUE(symbol) and no `leverage` column should
    be rebuilt without losing rows."""
    import data.database as db
    legacy_path = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "DB_PATH", legacy_path)

    # Build legacy schema by hand
    conn = sqlite3.connect(str(legacy_path))
    conn.executescript("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            amount REAL NOT NULL,
            stop_loss REAL, take_profit REAL,
            opened_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO positions (symbol, side, entry_price, amount, opened_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("BTC/USDT:USDT", "long", 50_000, 100, "2024-01-01", "2024-01-01"),
    )
    conn.commit()
    conn.close()

    # Running init_db (or the migration directly) should rebuild the table
    db.init_db()

    positions = db.get_positions()
    assert len(positions) == 1
    assert positions[0]["leverage"] == 1  # backfilled default

    # Hedged write now works
    db.upsert_position("BTC/USDT:USDT", "short", 51_000, 50)
    assert len(db.get_positions()) == 2
