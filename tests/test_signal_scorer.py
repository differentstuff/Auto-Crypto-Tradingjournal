# tests/test_signal_scorer.py
import pytest
import sqlite3
import random
import numpy as np


def _make_db(n=25):
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE analyzed_calls (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            setup_score INTEGER, outcome TEXT,
            rr_ratio TEXT, consensus_score REAL
        )
    """)
    random.seed(0)
    for i in range(n):
        score   = random.randint(4, 9)
        outcome = "won" if random.random() > 0.45 else "lost"
        rr      = f"{random.uniform(1.0, 4.0):.1f}:1"
        cs      = random.uniform(4.0, 9.0)
        conn.execute("INSERT INTO analyzed_calls VALUES (?,?,?,?,?,?,?)",
                     (i, "BTCUSDT", "long", score, outcome, rr, cs))
    conn.commit()
    return conn


def test_train_ok():
    conn = _make_db(25)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    assert s.train(conn) is True
    assert s.is_trained


def test_predict_probability_range():
    conn = _make_db(25)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    s.train(conn)
    features = {"setup_score": 7, "direction": "long", "rr_ratio": "2.5:1", "consensus_score": 7.0}
    prob = s.predict(features)
    assert prob is not None
    assert 0.0 <= prob <= 1.0


def test_train_fails_below_minimum():
    conn = _make_db(10)
    from signal_scorer import SignalScorer
    s = SignalScorer()
    assert s.train(conn) is False
    assert not s.is_trained


def test_predict_without_training_is_none():
    from signal_scorer import SignalScorer
    assert SignalScorer().predict({}) is None
