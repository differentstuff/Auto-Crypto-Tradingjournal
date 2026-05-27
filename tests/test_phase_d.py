"""
tests_new/test_phase_d.py -- Validation tests for Phase D: Learning Engine.

Tests that:
1. DB connections are always closed (no stale connections, no blocking)
2. analyzer.py: Wilson score math, verdict classification (incl. contrarian),
   update with real DB rows, insufficient data guard
3. combination.py: chi-squared math, pairwise extraction, significance threshold,
   contrarian combination detection
4. trajectory.py: classify_trajectory() pure function (all 4 patterns),
   accuracy update, edge cases
5. rulebook.py: generation from accuracy data, max 10 rule cap, contrarian rules,
   should_regenerate logic, DB writes
6. weight_adjuster.py: suppress→0, contrarian→negative, valid→boost,
   re-normalization, weight_history writes, safety guard (cannot zero everything)
7. UpdateRulebook enzyme: activation conditions, transform writes to substrate + DB

All tests are pure unit tests:
  - No real network calls
  - All use temp_db fixture for DB isolation
  - Pure math functions tested without DB
  - Connection safety tested with threading

Contrarian logic:
  A signal with ≤30% accuracy is a reliable ANTI-signal, not just a bad one.
  It fires "bullish" but the market moves bearish. This is actionable information.
  Verdict: 'contrarian'. Weight becomes negative so ScoreConfluence inverts
  the signal's contribution (bullish signal → subtract from long score).

Connection safety:
  Every learning function opens a short-lived connection via db_conn() and
  closes it in the finally block. PRAGMA busy_timeout is set so concurrent
  writers wait briefly (500ms) rather than failing with SQLITE_BUSY.
  Tests verify this explicitly.

Requires: pytest>=9.0.0
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Shared test thresholds — all tests use these to call learning functions.
# No hardcoded defaults in production code; tests are explicit about values.
# ---------------------------------------------------------------------------

_T = {
    "min_trades_per_signal": 15,
    "min_trades_before_adjusting": 30,
    "significance_level": 0.05,
    "contrarian_win_rate": 30.0,
    "highlight_threshold": 75.0,
    "monitor_low_threshold": 55.0,
    "suppress_range": (45.0, 55.0),
    "contrarian_threshold": 30.0,
    "rulebook_max_rules": 10,
    "retrain_every_n_trades": 10,
}


# ---------------------------------------------------------------------------
# Helpers: seed trade_learning rows
# ---------------------------------------------------------------------------

def _insert_trade(conn, *, symbol="BTCUSDT", direction="Long",
                  strategy_name="test_strategy", outcome="win",
                  signals_json: str = "", trajectory_pattern: str = "gradual_alignment",
                  pnl_pct: float = 1.5, entry_time: str | None = None) -> int:
    """Insert a single closed trade into trade_learning. Returns row id."""
    if entry_time is None:
        entry_time = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO trade_learning
           (symbol, direction, strategy_name, entry_time, exit_time,
            outcome, pnl_pct, confluence_score_at_entry,
            signals_at_entry_json, pre_trade_trajectory_pattern)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            symbol, direction, strategy_name,
            entry_time,
            datetime.now(timezone.utc).isoformat(),  # exit_time (closed)
            outcome, pnl_pct, 7.0,
            signals_json,
            trajectory_pattern,
        ),
    )
    return cur.lastrowid


def _signals_json(rsi: str = "bullish", macd: str = "bullish",
                  ema_stack: str = "bullish") -> str:
    """Build a minimal signals_at_entry_json string."""
    return json.dumps({
        "rsi":       {"signal": rsi,       "value": 35.0, "strength": 0.7},
        "macd":      {"signal": macd,      "value": 0.1,  "strength": 0.6},
        "ema_stack": {"signal": ema_stack, "value": None, "strength": 0.8},
    })


# ---------------------------------------------------------------------------
# 1. DB connection safety
# ---------------------------------------------------------------------------

class TestDBConnectionSafety:
    """Verify that db_conn() always closes the connection."""

    def test_db_conn_closes_on_success(self, temp_db):
        """Connection is closed after a clean exit from db_conn()."""
        from core.database import db_conn
        conn_ref: list[sqlite3.Connection] = []

        with db_conn() as conn:
            conn_ref.append(conn)

        # After the context manager exits, the connection should be closed.
        # Attempting to use it should raise ProgrammingError.
        with pytest.raises(Exception):
            conn_ref[0].execute("SELECT 1")

    def test_db_conn_closes_on_exception(self, temp_db):
        """Connection is closed even when an exception is raised inside the block."""
        from core.database import db_conn
        conn_ref: list[sqlite3.Connection] = []

        with pytest.raises(ValueError):
            with db_conn() as conn:
                conn_ref.append(conn)
                raise ValueError("deliberate error")

        # Connection must be closed despite the exception.
        with pytest.raises(Exception):
            conn_ref[0].execute("SELECT 1")

    def test_db_conn_rolls_back_on_exception(self, temp_db):
        """A write inside a failed db_conn() block is rolled back."""
        from core.database import db_conn, get_conn

        # Write a row, then raise — the row must NOT appear in the DB.
        with pytest.raises(RuntimeError):
            with db_conn() as conn:
                conn.execute(
                    "INSERT INTO cycle_log (strategy_name, cycle_count, action, duration_ms) "
                    "VALUES ('rollback_test', 999, 'test', 1)"
                )
                raise RuntimeError("force rollback")

        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM cycle_log WHERE strategy_name='rollback_test' AND cycle_count=999"
            ).fetchone()
        assert row is None

    def test_concurrent_writes_do_not_deadlock(self, temp_db):
        """
        Two threads writing to signal_accuracy simultaneously must both succeed.

        SQLite WAL mode allows one writer at a time; the second writer must wait
        (busy_timeout) and retry rather than immediately failing with SQLITE_BUSY.
        Both writes must complete within 3 seconds total.
        """
        from core.database import db_conn

        errors: list[Exception] = []
        results: list[str] = []

        def _write(label: str):
            try:
                with db_conn() as conn:
                    # Simulate a slightly slow write
                    time.sleep(0.05)
                    conn.execute(
                        """INSERT OR REPLACE INTO signal_accuracy
                           (strategy_uid, indicator_name, total_fired, correct, accuracy_pct, verdict)
                           VALUES ('legacy', ?, 10, 8, 80.0, 'valid')""",
                        (f"rsi_{label}",),
                    )
                results.append(label)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_write, args=("thread1",))
        t2 = threading.Thread(target=_write, args=("thread2",))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert not errors, f"Concurrent write errors: {errors}"
        assert len(results) == 2, f"Not all writes completed: {results}"


# ---------------------------------------------------------------------------
# 2. analyzer.py
# ---------------------------------------------------------------------------

class TestWilsonScore:
    """Pure math tests for Wilson score interval — no DB required."""

    def test_known_values_perfect_accuracy(self):
        """10/10 correct → lower bound well above 0.69."""
        from learning.analyzer import wilson_score_interval
        low, high = wilson_score_interval(10, 10)
        assert low > 0.69
        assert high == pytest.approx(1.0, abs=0.01)

    def test_known_values_50_percent(self):
        """10/20 correct → interval centered near 0.5."""
        from learning.analyzer import wilson_score_interval
        low, high = wilson_score_interval(10, 20)
        assert 0.28 < low < 0.5
        assert 0.5 < high < 0.72

    def test_zero_total_returns_zero_interval(self):
        """0 total observations → (0.0, 0.0) — no ZeroDivisionError."""
        from learning.analyzer import wilson_score_interval
        low, high = wilson_score_interval(0, 0)
        assert low == 0.0
        assert high == 0.0

    def test_zero_correct_of_many(self):
        """0/20 correct → upper bound is low (not zero — Wilson is never exactly 0)."""
        from learning.analyzer import wilson_score_interval
        low, high = wilson_score_interval(0, 20)
        assert low == pytest.approx(0.0, abs=0.01)
        assert high < 0.20

    def test_small_sample_wide_interval(self):
        """3/5 correct → wide interval, lower bound < 0.3 (not actionable)."""
        from learning.analyzer import wilson_score_interval
        low, high = wilson_score_interval(3, 5)
        assert high - low > 0.40  # interval width > 40 percentage points


class TestVerdictClassification:
    """Pure logic tests for classify_verdict — no DB required."""

    def test_verdict_valid_high(self):
        """80% accuracy, n=20 → 'valid'."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(80.0, 20, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "valid"

    def test_verdict_monitor(self):
        """65% accuracy, n=20 → 'monitor'."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(65.0, 20, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "monitor"

    def test_verdict_suppress(self):
        """50% accuracy, n=20 → 'suppress' (coin flip)."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(50.0, 20, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "suppress"

    def test_verdict_contrarian_low(self):
        """25% accuracy, n=20 → 'contrarian' (reliable anti-signal)."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(25.0, 20, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "contrarian"

    def test_verdict_contrarian_boundary(self):
        """30% accuracy exactly → 'contrarian' (boundary is inclusive)."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(30.0, 20, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "contrarian"

    def test_verdict_insufficient_data_below_min(self):
        """80% accuracy but only 5 observations → 'insufficient_data'."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(80.0, 5, min_trades=15,
                                highlight=_T["highlight_threshold"],
                                monitor_low=_T["monitor_low_threshold"],
                                suppress_range=_T["suppress_range"],
                                contrarian=_T["contrarian_threshold"]) == "insufficient_data"

    def test_verdict_insufficient_data_zero(self):
        """0 observations → 'insufficient_data'."""
        from learning.analyzer import classify_verdict
        assert classify_verdict(0.0, 0, _T["min_trades_per_signal"],
                                _T["highlight_threshold"], _T["monitor_low_threshold"],
                                _T["suppress_range"], _T["contrarian_threshold"]) == "insufficient_data"

    def test_verdict_boundaries_are_consistent(self):
        """Verify the verdict boundaries don't overlap or leave gaps."""
        from learning.analyzer import classify_verdict
        # 31% is above contrarian threshold (≤30%) but below suppress (45–55%)
        v = classify_verdict(31.0, 20, _T["min_trades_per_signal"],
                             _T["highlight_threshold"], _T["monitor_low_threshold"],
                             _T["suppress_range"], _T["contrarian_threshold"])
        assert v in ("suppress", "review"), f"Unexpected verdict at 31%: {v}"


