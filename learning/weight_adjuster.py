"""
learning/weight_adjuster.py -- Adjust indicator weights based on signal accuracy verdicts.

Reads verdicts from signal_accuracy and adjusts weights:
  - 'valid' (≥75%):    boost weight by 20% (multiply by 1.2)
  - 'monitor' (55-75%): keep weight unchanged
  - 'suppress' (45-55%): set weight to 0.0 (coin flip, ignore)
  - 'contrarian' (≤30%): set weight to NEGATIVE original value
                         (invert the signal's contribution in scoring)
  - 'review' (30-45%):  reduce weight by 10% (multiply by 0.9)
  - 'insufficient_data': keep original weight

After adjustment, positive weights are re-normalized so they sum to the
original total (preserving relative scale). Negative weights are left
as-is — they represent contrarian signals that ScoreConfluence should
invert (subtract bullish contribution instead of adding).

Safety guard:
  If ALL signals would be suppressed/contrarian, the system cannot trade.
  In that case, return original weights unchanged to prevent a dead system.

Every weight change is recorded in weight_history with justification text
and the accuracy data that justified the change. This allows auditing.

Connection safety: db_conn() context manager, always closed.
"""

from __future__ import annotations

import logging
from typing import Dict

_log = logging.getLogger(__name__)



def compute_adjusted_weights(
    current_weights: Dict[str, float],
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades: int = None,
    adjustment_boost: float = None,
    adjustment_review_reduce: float = None,
) -> Dict[str, float]:
    """
    Compute adjusted indicator weights based on signal accuracy verdicts.

    All parameters are required — they must come from substrate.cfg().
    No hardcoded defaults. Config is the single source of truth.

    min_trades: from learning.min_trades_before_adjusting
    adjustment_boost: from learning.adjustment_boost (e.g. 1.2 = +20%)
    adjustment_review_reduce: from learning.adjustment_review_reduce (e.g. 0.9 = -10%)

    Contrarian signals get NEGATIVE weights. This is the key insight:
    a signal with ≤30% accuracy fires "bullish" but the market moves bearish.
    ScoreConfluence should invert its contribution: when it fires bullish,
    subtract from the long score instead of adding. A negative weight achieves
    this automatically.

    Args:
        current_weights: Dict of {indicator_name: current_weight}.
        strategy_name:   Strategy name (for DB lookups and weight_history).
        min_trades:      Minimum total trades before any adjustment happens.

    Returns:
        Dict of {indicator_name: adjusted_weight}. If below min_trades,
        returns current_weights unchanged.

    Writes to weight_history for each changed weight.
    """
    if min_trades is None:
        raise TypeError(
            "Required parameter 'min_trades' not provided to compute_adjusted_weights. "
            "It must come from config (learning.min_trades_before_adjusting)."
        )
    if adjustment_boost is None:
        raise TypeError(
            "Required parameter 'adjustment_boost' not provided to compute_adjusted_weights. "
            "It must come from config (learning.adjustment_boost)."
        )
    if adjustment_review_reduce is None:
        raise TypeError(
            "Required parameter 'adjustment_review_reduce' not provided to compute_adjusted_weights. "
            "It must come from config (learning.adjustment_review_reduce)."
        )

    from core.database import db_conn

    # -- Check if we have enough trades --------------------------------------
    try:
        with db_conn() as conn:
            total_trades = conn.execute(
                """SELECT COUNT(*) FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL""",
                (strategy_name,),
            ).fetchone()[0]

        if total_trades < min_trades:
            _log.debug("Only %d trades (< %d threshold), keeping original weights",
                       total_trades, min_trades)
            return current_weights

    except Exception as e:
        _log.error("Failed to count trades for weight adjustment: %s", e, exc_info=True)
        return current_weights

    # -- Read signal verdicts ------------------------------------------------
    try:
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT indicator_name, verdict, accuracy_pct, total_fired
                   FROM signal_accuracy
                   WHERE strategy_uid = ?""",
                (strategy_uid,),
            ).fetchall()

        verdicts = {row["indicator_name"]: {
            "verdict": row["verdict"],
            "accuracy": row["accuracy_pct"],
            "total": row["total_fired"],
        } for row in rows}

    except Exception as e:
        _log.error("Failed to read signal verdicts for weight adjustment: %s", e, exc_info=True)
        return current_weights

    # -- Apply adjustments ---------------------------------------------------
    original_total = sum(v for v in current_weights.values() if v > 0)
    adjusted: Dict[str, float] = {}
    changes: Dict[str, Dict] = {}  # indicator_name → {old, new, justification}

    for indicator, weight in current_weights.items():
        info = verdicts.get(indicator)

        if info is None or info["verdict"] == "insufficient_data":
            # No verdict data → keep original weight
            adjusted[indicator] = weight
            continue

        verdict = info["verdict"]
        accuracy = info["accuracy"]

        if verdict == "valid":
            # Boost by adjustment_boost (e.g. 1.2 = +20%)
            new_weight = weight * adjustment_boost
            adjusted[indicator] = new_weight
            changes[indicator] = {
                "old": weight, "new": new_weight,
                "justification": f"accuracy {accuracy:.0f}% (valid), highlight boost +{int((adjustment_boost - 1) * 100)}%",
            }

        elif verdict == "monitor":
            # Keep unchanged
            adjusted[indicator] = weight

        elif verdict == "suppress":
            # Set to 0 (coin flip)
            new_weight = 0.0
            adjusted[indicator] = new_weight
            changes[indicator] = {
                "old": weight, "new": new_weight,
                "justification": f"accuracy {accuracy:.0f}% (suppress), coin flip → weight=0",
            }

        elif verdict == "contrarian":
            # NEGATIVE weight: invert the signal's contribution
            # The magnitude stays the same, but the sign flips.
            # ScoreConfluence interprets: bullish signal with -0.5 weight → subtract 0.5
            new_weight = -abs(weight)
            adjusted[indicator] = new_weight
            changes[indicator] = {
                "old": weight, "new": new_weight,
                "justification": f"accuracy {accuracy:.0f}% (contrarian), anti-signal → invert weight",
            }

        elif verdict == "review":
            # Reduce by adjustment_review_reduce (e.g. 0.9 = -10%)
            new_weight = weight * adjustment_review_reduce
            adjusted[indicator] = new_weight
            changes[indicator] = {
                "old": weight, "new": new_weight,
                "justification": f"accuracy {accuracy:.0f}% (review), borderline → reduce -{int((1 - adjustment_review_reduce) * 100)}%",
            }

        else:
            # Unknown verdict → keep original
            adjusted[indicator] = weight

    # -- Safety guard: cannot zero out everything ----------------------------
    positive_sum = sum(v for v in adjusted.values() if v > 0)
    if positive_sum == 0:
        _log.warning("All weights would be ≤0 — safety guard: returning original weights")
        return current_weights

    # -- Re-normalize positive weights ---------------------------------------
    # Positive weights should sum to the original total (preserving relative scale).
    # Negative weights are left as-is — they represent contrarian signals.
    if positive_sum > 0 and original_total > 0:
        scale = original_total / positive_sum
        for indicator in adjusted:
            if adjusted[indicator] > 0:
                adjusted[indicator] *= scale

    # -- Write weight_history for changes ------------------------------------
    if changes:
        try:
            with db_conn() as conn:
                for indicator, change in changes.items():
                    info = verdicts.get(indicator, {})
                    conn.execute(
                        """INSERT INTO weight_history
                           (strategy_uid, indicator_name, old_weight, new_weight,
                            justification, accuracy_at_time, sample_size_at_time)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (strategy_uid, indicator, change["old"], change["new"],
                         change["justification"],
                         info.get("accuracy", 0.0), info.get("total", 0)),
                    )

            _log.info("Adjusted %d weights for '%s'", len(changes), strategy_name)

        except Exception as e:
            _log.error("Failed to write weight_history: %s", e, exc_info=True)

    return adjusted


