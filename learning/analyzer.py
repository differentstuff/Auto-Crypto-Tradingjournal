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


# -- Parameter validation --------------------------------------------------

def _validate_required_params(**kwargs) -> None:
    """
    Raise TypeError if any kwarg is None — caller forgot to pass a config value.

    This catches the case where UpdateLearning fails to thread a config
    value through to a learning function. The error message names every
    missing parameter so the fix is immediate.
    """
    missing = [k for k, v in kwargs.items() if v is None]
    if missing:
        raise TypeError(
            f"Required parameter(s) not provided to update_signal_accuracy: "
            + ", ".join(missing)
            + ". All learning thresholds must come from config (learning.*)."
        )


# -- Wilson score interval --------------------------------------------------

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


# -- Verdict classification -------------------------------------------------

def classify_verdict(
    accuracy_pct: float,
    sample_size: int,
    min_trades: int,
    highlight: float,
    monitor_low: float,
    suppress_range: Tuple[float, float],
    contrarian: float,
) -> str:
    """
    Classify a signal's verdict based on accuracy and sample size.

    All thresholds are required — they come from the strategy config
    (learning.highlight_threshold, learning.monitor_low_threshold,
    learning.suppress_range, learning.contrarian_threshold). No
    hardcoded defaults.

    The contrarian verdict is key: a signal with ≤ contrarian % accuracy
    is a reliable anti-signal. It's not useless — it's consistently wrong,
    which means inverting its contribution is statistically sound.

    Args:
        accuracy_pct:   Observed accuracy as a percentage (0–100).
        sample_size:    Number of observations for this signal.
        min_trades:     Minimum observations before any verdict other than
                        'insufficient_data' is assigned.
        highlight:      Accuracy threshold (≥) for 'valid' verdict.
        monitor_low:    Accuracy threshold (≥) for 'monitor' verdict.
        suppress_range: (low, high) tuple → 'suppress' (coin flip).
        contrarian:     Accuracy threshold (≤) for 'contrarian' anti-signal.

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


# -- Update signal accuracy from closed trades ------------------------------

def update_signal_accuracy(
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades_per_signal: int = None,
    highlight_threshold: float = None,
    monitor_low_threshold: float = None,
    suppress_range: Tuple[float, float] = None,
    contrarian_threshold: float = None,
    bucket: Optional[str] = None,
) -> None:
    """
    Recompute signal_accuracy from all closed trades for the given strategy.

    All thresholds are required — they come from the strategy config.
    None of them have hardcoded defaults; the caller must supply values
    read from substrate.cfg().

    Bucket parameter (Decision D3 — unified function):
      - bucket=None (default): existing behavior — write to signal_accuracy table,
        but filter to production trades only. This ensures weight_adjuster
        gets clean production data.
      - bucket="production": write production-bucket accuracy to
        signal_accuracy_by_threshold table.
      - bucket="exploration": write exploration-bucket accuracy to
        signal_accuracy_by_threshold table.
      - bucket="all": write all buckets to signal_accuracy_by_threshold table.

    For each indicator signal present at trade entry, determines whether
    the signal direction matched the trade outcome. A signal is "correct" if:
      - Trade won AND signal direction matches trade direction (bullish on long win)
      - Trade lost AND signal direction opposes trade direction (bearish on long loss)

    Writes results to signal_accuracy (bucket=None) or
    signal_accuracy_by_threshold (bucket specified) using INSERT OR REPLACE
    (idempotent — safe to call multiple times without double-counting).

    Rows with empty or invalid signals_at_entry_json are skipped gracefully.

    Connection safety: uses db_conn() which auto-commits on success and
    auto-rolls-back + closes on any exception.
    """
    _validate_required_params(
        min_trades_per_signal=min_trades_per_signal,
        highlight_threshold=highlight_threshold,
        monitor_low_threshold=monitor_low_threshold,
        suppress_range=suppress_range,
        contrarian_threshold=contrarian_threshold,
    )

    from core.database import db_conn

    # Determine target table and bucket filter
    write_to_threshold_table = bucket is not None
    target_bucket = bucket if bucket != "all" else None  # None = no filter for "all"

    try:
        with db_conn() as conn:
            # Build query with optional bucket filter
            base_query = """SELECT id, direction, outcome, signals_at_entry_json, pnl_pct
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL
                     AND signals_at_entry_json IS NOT NULL
                     AND signals_at_entry_json != ''"""

            query_params: list = [strategy_name]

            if write_to_threshold_table:
                if target_bucket:
                    # Filter by specific bucket
                    base_query += """ AND signals_at_entry_json LIKE ?"""
                    query_params.append(f'%"_threshold_bucket": "{target_bucket}"%')
                # bucket="all" → no additional filter
            else:
                # Default behavior: production-only filter (Decision D3)
                # Include trades with production bucket OR old trades without bucket tag
                base_query += """ AND (signals_at_entry_json LIKE '%"_threshold_bucket": "production"%'
                                      OR signals_at_entry_json NOT LIKE '%_threshold_bucket%')"""

            rows = conn.execute(base_query, query_params).fetchall()

            if not rows:
                _log.debug("No closed trades with signals for strategy '%s' (bucket=%s)", strategy_name, bucket)
                return

            # Aggregate per-indicator stats
            # For threshold table, also track pnl for profit_factor and win_rate
            stats: Dict[str, Dict] = {}  # indicator_name → {total, correct, pnl_wins, pnl_losses, win_pnl_sum, loss_pnl_sum}

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
                pnl_pct = row["pnl_pct"] if row["pnl_pct"] is not None else 0.0

                # Extract threshold metadata for the threshold table
                threshold_used = signals.get("_threshold_used", 0.0)
                threshold_bucket = signals.get("_threshold_bucket", "production")

                for indicator_name, signal_data in signals.items():
                    if not isinstance(signal_data, dict):
                        continue
                    # Skip metadata keys (prefixed with _)
                    if indicator_name.startswith("_"):
                        continue

                    signal_direction = signal_data.get("signal", "").lower()
                    if not signal_direction or signal_direction == "neutral":
                        # Neutral signals don't count — they didn't take a stance
                        continue

                    if indicator_name not in stats:
                        stats[indicator_name] = {
                            "total": 0, "correct": 0,
                            "pnl_wins": 0, "pnl_losses": 0,
                            "win_pnl_sum": 0.0, "loss_pnl_sum": 0.0,
                            "threshold_value": threshold_used,
                            "threshold_bucket": threshold_bucket,
                        }

                    stats[indicator_name]["total"] += 1

                    # Determine correctness
                    signal_aligned_with_long = signal_direction in ("bullish", "long")
                    trade_is_long = direction in ("long",)

                    is_correct = False
                    if trade_won:
                        if trade_is_long and signal_aligned_with_long:
                            is_correct = True
                        elif not trade_is_long and not signal_aligned_with_long:
                            is_correct = True
                    else:
                        if trade_is_long and not signal_aligned_with_long:
                            is_correct = True
                        elif not trade_is_long and signal_aligned_with_long:
                            is_correct = True

                    if is_correct:
                        stats[indicator_name]["correct"] += 1

                    # Track PnL for profit_factor / win_rate (threshold table only)
                    if write_to_threshold_table:
                        if pnl_pct > 0:
                            stats[indicator_name]["pnl_wins"] += 1
                            stats[indicator_name]["win_pnl_sum"] += abs(pnl_pct)
                        elif pnl_pct < 0:
                            stats[indicator_name]["pnl_losses"] += 1
                            stats[indicator_name]["loss_pnl_sum"] += abs(pnl_pct)

            # Write results
            for indicator_name, data in stats.items():
                total = data["total"]
                correct = data["correct"]
                accuracy_pct = (correct / total * 100) if total > 0 else 0.0
                low, high = wilson_score_interval(correct, total)
                verdict = classify_verdict(
                    accuracy_pct, total,
                    min_trades=min_trades_per_signal,
                    highlight=highlight_threshold,
                    monitor_low=monitor_low_threshold,
                    suppress_range=suppress_range,
                    contrarian=contrarian_threshold,
                )

                if write_to_threshold_table:
                    # Compute profit_factor and win_rate for threshold table
                    win_rate = (data["pnl_wins"] / total * 100) if total > 0 else 0.0
                    profit_factor = (
                        data["win_pnl_sum"] / data["loss_pnl_sum"]
                        if data["loss_pnl_sum"] > 0
                        else (float("inf") if data["win_pnl_sum"] > 0 else 0.0)
                    )
                    if profit_factor == float("inf"):
                        profit_factor = 999.9  # cap for DB storage

                    bucket_label = target_bucket or data["threshold_bucket"]

                    conn.execute(
                        """INSERT OR REPLACE INTO signal_accuracy_by_threshold
                           (strategy_uid, indicator_name, threshold_bucket, threshold_value,
                            total_fired, correct, accuracy_pct,
                            confidence_95_low, confidence_95_high, verdict, sample_size,
                            profit_factor, win_rate, trade_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (strategy_uid, indicator_name, bucket_label, data["threshold_value"],
                         total, correct, accuracy_pct,
                         low * 100, high * 100, verdict, total,
                         profit_factor, win_rate, total),
                    )
                else:
                    # Default: write to signal_accuracy (production only)
                    conn.execute(
                        """INSERT OR REPLACE INTO signal_accuracy
                           (strategy_uid, indicator_name, total_fired, correct, accuracy_pct,
                            confidence_95_low, confidence_95_high, verdict, sample_size)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (strategy_uid, indicator_name, total, correct, accuracy_pct,
                         low * 100, high * 100, verdict, total),
                    )

            table_name = "signal_accuracy_by_threshold" if write_to_threshold_table else "signal_accuracy"
            _log.info(
                "Updated %s for '%s' (bucket=%s): %d indicators processed",
                table_name, strategy_name, bucket, len(stats),
            )

    except Exception as e:
        _log.error("Failed to update signal accuracy for '%s': %s", strategy_name, e, exc_info=True)


# -- Read signal verdicts ---------------------------------------------------

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