class TestUpdateSignalAccuracy:
    """Tests for update_signal_accuracy() with real DB rows."""

    def test_empty_trade_learning_no_crash(self, temp_db):
        """No trades → function returns without error, no rows written."""
        from learning.analyzer import update_signal_accuracy
        update_signal_accuracy("no_trades_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])
        # No exception = pass. Verify no rows written.
        from core.database import db_conn
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM signal_accuracy"
            ).fetchone()[0]
        assert rows == 0

    def test_update_writes_signal_accuracy_rows(self, temp_db):
        """After 20 trades with consistent signals, signal_accuracy is populated."""
        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        # Insert 20 winning long trades where rsi, macd, ema_stack are all bullish
        with db_conn() as conn:
            for _ in range(20):
                _insert_trade(conn, outcome="win", direction="Long",
                               signals_json=_signals_json("bullish", "bullish", "bullish"))

        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT * FROM signal_accuracy WHERE indicator_name='rsi'"
            ).fetchone()

        assert row is not None
        assert row["total_fired"] == 20
        # All bullish on winning long trades → all correct
        assert row["correct"] == 20
        assert row["accuracy_pct"] == pytest.approx(100.0, abs=1.0)

    def test_update_handles_empty_signals_json(self, temp_db):
        """Rows with empty signals_at_entry_json are skipped gracefully."""
        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            # Mix: some with signals, some without
            for _ in range(5):
                _insert_trade(conn, outcome="win", signals_json="")
            for _ in range(5):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "bullish"))

        # Must not raise
        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT total_fired FROM signal_accuracy WHERE indicator_name='rsi'"
            ).fetchone()
        # Only 5 rows had valid signals
        assert row is not None
        assert row["total_fired"] == 5

    def test_contrarian_verdict_stored_in_db(self, temp_db):
        """
        A signal that fires 'bullish' on consistently losing long trades
        should receive verdict='contrarian' after enough observations.

        This is the anti-signal case: rsi says bullish, but the trade always loses.
        The signal is not useless — it's reliably wrong, which means it's
        a contrarian indicator (invert its contribution).
        """
        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        # 20 losing long trades where rsi fires bullish (= wrong direction)
        with db_conn() as conn:
            for _ in range(20):
                _insert_trade(
                    conn,
                    outcome="loss",
                    direction="Long",
                    pnl_pct=-1.5,
                    signals_json=_signals_json("bullish", "neutral", "neutral"),
                )

        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT verdict, accuracy_pct FROM signal_accuracy WHERE indicator_name='rsi'"
            ).fetchone()

        assert row is not None
        # rsi fired bullish on 20 losing long trades → 0% correct → contrarian
        assert row["verdict"] == "contrarian"
        assert row["accuracy_pct"] < 10.0

    def test_get_signal_verdicts_returns_dict(self, temp_db):
        """get_signal_verdicts() returns a dict keyed by indicator name."""
        from learning.analyzer import update_signal_accuracy, get_signal_verdicts
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(20):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "bullish"))

        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])
        verdicts = get_signal_verdicts("test_strategy")

        assert isinstance(verdicts, dict)
        assert "rsi" in verdicts
        assert "macd" in verdicts
        assert verdicts["rsi"] in ("valid", "monitor", "suppress", "contrarian",
                                   "review", "insufficient_data")

    def test_insufficient_data_below_min_trades(self, temp_db):
        """Only 5 trades → all verdicts are 'insufficient_data'."""
        from learning.analyzer import update_signal_accuracy, get_signal_verdicts
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(5):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "bullish"))

        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=15,
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])
        verdicts = get_signal_verdicts("test_strategy")

        for indicator, verdict in verdicts.items():
            assert verdict == "insufficient_data", (
                f"{indicator} has verdict '{verdict}' with only 5 trades"
            )

    def test_update_is_idempotent(self, temp_db):
        """Calling update_signal_accuracy twice does not double-count trades."""
        from learning.analyzer import update_signal_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(20):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "bullish"))

        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])
        update_signal_accuracy("test_strategy",
                               min_trades_per_signal=_T["min_trades_per_signal"],
                               highlight_threshold=_T["highlight_threshold"],
                               monitor_low_threshold=_T["monitor_low_threshold"],
                               suppress_range=_T["suppress_range"],
                               contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT total_fired FROM signal_accuracy WHERE indicator_name='rsi'"
            ).fetchone()

        # Must still be 20, not 40
        assert row["total_fired"] == 20


