"""
learning/trajectory.py -- Pre-trade trajectory classification and accuracy tracking.

Classifies how indicators aligned before a trade entry:
  - gradual_alignment: signals rose/fell together over 6+ bars → low coincidence risk
  - sudden_snap:      signals aligned in 1-2 bars only → high coincidence risk
  - oscillating:      signals flip back and forth → medium risk, market undecided
  - flat:             no meaningful change → low risk but no entry signal

The classify_trajectory() function is pure (no DB, no side effects).
It is called by CollectPreTradeContext enzyme during the sensing phase.

update_trajectory_accuracy() reads closed trades from trade_learning and
writes aggregated pattern accuracy to the trajectory_accuracy table.

Connection safety: db_conn() context manager, always closed.
"""

from __future__ import annotations

import json
import logging
from typing import Dict, List, Tuple

_log = logging.getLogger(__name__)



# ── Trajectory classification (pure function) ──────────────────────────────

def classify_trajectory(
    indicator_history: List[Dict],
    final_direction: str = "bullish",
) -> Tuple[str, str]:
    """
    Classify the pre-trade indicator trajectory pattern.

    Examines how consistently indicators aligned with the final signal
    direction over the lookback period. Returns (pattern, coincidence_risk).

    Args:
        indicator_history: List of dicts with at least "signal" key.
                           Each entry represents one bar's indicator state.
                           Example: [{"signal": "bullish", "value": 50.0}, ...]
        final_direction:   The direction of the final signal ("bullish" or "bearish").

    Returns:
        (pattern, risk) where pattern is one of:
          "gradual_alignment", "sudden_snap", "oscillating", "flat"
        and risk is one of: "low", "high", "medium"

    Edge cases:
        - Empty list → ("flat", "low") — no data means no risk, but no signal either
        - Single bar → ("sudden_snap", "high") — insufficient history
    """
    if not indicator_history:
        return ("flat", "low")

    total = len(indicator_history)

    if total <= 2:
        # 1-2 bars: insufficient history → sudden snap by definition
        return ("sudden_snap", "high")

    final_dir = final_direction.lower()
    consistent_bars = 0
    neutral_bars = 0

    for entry in indicator_history:
        if not isinstance(entry, dict):
            continue
        signal = entry.get("signal", "").lower()

        if not signal or signal == "neutral":
            neutral_bars += 1
            continue

        if signal in ("bullish", "long") and final_dir in ("bullish", "long"):
            consistent_bars += 1
        elif signal in ("bearish", "short") and final_dir in ("bearish", "short"):
            consistent_bars += 1

    # Exclude neutral bars from the denominator
    directional_bars = total - neutral_bars

    if directional_bars == 0:
        # All bars are neutral → flat
        return ("flat", "low")

    consistency_ratio = consistent_bars / directional_bars

    # Check for oscillation: count direction changes
    direction_changes = 0
    prev_signal = None
    for entry in indicator_history:
        signal = entry.get("signal", "").lower()
        if not signal or signal == "neutral":
            continue
        if prev_signal is not None and signal != prev_signal:
            direction_changes += 1
        prev_signal = signal

    # Oscillation detection: many direction changes relative to total
    if directional_bars > 2 and direction_changes >= directional_bars * 0.5:
        return ("oscillating", "medium")

    # Classification based on consistency ratio
    if consistency_ratio >= 0.75:
        return ("gradual_alignment", "low")
    elif consistency_ratio <= 0.25:
        return ("sudden_snap", "high")
    elif 0.4 <= consistency_ratio <= 0.6:
        return ("oscillating", "medium")
    else:
        # Mixed: not clearly gradual or sudden.
        # Ambiguity is treated as HIGH risk — the system has a wait bias,
        # not an action bias. ISC-007 checks coincidence_risk != "high",
        # so a mixed pattern must not slip through as "medium".
        # Verify > assume. Never assume ambiguity is safe.
        return ("sudden_snap", "high")


# ── Update trajectory accuracy from closed trades ──────────────────────────

