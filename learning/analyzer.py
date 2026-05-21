"""
learning/analyzer.py -- Per-signal accuracy tracking with Wilson score intervals.

After each closed trade, determines whether each indicator signal at entry
was "correct" (its direction matched the outcome). Updates the signal_accuracy
table with cumulative stats and a verdict.

Verdicts:
  - 'valid':        accuracy >= 75%, enough samples → boost weight
  - 'monitor':      accuracy 55–75%, enough samples → keep weight, watch
  - 'suppress':     accuracy 45–55%, enough samples → coin flip, zero weight
  - 'contrarian':   accuracy <= 30%, enough samples → reliable anti-signal, negative weight
  - 'review':       accuracy 30–45%, enough samples → borderline, reduce weight
  - 'insufficient_data': below min_trades_per_signal → do not adjust

Contrarian logic:
  A signal with ≤30% accuracy is not useless — it's reliably wrong.
  If rsi fires "bullish" but the trade consistently loses, that's actionable:
  invert rsi's contribution in ScoreConfluence (negative weight).

Connection safety:
  Uses db_conn() context manager for all DB writes. Connection is always
  closed in the finally block. PRAGMA busy_timeout allows concurrent writers
  to wait briefly rather than failing with SQLITE_BUSY.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Dict, Optional, Tuple

_log = logging.getLogger(__name__)

# ── Default thresholds (can be overridden via config) ──────────────────────

DEFAULT_MIN_TRADES_PER_SIGNAL = 15
DEFAULT_HIGHLIGHT_THRESHOLD = 75.0   # % accuracy → "valid"
DEFAULT_MONITOR_LOW = 55.0           # % accuracy → lower bound of "monitor"
DEFAULT_SUPPRESS_RANGE = (45.0, 55.0)  # coin-flip zone → "suppress"
DEFAULT_CONTRARIAN_THRESHOLD = 30.0  # % accuracy → "contrarian" (≤ this)


# ── Wilson score interval ──────────────────────────────────────────────────

def wilson_score_interval(correct: int, total: int, z: float = 1.96) -> Tuple[float, float]:
    """
    Compute the Wilson score interval for a binomial proportion.

    Returns (lower_bound, upper_bound) as fractions (0.0–1.0).
    Used instead of simple percentage for statistical confidence on small samples.

    A signal with "80% accuracy on 5 trades" has a Wilson interval of
    [0.34, 0.98] — too wide to be actionable. This prevents the system
    from drawing conclusions from small samples.

    Args:
        correct:  Number of correct predictions.
        total:    Total number of observations.
        z:        Z-score for confidence level (1.96 = 95% confidence).

    Returns:
        (lower, upper) bounds. Returns (0.0, 0.0) if total == 0.
    """
    if total == 0:
        return (0.0, 0.0)

    n = total
    p_hat = correct / n
    z2 = z * z

    denominator = 1 + z2 / n
    centre = p_hat + z2 / (2 * n)
    spread = z * math.sqrt(p_hat * (1 - p_hat) / n + z2 / (4 * n * n))

    lower = max(0.0, (centre - spread) / denominator)
    upper = min(1.0, (centre + spread) / denominator)

    return (lower, upper)


# ── Verdict classification ─────────────────────────────────────────────────

def classify_verdict(
    accuracy_pct: float,
    sample_size: int,
    min_trades: int = DEFAULT_MIN_TRADES_PER_SIGNAL,
    highlight: float = DEFAULT_HIGHLIGHT_THRESHOLD,
    monitor_low: float = DEFAULT_MONITOR_LOW,
    suppress_range: Tuple[float, float] = DEFAULT_SUPPRESS_RANGE,
    contrarian: float = DEFAULT_CONTRARIAN_THRESHOLD,
) -> str:
    """
    Classify a signal's verdict based on accuracy and sample size.

    The contrarian verdict is key: a signal with ≤30% accuracy is a reliable
    anti-signal. It's not useless — it's consistently wrong, which means
    inverting its contribution is statistically sound.

    Args:
        accuracy_pct:  Observed accuracy as a percentage (0–100).
        sample_size:   Number of observations for this signal.
        min_trades:    Minimum observations before any verdict other than
                       'insufficient_data' is assigned.

    Returns:
        One of: 'valid', 'monitor', 'suppress', 'contrarian', 'review',
        'insufficient_data'.
    """
    if sample_size < min_trades:
        return "insufficient_data"

    if accuracy_pct >= highlight:
        return "valid"

    if accuracy_pct >= monitor_low:
        return "monitor"

    if suppress_range[0] <= accuracy_pct <= suppress_range[1]:
        return "suppress"

    if accuracy_pct <= contrarian:
        return "contrarian"

    # Between contrarian threshold and suppress low → "review" (borderline)
    return "review"


# ── Update signal accuracy from closed trades ──────────────────────────────

def update_signal_accuracy(
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades_per_signal: int = DEFAULT_MIN_TRADES_PER_SIGNAL,
) -> None:
    """
    Recompute signal_accuracy from all closed trades for the given strategy.

    For each indicator signal present at trade entry, determines whether
    the signal direction matched the trade outcome. A signal is "correct" if:
      - Trade won AND signal direction matches trade direction (bullish on long win)
      - Trade lost AND signal direction opposes trade direction (bearish on long loss)

    Writes results to the signal_accuracy table using INSERT OR REPLACE
    (idempotent — safe to call multiple times without double-counting).

    Rows with empty or invalid signals_at_entry_json are skipped gracefully.

    Connection safety: uses db_conn() which auto-commits on success and
    auto-rolls-back + closes on any exception.
    """
    from core.database import db_conn

    try:
        with db_conn() as conn:
            # Fetch all closed trades for this strategy
            rows = conn.execute(
                """SELECT id, direction, outcome, signals_at_entry_json
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL
                     AND signals_at_entry_json IS NOT NULL
                     AND signals_at_entry_json != ''""",
                (strategy_name,),
            ).fetchall()

            if not rows:
                _log.debug("No closed trades with signals for strategy '%s'", strategy_name)
                return

            # Aggregate per-indicator stats
            stats: Dict[str, Dict] = {}  # indicator_name → {total, correct}

            for row in rows:
                direction = row["direction"].lower() if row["direction"] else ""
                outcome = row["outcome"].lower() if row["outcome"] else ""

                if not direction or not outcome:
                    continue

                try:
                    signals = json.loads(row["signals_at_entry_json"])
                except (json.JSONDecodeError, TypeError):
                    _log.warning("Skipping trade id=%d: invalid signals JSON", row["id"])
                    continue

                if not isinstance(signals, dict):
                    continue

                trade_won = outcome in ("win", "won")

                for indicator_name, signal_data in signals.items():
                    if not isinstance(signal_data, dict):
                        continue

                    signal_direction = signal_data.get("signal", "").lower()
                    if not signal_direction or signal_direction == "neutral":
                        # Neutral signals don't count — they didn't take a stance
                        continue

                    if indicator_name not in stats:
                        stats[indicator_name] = {"total": 0, "correct": 0}

                    stats[indicator_name]["total"] += 1

                    # Determine correctness
                    signal_aligned_with_long = signal_direction in ("bullish", "long")
                    trade_is_long = direction in ("long",)

                    if trade_won:
                        # Won trade: signal is correct if it aligned with the winning direction
                        if trade_is_long and signal_aligned_with_long:
                            stats[indicator_name]["correct"] += 1
                        elif not trade_is_long and not signal_aligned_with_long:
                            stats[indicator_name]["correct"] += 1
                    else:
                        # Lost trade: signal is correct if it OPPOSED the losing direction
                        # (bearish signal on a losing long = correctly predicted failure)
                        if trade_is_long and not signal_aligned_with_long:
                            stats[indicator_name]["correct"] += 1
                        elif not trade_is_long and signal_aligned_with_long:
                            stats[indicator_name]["correct"] += 1

            # Write to signal_accuracy table
            for indicator_name, data in stats.items():
                total = data["total"]
                correct = data["correct"]
                accuracy_pct = (correct / total * 100) if total > 0 else 0.0
                low, high = wilson_score_interval(correct, total)
                verdict = classify_verdict(accuracy_pct, total, min_trades=min_trades_per_signal)

                conn.execute(
                    """INSERT OR REPLACE INTO signal_accuracy
                       (strategy_uid, indicator_name, total_fired, correct, accuracy_pct,
                        confidence_95_low, confidence_95_high, verdict, sample_size)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_uid, indicator_name, total, correct, accuracy_pct,
                     low * 100, high * 100, verdict, total),
                )

            _log.info(
                "Updated signal accuracy for '%s': %d indicators processed",
                strategy_name, len(stats),
            )

    except Exception as e:
        _log.error("Failed to update signal accuracy for '%s': %s", strategy_name, e, exc_info=True)


# ── Read signal verdicts ───────────────────────────────────────────────────

def get_signal_verdicts(strategy_name: str, strategy_uid: str = "legacy") -> Dict[str, str]:
    """
    Return a dict of {indicator_name: verdict} for the given strategy.

    Returns an empty dict if no signal_accuracy rows exist.
    """
    from core.database import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT indicator_name, verdict FROM signal_accuracy WHERE strategy_uid = ?",
                (strategy_uid,),
            ).fetchall()

        return {row["indicator_name"]: row["verdict"] for row in rows}

    except Exception as e:
        _log.error("Failed to read signal verdicts: %s", e, exc_info=True)
        return {}