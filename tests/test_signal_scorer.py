# tests/test_signal_scorer.py
import pytest
import sqlite3
import json
import random
import numpy as np


def _make_db(n=25):
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE analyzed_calls (
            id INTEGER PRIMARY KEY, symbol TEXT, direction TEXT,
            setup_score INTEGER, outcome TEXT, analysis_json TEXT
        )
    """)
    random.seed(0)
    for i in range(n):
        score   = random.randint(4, 9)
        outcome = "won" if random.random() > 0.45 else "lost"
        analysis = json.dumps({
            "rsi": random.uniform(30, 70),
            "macd_histogram": random.uniform(-1, 1),
            "ema_alignment":  random.choice([1, 0, -1]),
            "adx":            random.uniform(10, 40),
            "wt_signal":      random.choice([1, 0, -1]),
            "mfi":            random.uniform(20, 80),
            "cvd_trend":      random.choice([1, 0, -1]),
            "volume_ratio":   random.uniform(0.5, 2.0),
        })
        conn.execute("INSERT INTO analyzed_calls VALUES (?,?,?,?,?,?)",
                     (i, "BTCUSDT", "long", score, outcome, analysis))
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
    features = {"setup_score": 7, "rsi": 45.0, "macd_histogram": 0.5,
                "ema_alignment": 1, "adx": 25.0, "wt_signal": 1,
                "mfi": 55.0, "cvd_trend": 1, "volume_ratio": 1.2, "direction": "long"}
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