# ---------------------------------------------------------------------------
# 3. combination.py
# ---------------------------------------------------------------------------

class TestChiSquared:
    """Pure math tests for chi-squared significance — no DB required."""

    def test_significant_combination(self):
        """10/12 wins → p < 0.05 (statistically significant)."""
        from learning.combination import chi_squared_p_value
        p = chi_squared_p_value(won=10, trades=12)
        assert p < 0.05

    def test_not_significant_small_sample(self):
        """3/5 wins → p > 0.05 (insufficient data)."""
        from learning.combination import chi_squared_p_value
        p = chi_squared_p_value(won=3, trades=5)
        assert p > 0.05

    def test_zero_trades_returns_one(self):
        """0 trades → p=1.0 (no evidence), no ZeroDivisionError."""
        from learning.combination import chi_squared_p_value
        p = chi_squared_p_value(won=0, trades=0)
        assert p == pytest.approx(1.0)

    def test_all_wins_significant(self):
        """10/10 wins → p < 0.05."""
        from learning.combination import chi_squared_p_value
        p = chi_squared_p_value(won=10, trades=10)
        assert p < 0.05

    def test_fifty_fifty_not_significant(self):
        """10/20 wins (50%) → p is high (consistent with null hypothesis)."""
        from learning.combination import chi_squared_p_value
        p = chi_squared_p_value(won=10, trades=20)
        assert p > 0.5  # 50% is exactly the null hypothesis


class TestPairwiseExtraction:
    """Pure logic tests for extracting aligned signal pairs."""

    def test_three_aligned_signals_gives_three_pairs(self):
        """3 aligned signals → 3 unique pairs (C(3,2)=3)."""
        from learning.combination import extract_aligned_pairs
        signals = {
            "rsi":       {"signal": "bullish"},
            "macd":      {"signal": "bullish"},
            "ema_stack": {"signal": "bullish"},
        }
        pairs = extract_aligned_pairs(signals, direction="Long")
        assert len(pairs) == 3

    def test_single_aligned_signal_gives_no_pairs(self):
        """Only 1 aligned signal → 0 pairs (need at least 2 for a combination)."""
        from learning.combination import extract_aligned_pairs
        signals = {
            "rsi":  {"signal": "bullish"},
            "macd": {"signal": "bearish"},  # not aligned with Long
        }
        pairs = extract_aligned_pairs(signals, direction="Long")
        assert len(pairs) == 0

    def test_no_aligned_signals_gives_no_pairs(self):
        """No signals aligned → 0 pairs."""
        from learning.combination import extract_aligned_pairs
        signals = {
            "rsi":  {"signal": "bearish"},
            "macd": {"signal": "bearish"},
        }
        pairs = extract_aligned_pairs(signals, direction="Long")
        assert len(pairs) == 0

    def test_empty_signals_gives_no_pairs(self):
        """Empty signals dict → 0 pairs, no crash."""
        from learning.combination import extract_aligned_pairs
        pairs = extract_aligned_pairs({}, direction="Long")
        assert len(pairs) == 0

    def test_pair_names_are_sorted(self):
        """Pair names are sorted alphabetically so 'macd+rsi' == 'rsi+macd'."""
        from learning.combination import extract_aligned_pairs
        signals = {
            "rsi":  {"signal": "bullish"},
            "macd": {"signal": "bullish"},
        }
        pairs = extract_aligned_pairs(signals, direction="Long")
        assert len(pairs) == 1
        # Sorted: macd comes before rsi alphabetically
        assert pairs[0] == "macd+rsi"


