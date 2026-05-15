"""Tests for retroactive_close_calls (moved to sync_base)."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import datetime, timezone, timedelta


def _insert_saved_call(db, symbol, direction, entry, sl, tp1, tp2=None, hours_ago=3):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, tp2_price,
           status, created_at)
        VALUES (?,?,?,?,?,?,'saved',?)
    """, (symbol, direction, entry, sl, tp1, tp2, created))
    db.commit()
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def _make_candles(rows):
    """rows: list of (timestamp_ms, high, low)"""
    return pd.DataFrame(
        [(ts, 0.0, h, l, 0.0, 0.0, 0.0) for ts, h, l in rows],
        columns=["timestamp", "open", "high", "low", "close", "volume", "quote_volume"]
    )


def test_long_tp1_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, tp2=120)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 95)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT status, outcome, hit_tp1, hit_tp2, hit_sl FROM analyzed_calls WHERE id=?",
        (call_id,)
    ).fetchone()
    assert row == ("closed", "won", 1, 0, 0)


def test_long_tp2_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, tp2=120)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 125, 95)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_tp1, hit_tp2 FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("won", 1, 1)


def test_long_sl_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 105, 88)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_sl FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("lost", 1)


def test_short_tp1_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "ETHUSDT", "Short", entry=100, sl=110, tp1=90)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 105, 88)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_tp1 FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("won", 1)


def test_short_sl_hit(db, monkeypatch):
    call_id = _insert_saved_call(db, "ETHUSDT", "Short", entry=100, sl=110, tp1=90)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 112, 95)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute(
        "SELECT outcome, hit_sl FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("lost", 1)


def test_no_resolution_when_price_between_sl_and_tp(db, monkeypatch):
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 108, 95)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0
    row = db.execute(
        "SELECT status, outcome FROM analyzed_calls WHERE id=?", (call_id,)
    ).fetchone()
    assert row == ("saved", None)


def test_sl_wins_when_same_candle_touches_both(db, monkeypatch):
    """SL takes priority when a single candle touches both SL and TP1."""
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 1
    row = db.execute("SELECT outcome FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    assert row[0] == "lost"


def test_skips_call_too_recent(db, monkeypatch):
    """Calls newer than 2 hours must not be processed."""
    _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110, hours_ago=1)
    candles = _make_candles([
        (int(datetime.now(timezone.utc).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0


def test_skips_matched_calls(db, monkeypatch):
    """Only touches 'saved' calls — matched calls handled by _auto_close_calls."""
    db.execute("""
        INSERT INTO analyzed_calls
          (symbol, direction, entry_price, sl_price, tp1_price, status, created_at)
        VALUES ('BTCUSDT','Long',100,90,110,'matched', datetime('now', '-3 hours'))
    """)
    db.commit()
    candles = _make_candles([
        (int(datetime.now(timezone.utc).timestamp() * 1000), 115, 88)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0


def test_outcome_pnl_is_null(db, monkeypatch):
    """Retroactive records have NULL outcome_pnl — no actual trade."""
    call_id = _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    candles = _make_candles([
        (int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp() * 1000), 115, 95)
    ])
    monkeypatch.setattr("chart_context.get_candles_at_time", lambda *a, **kw: candles)
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    _retroactive_close_calls(db)
    row = db.execute("SELECT outcome_pnl FROM analyzed_calls WHERE id=?", (call_id,)).fetchone()
    assert row[0] is None


def test_empty_candles_skipped(db, monkeypatch):
    _insert_saved_call(db, "BTCUSDT", "Long", entry=100, sl=90, tp1=110)
    monkeypatch.setattr(
        "bitget_sync.chart_context.get_candles_at_time", lambda *a, **kw: pd.DataFrame()
    )
    from sync_base import retroactive_close_calls as _retroactive_close_calls
    assert _retroactive_close_calls(db) == 0
