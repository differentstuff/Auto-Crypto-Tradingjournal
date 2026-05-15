"""Tests for GET /api/calls/accuracy-progress."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _insert_outcome(db, outcome):
    db.execute("""
        INSERT INTO analyzed_calls (symbol, direction, status, outcome, created_at)
        VALUES ('BTCUSDT', 'Long', 'closed', ?, datetime('now'))
    """, (outcome,))
    db.commit()


def test_empty(client):
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"] == 0
    assert data["data"]["enough_data"] is False


def test_partial(db, client):
    for outcome in ["won", "won", "lost"]:
        _insert_outcome(db, outcome)
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["recorded"] == 3
    assert data["data"]["enough_data"] is False


def test_target_reached(db, client):
    from constants import ACCURACY_TARGET
    for i in range(ACCURACY_TARGET):
        _insert_outcome(db, "won" if i % 2 == 0 else "lost")
    data = client.get("/api/calls/accuracy-progress").get_json()
    assert data["ok"] is True
    assert data["data"]["enough_data"] is True
    assert data["data"]["recorded"] >= ACCURACY_TARGET