class TestUpdateCombinationAccuracy:
    """Tests for update_combination_accuracy() with real DB rows."""

    def test_significant_combination_stored(self, temp_db):
        """15 trades with 12 wins for rsi+macd → significance='significant'."""
        from learning.combination import update_combination_accuracy
        from core.database import db_conn

        # 12 wins, 3 losses — all with rsi+macd both bullish
        with db_conn() as conn:
            for i in range(15):
                outcome = "win" if i < 12 else "loss"
                _insert_trade(conn, outcome=outcome, direction="Long",
                               signals_json=_signals_json("bullish", "bullish", "neutral"))

        update_combination_accuracy("test_strategy",
                                   min_trades=_T["min_trades_per_signal"],
                                   significance_level=_T["significance_level"],
                                   contrarian_win_rate=_T["contrarian_win_rate"])

        with db_conn() as conn:
            row = conn.execute(
                """SELECT * FROM combination_accuracy
                   WHERE combination_name='macd+rsi' AND direction_state='both_bullish'"""
            ).fetchone()

        assert row is not None
        assert row["trades"] == 15
        assert row["won"] == 12
        assert row["significance"] == "significant"

    def test_insufficient_sample_not_significant(self, temp_db):
        """5 trades → significance='insufficient_data'."""
        from learning.combination import update_combination_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(5):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "neutral"))

        update_combination_accuracy("test_strategy", min_trades=10,
                                   significance_level=_T["significance_level"],
                                   contrarian_win_rate=_T["contrarian_win_rate"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT significance FROM combination_accuracy WHERE combination_name='macd+rsi'"
            ).fetchone()

        assert row is not None
        assert row["significance"] == "insufficient_data"

    def test_contrarian_combination_detected(self, temp_db):
        """
        A combination that loses consistently is a contrarian anti-signal.

        rsi+macd both bullish on 15 losing long trades → win_rate < 30%
        → significance='contrarian' (not just 'not_significant').
        """
        from learning.combination import update_combination_accuracy
        from core.database import db_conn

        # 13 losses, 2 wins — reliable anti-signal
        with db_conn() as conn:
            for i in range(15):
                outcome = "loss" if i < 13 else "win"
                _insert_trade(conn, outcome=outcome, direction="Long",
                               pnl_pct=-1.5 if i < 13 else 1.5,
                               signals_json=_signals_json("bullish", "bullish", "neutral"))

        update_combination_accuracy("test_strategy",
                                   min_trades=_T["min_trades_per_signal"],
                                   significance_level=_T["significance_level"],
                                   contrarian_win_rate=_T["contrarian_win_rate"])

        with db_conn() as conn:
            row = conn.execute(
                """SELECT win_rate_pct, significance FROM combination_accuracy
                   WHERE combination_name='macd+rsi' AND direction_state='both_bullish'"""
            ).fetchone()

        assert row is not None
        assert row["win_rate_pct"] < 30.0
        # Contrarian combinations should be flagged, not silently dropped
        assert row["significance"] in ("contrarian", "significant"), (
            f"Expected contrarian/significant, got: {row['significance']}"
        )

    def test_update_combination_is_idempotent(self, temp_db):
        """Calling update twice does not double-count trades."""
        from learning.combination import update_combination_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(15):
                _insert_trade(conn, outcome="win",
                               signals_json=_signals_json("bullish", "bullish", "neutral"))

        update_combination_accuracy("test_strategy",
                                   min_trades=_T["min_trades_per_signal"],
                                   significance_level=_T["significance_level"],
                                   contrarian_win_rate=_T["contrarian_win_rate"])
        update_combination_accuracy("test_strategy",
                                   min_trades=_T["min_trades_per_signal"],
                                   significance_level=_T["significance_level"],
                                   contrarian_win_rate=_T["contrarian_win_rate"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT trades FROM combination_accuracy WHERE combination_name='macd+rsi'"
            ).fetchone()

        assert row["trades"] == 15  # not 30


# ---------------------------------------------------------------------------
# 4. trajectory.py
# ---------------------------------------------------------------------------

class TestClassifyTrajectory:
    """Pure function tests for classify_trajectory() — no DB required."""

    def _make_history(self, consistent_bars: int, total: int = 12,
                      direction: str = "bullish") -> list[dict]:
        """
        Build a synthetic indicator history list.
        consistent_bars bars align with `direction`, the rest are opposite.
        """
        history = []
        for i in range(total):
            signal = direction if i < consistent_bars else (
                "bearish" if direction == "bullish" else "bullish"
            )
            history.append({"signal": signal, "value": 50.0 + i})
        return history

    def test_gradual_alignment(self):
        """9/12 bars consistently bullish → gradual_alignment, risk=low."""
        from learning.trajectory import classify_trajectory
        history = self._make_history(consistent_bars=9, total=12)
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        assert pattern == "gradual_alignment"
        assert risk == "low"

    def test_sudden_snap(self):
        """2/12 bars consistent → sudden_snap, risk=high."""
        from learning.trajectory import classify_trajectory
        history = self._make_history(consistent_bars=2, total=12)
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        assert pattern == "sudden_snap"
        assert risk == "high"

    def test_oscillating(self):
        """Alternating bars → oscillating, risk=medium."""
        from learning.trajectory import classify_trajectory
        history = []
        for i in range(12):
            signal = "bullish" if i % 2 == 0 else "bearish"
            history.append({"signal": signal, "value": 50.0})
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        assert pattern == "oscillating"
        assert risk == "medium"

    def test_flat_no_movement(self):
        """All bars neutral → flat, risk=low."""
        from learning.trajectory import classify_trajectory
        history = [{"signal": "neutral", "value": 50.0} for _ in range(12)]
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        assert pattern == "flat"
        assert risk == "low"

    def test_empty_history_returns_flat(self):
        """Empty list → flat, no crash."""
        from learning.trajectory import classify_trajectory
        pattern, risk = classify_trajectory([], final_direction="bullish")
        assert pattern == "flat"
        assert risk == "low"

    def test_single_bar_is_sudden_snap(self):
        """1 bar → sudden_snap (insufficient history to call it gradual)."""
        from learning.trajectory import classify_trajectory
        history = [{"signal": "bullish", "value": 50.0}]
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        assert pattern == "sudden_snap"
        assert risk == "high"

    def test_bearish_gradual_alignment(self):
        """Gradual alignment works for bearish direction too."""
        from learning.trajectory import classify_trajectory
        history = self._make_history(consistent_bars=9, total=12, direction="bearish")
        pattern, risk = classify_trajectory(history, final_direction="bearish")
        assert pattern == "gradual_alignment"
        assert risk == "low"

    def test_mixed_consistency_is_high_risk(self):
        """
        A mixed consistency ratio (not clearly gradual or sudden) must be
        treated as HIGH risk. The system has a wait bias, not an action bias.
        ISC-007 checks coincidence_risk != "high" — ambiguity must not slip
        through as "medium". Verify > assume.
        """
        from learning.trajectory import classify_trajectory
        # 5/12 consistent = 0.42 ratio → falls in the oscillating or mixed range
        # Use 4/12 = 0.33 ratio → below oscillating (0.4-0.6), above sudden (≤0.25)
        # This is the "mixed" catch-all case
        history = self._make_history(consistent_bars=4, total=12)
        pattern, risk = classify_trajectory(history, final_direction="bullish")
        # Mixed patterns must be HIGH risk, never medium
        assert risk == "high", (
            f"Mixed trajectory must be high risk (wait bias), got risk='{risk}' for pattern='{pattern}'"
        )


class TestUpdateTrajectoryAccuracy:
    """Tests for update_trajectory_accuracy() with real DB rows."""

    def test_update_trajectory_writes_to_db(self, temp_db):
        """Closed trades with trajectory patterns → trajectory_accuracy populated."""
        from learning.trajectory import update_trajectory_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(10):
                _insert_trade(conn, outcome="win", trajectory_pattern="gradual_alignment")
            for _ in range(5):
                _insert_trade(conn, outcome="loss", trajectory_pattern="sudden_snap")

        update_trajectory_accuracy("test_strategy",
                                  min_trades=_T["min_trades_per_signal"],
                                  highlight_threshold=_T["highlight_threshold"],
                                  monitor_low_threshold=_T["monitor_low_threshold"],
                                  suppress_range=_T["suppress_range"],
                                  contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            gradual = conn.execute(
                "SELECT * FROM trajectory_accuracy WHERE trajectory_pattern='gradual_alignment'"
            ).fetchone()
            sudden = conn.execute(
                "SELECT * FROM trajectory_accuracy WHERE trajectory_pattern='sudden_snap'"
            ).fetchone()

        assert gradual is not None
        assert gradual["trades"] == 10
        assert gradual["won"] == 10

        assert sudden is not None
        assert sudden["trades"] == 5
        assert sudden["won"] == 0

    def test_trajectory_verdict_gradual_valid(self, temp_db):
        """18/23 gradual wins → verdict='valid'."""
        from learning.trajectory import update_trajectory_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for i in range(23):
                outcome = "win" if i < 18 else "loss"
                _insert_trade(conn, outcome=outcome, trajectory_pattern="gradual_alignment")

        update_trajectory_accuracy("test_strategy", min_trades=15,
                                  highlight_threshold=_T["highlight_threshold"],
                                  monitor_low_threshold=_T["monitor_low_threshold"],
                                  suppress_range=_T["suppress_range"],
                                  contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT verdict FROM trajectory_accuracy WHERE trajectory_pattern='gradual_alignment'"
            ).fetchone()

        assert row is not None
        assert row["verdict"] == "valid"

    def test_trajectory_verdict_sudden_suppress(self, temp_db):
        """4/12 sudden wins → verdict='suppress'."""
        from learning.trajectory import update_trajectory_accuracy
        from core.database import db_conn

        with db_conn() as conn:
            for i in range(12):
                outcome = "win" if i < 4 else "loss"
                _insert_trade(conn, outcome=outcome, trajectory_pattern="sudden_snap")

        update_trajectory_accuracy("test_strategy", min_trades=10,
                                  highlight_threshold=_T["highlight_threshold"],
                                  monitor_low_threshold=_T["monitor_low_threshold"],
                                  suppress_range=_T["suppress_range"],
                                  contrarian_threshold=_T["contrarian_threshold"])

        with db_conn() as conn:
            row = conn.execute(
                "SELECT verdict FROM trajectory_accuracy WHERE trajectory_pattern='sudden_snap'"
            ).fetchone()

        assert row is not None
        assert row["verdict"] in ("suppress", "contrarian", "review")

    def test_empty_table_no_crash(self, temp_db):
        """No closed trades → function returns without error."""
        from learning.trajectory import update_trajectory_accuracy
        update_trajectory_accuracy("empty_strategy",
                                  min_trades=_T["min_trades_per_signal"],
                                  highlight_threshold=_T["highlight_threshold"],
                                  monitor_low_threshold=_T["monitor_low_threshold"],
                                  suppress_range=_T["suppress_range"],
                                  contrarian_threshold=_T["contrarian_threshold"])


# ---------------------------------------------------------------------------
# 5. rulebook.py
# ---------------------------------------------------------------------------

class TestShouldRegenerate:
    """Tests for _should_regenerate() logic — no DB writes needed."""

    def test_false_below_threshold(self, temp_db):
        """15 total trades, threshold=30 → False."""
        from learning.rulebook import should_regenerate
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(15):
                _insert_trade(conn, outcome="win")

        assert should_regenerate("test_strategy", min_trades=30, retrain_every_n_trades=10) is False

    def test_true_above_threshold_no_previous(self, temp_db):
        """35 trades, no previous rulebook → True."""
        from learning.rulebook import should_regenerate
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        assert should_regenerate("test_strategy", min_trades=30, retrain_every_n_trades=10) is True

    def test_false_not_enough_new_trades(self, temp_db):
        """
        35 total trades, last rulebook at 32 trades, retrain_every=10 → False.
        Only 3 new trades since last generation, need 10.
        """
        from learning.rulebook import should_regenerate
        from core.database import db_conn

        # Insert a previous rulebook version that was generated at 32 trades
        with db_conn() as conn:
            conn.execute(
                """INSERT INTO rulebook_versions
                   (strategy_uid, version, rulebook_text, trades_recorded_at_generation)
                   VALUES (?, ?, ?, ?)""",
                ("legacy", "2026-05-01-v1", "Rule 1: test rule", 32),
            )
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        assert should_regenerate(
            "test_strategy", min_trades=30, retrain_every_n_trades=10
        ) is False

    def test_true_enough_new_trades_since_last(self, temp_db):
        """35 total trades, last rulebook at 20 trades, retrain_every=10 → True."""
        from learning.rulebook import should_regenerate
        from core.database import db_conn

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO rulebook_versions
                   (strategy_uid, version, rulebook_text, trades_recorded_at_generation)
                   VALUES (?, ?, ?, ?)""",
                ("legacy", "2026-04-01-v1", "Rule 1: old rule", 20),
            )
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        assert should_regenerate(
            "test_strategy", min_trades=30, retrain_every_n_trades=10
        ) is True


class TestGenerateRulebook:
    """Tests for generate_rulebook() — requires populated accuracy tables."""

    def _populate_signal_accuracy(self, conn, indicator: str, total: int,
                                   correct: int, accuracy: float, verdict: str,
                                   strategy_uid: str = "legacy"):
        """Seed a signal_accuracy row directly."""
        conn.execute(
            """INSERT OR REPLACE INTO signal_accuracy
               (strategy_uid, indicator_name, total_fired, correct, accuracy_pct,
                confidence_95_low, confidence_95_high, verdict)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_uid, indicator, total, correct, accuracy, accuracy - 10, accuracy + 5, verdict),
        )

    def _populate_combination_accuracy(self, conn, combo: str, direction_state: str,
                                        trades: int, won: int, win_rate: float,
                                        p_value: float, significance: str,
                                        strategy_uid: str = "legacy"):
        """Seed a combination_accuracy row directly."""
        conn.execute(
            """INSERT OR REPLACE INTO combination_accuracy
               (strategy_uid, combination_name, direction_state, trades, won,
                win_rate_pct, p_value, significance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (strategy_uid, combo, direction_state, trades, won, win_rate, p_value, significance),
        )

    def test_empty_accuracy_data_returns_empty_or_minimal(self, temp_db):
        """No accuracy data → generate_rulebook returns empty string, no crash."""
        from learning.rulebook import generate_rulebook
        result = generate_rulebook("empty_strategy", max_rules=_T["rulebook_max_rules"])
        assert isinstance(result, str)
        # Either empty or a message saying no data
        assert len(result) < 200

    def test_max_10_rules(self, temp_db):
        """20 candidates → output has at most 10 rules."""
        from learning.rulebook import generate_rulebook
        from core.database import db_conn

        with db_conn() as conn:
            # Seed 20 signal accuracy rows with 'valid' verdict
            for i in range(20):
                self._populate_signal_accuracy(
                    conn, f"indicator_{i}", 20, 16, 80.0, "valid"
                )

        result = generate_rulebook("test_strategy", max_rules=_T["rulebook_max_rules"])
        # Count lines that look like rules (start with "[" or "Rule")
        rule_lines = [ln for ln in result.split("\n")
                      if ln.strip().startswith(("[", "Rule", "-"))]
        assert len(rule_lines) <= 10

    def test_contrarian_signal_produces_anti_signal_rule(self, temp_db):
        """A 'contrarian' verdict produces a rule mentioning anti-signal or invert."""
        from learning.rulebook import generate_rulebook
        from core.database import db_conn

        with db_conn() as conn:
            self._populate_signal_accuracy(
                conn, "macd", 20, 4, 20.0, "contrarian"
            )

        result = generate_rulebook("test_strategy", max_rules=_T["rulebook_max_rules"])
        # The rulebook must mention the contrarian nature
        lower = result.lower()
        assert any(word in lower for word in ("anti", "contrarian", "invert", "reverse")), (
            f"Contrarian rule not found in rulebook:\n{result}"
        )

    def test_combination_rules_prioritized_over_single_signals(self, temp_db):
        """
        Combination rules appear before single-signal rules when the
        combination genuinely has higher statistical weight.

        Learning must be honest: the highest priority wins, regardless
        of source type. A combination CAN outrank a single signal if
        its statistical weight (trades * |win_rate - 50|) is higher.
        It CANNOT outrank if its weight is lower — that would falsify data.
        """
        from learning.rulebook import generate_rulebook
        from core.database import db_conn

        with db_conn() as conn:
            # Single signal: 20 trades, 80% accuracy → priority = 20 * 30 = 600
            self._populate_signal_accuracy(conn, "rsi", 20, 16, 80.0, "valid")
            # Combination: 25 trades, 90% win rate → priority = 25 * 40 = 1000
            # This genuinely outranks the single signal — honest data.
            self._populate_combination_accuracy(
                conn, "macd+rsi", "both_bullish", 25, 23, 90.0, 0.001, "significant"
            )

        result = generate_rulebook("test_strategy", max_rules=_T["rulebook_max_rules"])
        # Combination name should appear before single indicator name in the text
        macd_rsi_pos = result.find("macd+rsi")
        rsi_pos = result.find("rsi")
        if macd_rsi_pos != -1 and rsi_pos != -1:
            assert macd_rsi_pos <= rsi_pos, (
                "Combination rule should appear before single-signal rule when it genuinely outranks"
            )

    def test_generate_rulebook_writes_to_db(self, temp_db):
        """generate_rulebook() writes a new row to rulebook_versions."""
        from learning.rulebook import generate_rulebook
        from core.database import db_conn

        with db_conn() as conn:
            self._populate_signal_accuracy(conn, "rsi", 20, 16, 80.0, "valid")

        generate_rulebook("test_strategy", max_rules=_T["rulebook_max_rules"])

        with db_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM rulebook_versions"
            ).fetchone()[0]
        assert count == 1

    def test_get_latest_rulebook_none_when_empty(self, temp_db):
        """No rulebook versions → get_latest_rulebook returns None."""
        from learning.rulebook import get_latest_rulebook
        result = get_latest_rulebook("no_strategy")
        assert result is None

    def test_get_latest_rulebook_returns_most_recent(self, temp_db):
        """Two versions → get_latest_rulebook returns the most recent text."""
        from learning.rulebook import get_latest_rulebook
        from core.database import db_conn

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO rulebook_versions (strategy_uid, version, rulebook_text, trades_recorded_at_generation)
                   VALUES (?, ?, ?, ?)""",
                ("legacy", "2026-01-01-v1", "Old rulebook text", 30),
            )
            conn.execute(
                """INSERT INTO rulebook_versions (strategy_uid, version, rulebook_text, trades_recorded_at_generation)
                   VALUES (?, ?, ?, ?)""",
                ("legacy", "2026-05-01-v2", "New rulebook text", 50),
            )

        result = get_latest_rulebook("test_strategy")
        # Should return the most recent one (v2)
        assert result is not None
        assert "New rulebook" in result


# ---------------------------------------------------------------------------
# 6. weight_adjuster.py
# ---------------------------------------------------------------------------

class TestWeightAdjuster:
    """Tests for compute_adjusted_weights() — includes contrarian negative weights."""

    def _seed_signal_accuracy(self, conn, indicator: str, verdict: str,
                               accuracy: float = 75.0, total: int = 20,
                               strategy_uid: str = "legacy"):
        """Seed a signal_accuracy row."""
        conn.execute(
            """INSERT OR REPLACE INTO signal_accuracy
               (strategy_uid, indicator_name, total_fired, correct, accuracy_pct, verdict)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (strategy_uid, indicator, total, int(total * accuracy / 100), accuracy, verdict),
        )

    def test_below_threshold_returns_original_weights(self, temp_db):
        """Fewer than min_trades → original weights returned unchanged."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(10):
                _insert_trade(conn, outcome="win")

        original = {"rsi": 0.25, "macd": 0.25, "ema_stack": 0.25, "adx": 0.25}
        result = compute_adjusted_weights(
            original, "test_strategy", min_trades=30
        )
        assert result == original

    def test_suppress_verdict_sets_weight_to_zero(self, temp_db):
        """'suppress' verdict → weight becomes 0.0."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            self._seed_signal_accuracy(conn, "macd", "suppress", accuracy=50.0)

        original = {"rsi": 0.5, "macd": 0.5}
        result = compute_adjusted_weights(original, "test_strategy", min_trades=30)
        assert result["macd"] == 0.0

    def test_contrarian_verdict_sets_negative_weight(self, temp_db):
        """
        'contrarian' verdict → weight becomes negative.

        A negative weight tells ScoreConfluence to INVERT the signal's contribution:
        if the indicator fires 'bullish', it subtracts from the long score instead of adding.
        This is the correct handling of a reliable anti-signal.
        """
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            self._seed_signal_accuracy(conn, "macd", "contrarian", accuracy=20.0)

        original = {"rsi": 0.5, "macd": 0.5}
        result = compute_adjusted_weights(original, "test_strategy", min_trades=30)
        assert result["macd"] < 0.0, (
            f"Contrarian signal must have negative weight, got: {result['macd']}"
        )

    def test_valid_high_accuracy_boosts_weight(self, temp_db):
        """'valid' verdict ≥75% accuracy → weight boosted by ~20%."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            self._seed_signal_accuracy(conn, "rsi", "valid", accuracy=80.0)

        original = {"rsi": 0.5, "macd": 0.5}
        result = compute_adjusted_weights(original, "test_strategy", min_trades=30)
        # rsi should be boosted (> original 0.5 before normalization)
        # After normalization, rsi's share should be larger than macd's
        assert result["rsi"] > result["macd"]

    def test_renormalization_positive_weights_sum_correctly(self, temp_db):
        """After adjustment, positive weights re-normalize to original total."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            # rsi: valid (boost), macd: monitor (unchanged)
            self._seed_signal_accuracy(conn, "rsi", "valid", accuracy=80.0)
            self._seed_signal_accuracy(conn, "macd", "monitor", accuracy=65.0)

        original = {"rsi": 0.5, "macd": 0.5}
        result = compute_adjusted_weights(original, "test_strategy", min_trades=30)
        # Positive weights should sum to the original total (1.0)
        positive_sum = sum(v for v in result.values() if v > 0)
        assert positive_sum == pytest.approx(1.0, abs=0.01)

    def test_all_suppressed_returns_original_weights(self, temp_db):
        """
        Safety guard: if ALL signals are suppressed/contrarian, the system
        cannot trade at all. Return original weights to prevent a dead system.
        """
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            self._seed_signal_accuracy(conn, "rsi", "suppress", accuracy=50.0)
            self._seed_signal_accuracy(conn, "macd", "suppress", accuracy=48.0)

        original = {"rsi": 0.5, "macd": 0.5}
        result = compute_adjusted_weights(original, "test_strategy", min_trades=30)
        # Safety: cannot zero out everything
        positive_sum = sum(v for v in result.values() if v > 0)
        assert positive_sum > 0, "All weights zeroed — safety guard must prevent this"

    def test_weight_history_written_for_each_change(self, temp_db):
        """Each weight change writes a row to weight_history with justification."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            self._seed_signal_accuracy(conn, "rsi", "valid", accuracy=82.0)
            self._seed_signal_accuracy(conn, "macd", "suppress", accuracy=48.0)

        original = {"rsi": 0.5, "macd": 0.5}
        compute_adjusted_weights(original, "test_strategy", min_trades=30)

        with db_conn() as conn:
            rows = conn.execute(
                "SELECT indicator_name, justification FROM weight_history"
            ).fetchall()

        assert len(rows) >= 1  # At least one change recorded
        names = [r["indicator_name"] for r in rows]
        assert any(n in ("rsi", "macd") for n in names)
        # Justification must be a non-empty string
        for row in rows:
            assert row["justification"] and len(row["justification"]) > 0

    def test_no_history_written_when_weights_unchanged(self, temp_db):
        """Unchanged weights (monitor verdict) → no weight_history rows."""
        from learning.weight_adjuster import compute_adjusted_weights
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            # monitor = no change
            self._seed_signal_accuracy(conn, "rsi", "monitor", accuracy=65.0)
            self._seed_signal_accuracy(conn, "macd", "monitor", accuracy=62.0)

        original = {"rsi": 0.5, "macd": 0.5}
        compute_adjusted_weights(original, "test_strategy", min_trades=30)

        with db_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM weight_history"
            ).fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# 7. UpdateRulebook enzyme
# ---------------------------------------------------------------------------

class TestUpdateRulebookEnzyme:
    """Tests for the UpdateRulebook Synthase enzyme."""

    def _get_enzyme(self, config: dict | None = None):
        from enzymes.update_rulebook import UpdateRulebook
        cfg = config or {
            "strategy": {"name": "test_strategy"},
            "learning": {
                "min_trades_before_adjusting": 30,
                "retrain_every_n_trades": 10,
                "rulebook_max_rules": 10,
            },
        }
        return UpdateRulebook(config=cfg)

    def _make_substrate(self, total_trades: int = 0, config: dict | None = None):
        from core.substrate import Substrate
        cfg = config or {
            "strategy": {"name": "test_strategy", "uid": "legacy"},
            "learning": {
                "min_trades_before_adjusting": 30,
                "retrain_every_n_trades": 10,
                "rulebook_max_rules": 10,
            },
        }
        sub = Substrate(config=cfg)
        sub.learning["total_trades_recorded"] = total_trades
        return sub

    def test_is_synthase_class(self):
        """UpdateRulebook must be a Synthase enzyme."""
        from core.enzyme import EnzymeClass
        enzyme = self._get_enzyme()
        assert enzyme.enzyme_class == EnzymeClass.SYNTHASE

    def test_does_not_activate_below_threshold(self, temp_db):
        """Does not activate when total_trades_recorded < min_trades_before_adjusting."""
        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=15)
        assert enzyme.can_activate(sub) is False

    def test_activates_above_threshold_no_previous(self, temp_db):
        """Activates when total_trades_recorded >= min_trades and no previous rulebook."""
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        assert enzyme.can_activate(sub) is True

    def test_does_not_activate_when_recent_rulebook(self, temp_db):
        """Does not activate when a recent rulebook was generated (< retrain_every_n_trades new)."""
        from core.database import db_conn

        with db_conn() as conn:
            # Previous rulebook at 32 trades
            conn.execute(
                """INSERT INTO rulebook_versions
                   (strategy_uid, version, rulebook_text, trades_recorded_at_generation)
                   VALUES (?, ?, ?, ?)""",
                ("legacy", "2026-05-01-v1", "old rule", 32),
            )
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        # 35 - 32 = 3 new trades, retrain_every=10 → should NOT activate
        assert enzyme.can_activate(sub) is False

    def test_transform_writes_rulebook_to_substrate(self, temp_db):
        """After transform, substrate.learning['rulebook'] is set."""
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            # Seed some signal accuracy so rulebook has content
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy
                   (strategy_uid, indicator_name, total_fired, correct, accuracy_pct, verdict)
                   VALUES ('legacy', 'rsi', 35, 28, 80.0, 'valid')"""
            )

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        result = enzyme.transform(sub)

        assert "rulebook" in result.learning
        assert isinstance(result.learning["rulebook"], str)

    def test_transform_writes_rulebook_version_to_substrate(self, temp_db):
        """After transform, substrate.learning['rulebook_version'] is set."""
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        result = enzyme.transform(sub)

        assert "rulebook_version" in result.learning
        assert result.learning["rulebook_version"] is not None

    def test_transform_writes_to_rulebook_versions_db(self, temp_db):
        """After transform with accuracy data, rulebook_versions table has a new row."""
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            # Seed accuracy data so generate_rulebook() has content to work with
            conn.execute(
                """INSERT OR REPLACE INTO signal_accuracy
                   (strategy_uid, indicator_name, total_fired, correct, accuracy_pct, verdict)
                   VALUES ('legacy', 'rsi', 35, 28, 80.0, 'valid')"""
            )

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        enzyme.transform(sub)

        with db_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM rulebook_versions"
            ).fetchone()[0]
        assert count >= 1

    def test_transform_no_write_when_no_accuracy_data(self, temp_db):
        """
        When no accuracy data exists, generate_rulebook() returns ''
        and no row is written to rulebook_versions.

        This verifies the system passes clean even when no write is needed.
        An empty rulebook should NOT be persisted — it would be meaningless.
        """
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")
            # Deliberately NO signal_accuracy rows

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        result = enzyme.transform(sub)

        # Enzyme completes without error
        assert result is not None
        # No rulebook written to substrate (empty string = no data)
        assert result.learning["rulebook"] == ""
        # No row written to rulebook_versions DB
        with db_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM rulebook_versions"
            ).fetchone()[0]
        assert count == 0

    def test_transform_does_not_crash_with_empty_accuracy_data(self, temp_db):
        """Transform completes without error even when accuracy tables are empty."""
        from core.database import db_conn

        with db_conn() as conn:
            for _ in range(35):
                _insert_trade(conn, outcome="win")

        enzyme = self._get_enzyme()
        sub = self._make_substrate(total_trades=35)
        # Must not raise
        result = enzyme.transform(sub)
        assert result is not None