def compute_adjusted_thresholds(
    current_penalties: Dict[str, float],
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades: int = 30,
    adjustment_rate: float = 0.05,
) -> Dict[str, float]:
    """
    Compute adjusted soft penalty thresholds based on trade outcomes.

    If trades made under a penalty are winning at a higher rate than
    the overall win rate, the penalty is too aggressive and should be
    reduced. If they're losing more, the penalty should increase.

    This allows the learning engine to tune soft penalties over time,
    making the system more or less aggressive based on real outcomes.

    Args:
        current_penalties: Dict of {penalty_name: current_ratio}.
            e.g. {"noise_penalty_ratio": 0.3, "confluence_penalty_ratio": 0.3, ...}
        strategy_name: Strategy name for DB lookups.
        strategy_uid: Strategy UID for DB lookups.
        min_trades: Minimum trades before any adjustment.
        adjustment_rate: Max adjustment per call (e.g. 0.05 = ±5%).

    Returns:
        Dict of {penalty_name: adjusted_ratio}. Clamped to [0.0, 1.0].
    """
    from core.database import db_conn

    # Check if we have enough trades
    try:
        with db_conn() as conn:
            total_trades = conn.execute(
                """SELECT COUNT(*) FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL""",
                (strategy_name,),
            ).fetchone()[0]

        if total_trades < min_trades:
            return current_penalties

    except Exception as e:
        _log.error("Failed to count trades for threshold adjustment: %s", e, exc_info=True)
        return current_penalties

    adjusted = dict(current_penalties)

    # For each penalty, check if penalized trades are winning more than expected
    # This is a simple heuristic: if the overall win rate is >50% for trades
    # that had this penalty active, the penalty is too aggressive.
    try:
        with db_conn() as conn:
            # Get overall win rate
            overall = conn.execute(
                """SELECT
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL""",
                (strategy_name,),
            ).fetchone()

            if not overall or overall["total"] == 0:
                return current_penalties

            overall_win_rate = overall["wins"] / overall["total"]

            # Check penalized trades (trades with effective_score < raw_score)
            penalized = conn.execute(
                """SELECT
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND effective_score IS NOT NULL
                     AND effective_score < score""",
                (strategy_name,),
            ).fetchone()

            if not penalized or penalized["total"] < 5:
                return current_penalties  # Not enough penalized trades

            penalized_win_rate = penalized["wins"] / penalized["total"]

            # If penalized trades win more than overall, penalties are too aggressive
            # If penalized trades win less, penalties are appropriate or too lenient
            delta = penalized_win_rate - overall_win_rate

            # Apply proportional adjustment to all penalty ratios
            # delta > 0 → reduce penalties (they're blocking good trades)
            # delta < 0 → increase penalties (they're not blocking bad trades enough)
            for key, current_ratio in current_penalties.items():
                # Scale adjustment by delta and cap at adjustment_rate
                change = max(-adjustment_rate, min(adjustment_rate, delta * adjustment_rate * 2))
                new_ratio = round(max(0.0, min(1.0, current_ratio - change)), 3)
                if new_ratio != current_ratio:
                    adjusted[key] = new_ratio
                    _log.info(
                        "Adjusted %s: %.3f → %.3f (penalized WR=%.1f%%, overall WR=%.1f%%)",
                        key, current_ratio, new_ratio,
                        penalized_win_rate * 100, overall_win_rate * 100,
                    )

    except Exception as e:
        _log.error("Failed to compute adjusted thresholds: %s", e, exc_info=True)
        return current_penalties

    return adjusted