def update_trajectory_accuracy(
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades: int = None,
    highlight_threshold: float = None,
    monitor_low_threshold: float = None,
    suppress_range: Tuple[float, float] = None,
    contrarian_threshold: float = None,
) -> None:
    """
    Recompute trajectory_accuracy from all closed trades for the given strategy.

    All thresholds are required — they come from the strategy config.
    None of them have hardcoded defaults; the caller must supply values
    read from substrate.cfg().

    Reads pre_trade_trajectory_pattern from trade_learning rows and aggregates
    win/loss stats per pattern. Writes to trajectory_accuracy table.

    Uses INSERT OR REPLACE (idempotent).
    """
    if any(v is None for v in (min_trades, highlight_threshold, monitor_low_threshold, suppress_range, contrarian_threshold)):
        missing = []
        if min_trades is None: missing.append("min_trades")
        if highlight_threshold is None: missing.append("highlight_threshold")
        if monitor_low_threshold is None: missing.append("monitor_low_threshold")
        if suppress_range is None: missing.append("suppress_range")
        if contrarian_threshold is None: missing.append("contrarian_threshold")
        raise TypeError(
            f"Required parameter(s) not provided to update_trajectory_accuracy: "
            + ", ".join(missing)
            + ". All learning thresholds must come from config (learning.*)."
        )

    from core.database import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT id, outcome, pre_trade_trajectory_pattern, pnl_pct
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL
                     AND pre_trade_trajectory_pattern IS NOT NULL
                     AND pre_trade_trajectory_pattern != ''""",
                (strategy_name,),
            ).fetchall()

            if not rows:
                _log.debug("No closed trades with trajectory data for '%s'", strategy_name)
                return

            # Aggregate per-pattern stats
            pattern_stats: Dict[str, Dict] = {}

            for row in rows:
                pattern = row["pre_trade_trajectory_pattern"]
                outcome = row["outcome"].lower() if row["outcome"] else ""
                pnl_pct = row["pnl_pct"] or 0.0

                if not pattern or not outcome:
                    continue

                if pattern not in pattern_stats:
                    pattern_stats[pattern] = {"trades": 0, "won": 0, "pnl_sum": 0.0}

                pattern_stats[pattern]["trades"] += 1
                pattern_stats[pattern]["pnl_sum"] += pnl_pct

                if outcome in ("win", "won"):
                    pattern_stats[pattern]["won"] += 1

            # Write to trajectory_accuracy table
            from learning.analyzer import classify_verdict

            for pattern, data in pattern_stats.items():
                trades = data["trades"]
                won = data["won"]
                win_rate_pct = (won / trades * 100) if trades > 0 else 0.0
                avg_pnl_pct = data["pnl_sum"] / trades if trades > 0 else 0.0

                # Use the same verdict classification as signal accuracy
                verdict = classify_verdict(
                    win_rate_pct, trades,
                    min_trades=min_trades,
                    highlight=highlight_threshold,
                    monitor_low=monitor_low_threshold,
                    suppress_range=suppress_range,
                    contrarian=contrarian_threshold,
                )

                conn.execute(
                    """INSERT OR REPLACE INTO trajectory_accuracy
                       (strategy_uid, trajectory_pattern, trades, won, win_rate_pct,
                        avg_pnl_pct, verdict)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_uid, pattern, trades, won, win_rate_pct, avg_pnl_pct, verdict),
                )

            _log.info(
                "Updated trajectory accuracy for '%s': %d patterns",
                strategy_name, len(pattern_stats),
            )

    except Exception as e:
        _log.error("Failed to update trajectory accuracy for '%s': %s", strategy_name, e, exc_info=True)


# ── Read trajectory verdicts ───────────────────────────────────────────────

def get_trajectory_verdicts(strategy_name: str, strategy_uid: str = "legacy") -> Dict[str, str]:
    """Return {pattern: verdict} dict from trajectory_accuracy table."""
    from core.database import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT trajectory_pattern, verdict FROM trajectory_accuracy WHERE strategy_uid = ?",
                (strategy_uid,),
            ).fetchall()

        return {row["trajectory_pattern"]: row["verdict"] for row in rows}

    except Exception as e:
        _log.error("Failed to read trajectory verdicts: %s", e, exc_info=True)
        return {}