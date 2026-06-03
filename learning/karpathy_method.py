"""
learning/karpathy_method.py -- Karpathy experiment loop (v1).

Proposes one parameter change at a time, evaluates it against historical
trade data, and pushes improvements to the CandidateQueue for Challenger
validation.

Karpathy method = propose one change -> evaluate -> keep if better, discard if worse.

Evaluation method (v1):
    Re-scores historical trades from trade_learning using the stored
    signals_at_entry_json with proposed indicator weights. Computes
    profit_factor from re-scored trades and compares to baseline.

    This is a CHEAP SCREENING HEURISTIC, not a rigorous backtest.
    It can only evaluate trades that were actually taken — it cannot
    see trades that were blocked by the current threshold but might
    have been profitable with different weights. This "selection bias"
    is a known limitation documented below.

Known limitation (v1):
    Karpathy v1 only evaluates trades that were actually opened. It
    cannot detect opportunities that the current weights blocked but
    shouldn't have (e.g. "entry_threshold is too strict — we missed
    good setups"). This is the "blocked but shouldn't be" blind spot.

    The Challenger's live paper-trading branch partially covers this
    case by running candidates in parallel. Hyperopt (the next module)
    provides a full OHLCV backtest that searches the entire parameter
    space including lower thresholds.

    Karpathy v2 (future): extend evaluation to include idle cycle
    near-misses with hypothetical_pnl_if_entered data from the
    idle_cycles table. This will allow Karpathy to evaluate "loosen
    threshold" proposals using actual hypothetical outcomes.

Integration:
    - Runs as a post-cycle hook in the daemon (non-blocking)
    - Pushes candidates to CandidateQueue with source="karpathy"
    - Challenger validates before any candidate reaches production
    - Every experiment logged to karpathy_log table

Config keys (from config/default.yaml karpathy section):
    karpathy.enabled              -- master switch (default: false)
    karpathy.step_size            -- weight change per experiment (default: 0.05)
    karpathy.max_experiments_per_cycle -- experiments per daemon cycle (default: 1)
    karpathy.min_trades_for_eval  -- minimum historical trades needed (default: 20)
    karpathy.interval_hours       -- minimum hours between runs (default: 24)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from core.database import db_conn

_log = logging.getLogger(__name__)


class KarpathyMethod:
    """Karpathy experiment loop: propose one change, evaluate, push if better.

    V1: Re-scores historical trade_learning records with proposed weights.
    Does NOT perform a full OHLCV backtest (that is Hyperopt's job).
    """

    @staticmethod
    def run_experiment_cycle(substrate: Any) -> None:
        """Run one experiment cycle: propose, evaluate, push if improved.

        This is the main entry point, called from the daemon's post-cycle hook.
        All errors are caught and logged — production must never be affected.
        """
        try:
            enabled = substrate.cfg("karpathy.enabled", False)
            if not enabled:
                return

            # Rate limiting: don't run if last run was too recent
            if _too_soon_to_run(substrate):
                _log.debug("Karpathy: skipping — last run was too recent")
                return

            step_size = substrate.cfg("karpathy.step_size", 0.05)
            max_experiments = substrate.cfg("karpathy.max_experiments_per_cycle", 1)
            min_trades = substrate.cfg("karpathy.min_trades_for_eval", 20)

            current_weights = _get_current_weights(substrate)
            if not current_weights:
                _log.debug("Karpathy: no indicator weights to experiment with")
                return

            # Deduplication check: if current weights are already queued,
            # skip the entire cycle to avoid wasted computation.
            if _current_weights_already_queued(substrate):
                _log.debug("Karpathy: current weights already in CandidateQueue — skipping")
                return

            # Record last run timestamp NOW — any path past this point counts
            # as "we tried" and should rate-limit the next attempt. This prevents
            # hammering the DB every 15-min cycle when there aren't enough trades.
            substrate.learning["karpathy_last_run_at"] = datetime.now(
                timezone.utc,
            ).isoformat()

            # Compute baseline profit_factor from historical trades
            baseline_pf, trade_count = _evaluate_weights(
                current_weights, substrate, min_trades,
            )
            if baseline_pf is None:
                _log.debug(
                    "Karpathy: insufficient trade history (%d < %d)",
                    trade_count, min_trades,
                )
                return

            if baseline_pf <= 0.0:
                _log.debug(
                    "Karpathy: baseline profit_factor is %.3f — nothing to improve from",
                    baseline_pf,
                )
                return

            # Run experiments: cycle through indicators
            indicators = list(current_weights.keys())
            last_idx = substrate.learning.get("karpathy_last_indicator_idx", -1)

            experiments_run = 0
            weights_changed = False

            for i in range(max_experiments):
                idx = (last_idx + 1 + i) % len(indicators)
                param_name = indicators[idx]
                old_value = current_weights[param_name]

                # Propose experiments: try decrease first (simplicity bias),
                # then increase. Pick the better one.
                best_direction = None
                best_pf = baseline_pf
                best_new_value = old_value

                for direction in [-1, +1]:  # Decrease first (simplicity bias)
                    new_val = old_value + direction * step_size
                    if new_val < 0:
                        continue  # Don't propose negative weights
                    if new_val == old_value:
                        continue  # No change

                    proposed = dict(current_weights)
                    proposed[param_name] = new_val

                    # Deduplication: skip if this exact weight set is already queued
                    if _weights_already_queued(proposed, substrate):
                        continue

                    pf, _ = _evaluate_weights(proposed, substrate, min_trades)
                    if pf is not None and pf > best_pf:
                        best_pf = pf
                        best_direction = direction
                        best_new_value = new_val

                if best_direction is not None:
                    # Improvement found — push to CandidateQueue
                    proposed_weights = dict(current_weights)
                    proposed_weights[param_name] = best_new_value

                    KarpathyMethod.push_candidate_if_improved(
                        proposed_weights, best_pf, baseline_pf, substrate,
                        param_changed=param_name,
                        old_val=old_value,
                        new_val=best_new_value,
                        trade_count=trade_count,
                    )
                    # Update current_weights for next experiment in this cycle
                    current_weights[param_name] = best_new_value
                    weights_changed = True
                else:
                    # No improvement — log the discard
                    _log_experiment(
                        substrate, param_name, old_value, old_value,
                        baseline_pf, None, trade_count,
                        "discarded", "no improvement found for this parameter",
                    )

                experiments_run += 1
                # Update the cycling index
                substrate.learning["karpathy_last_indicator_idx"] = idx

            if experiments_run > 0:
                _log.info(
                    "Karpathy: %d experiments run, weights_changed=%s",
                    experiments_run, weights_changed,
                )
        except Exception as e:
            _log.error(
                "Karpathy experiment cycle failed: %s", e, exc_info=True,
            )

    @staticmethod
    def push_candidate_if_improved(
        new_weights: Dict[str, float],
        proposed_pf: float,
        baseline_pf: float,
        substrate: Any,
        param_changed: str = "",
        old_val: float = 0.0,
        new_val: float = 0.0,
        trade_count: int = 0,
    ) -> None:
        """Push a candidate to the CandidateQueue if backtest improved.

        Only pushes if proposed_pf > baseline_pf (caller should ensure this).
        Logs the experiment to karpathy_log regardless.
        """
        from learning.challenger import CandidateQueue

        improvement = proposed_pf - baseline_pf
        reason = (
            f"profit_factor {baseline_pf:.3f} -> {proposed_pf:.3f} "
            f"(+{improvement:.3f}), {param_changed}: {old_val:.3f} -> {new_val:.3f}"
        )

        # Push to CandidateQueue — Challenger validates before production
        CandidateQueue.push(
            new_weights,
            source="karpathy",
            substrate=substrate,
            metadata={
                "param_changed": param_changed,
                "old_value": old_val,
                "new_value": new_val,
                "baseline_profit_factor": baseline_pf,
                "proposed_profit_factor": proposed_pf,
            },
        )

        _log_experiment(
            substrate, param_changed, old_val, new_val,
            baseline_pf, proposed_pf, trade_count,
            "kept", reason,
        )

        _log.info(
            "Karpathy: KEPT %s %.3f->%.3f (pf %.3f->%.3f)",
            param_changed, old_val, new_val, baseline_pf, proposed_pf,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _too_soon_to_run(substrate: Any) -> bool:
    """Check if not enough time has passed since the last Karpathy run."""
    interval_hours = substrate.cfg("karpathy.interval_hours", 24)
    last_run = substrate.learning.get("karpathy_last_run_at", "")
    if not last_run:
        return False  # Never run before — OK to proceed

    try:
        last_dt = datetime.fromisoformat(last_run)
        now = datetime.now(timezone.utc)
        elapsed_hours = (now - last_dt).total_seconds() / 3600
        return elapsed_hours < interval_hours
    except (ValueError, TypeError):
        return False  # Can't parse timestamp — allow run


def _get_current_weights(substrate: Any) -> Dict[str, float]:
    """Get current indicator weights from substrate.

    Prefers adjusted_weights (learning-adjusted) over config defaults.
    Only returns indicators with weight > 0.
    """
    # First try adjusted weights (learning-adjusted)
    adjusted = substrate.learning.get("adjusted_weights", {})
    if adjusted and isinstance(adjusted, dict) and any(
        v > 0 for v in adjusted.values()
    ):
        return {k: v for k, v in adjusted.items() if v > 0}

    # Fall back to config defaults
    indicator_configs = substrate.cfg("indicators", [])
    weights = {}
    for cfg in indicator_configs:
        name = cfg.get("name", "")
        weight = cfg.get("weight", 0)
        if weight > 0 and name:
            weights[name] = weight

    return weights


def _current_weights_already_queued(substrate: Any) -> bool:
    """Check if the current production weights are already in the CandidateQueue."""
    return _weights_already_queued(_get_current_weights(substrate), substrate)


def _weights_already_queued(
    weights: Dict[str, float], substrate: Any,
) -> bool:
    """Check if an identical weight set from karpathy is already in the queue."""
    challenger = substrate.learning.get("challenger", {})
    queue = challenger.get("candidate_queue", [])
    for entry in queue:
        if entry.get("source") != "karpathy":
            continue
        entry_weights = entry.get("weights", {})
        if _weights_equal(weights, entry_weights):
            return True
    return False


def _weights_equal(a: Dict[str, float], b: Dict[str, float]) -> bool:
    """Check if two weight dicts are approximately equal."""
    if set(a.keys()) != set(b.keys()):
        return False
    return all(abs(a[k] - b[k]) < 1e-9 for k in a)


def _evaluate_weights(
    weights: Dict[str, float],
    substrate: Any,
    min_trades: int,
) -> Tuple[Optional[float], int]:
    """Evaluate a set of indicator weights against historical trade data.

    Re-scores past trades using proposed weights and computes profit_factor.
    Only considers trades where the re-scored entry would have been taken
    (score >= entry_threshold).

    Returns (profit_factor, trade_count). Returns (None, count) if
    insufficient data.
    """
    strategy_uid = substrate.strategy.get("uid", "legacy")
    entry_threshold = substrate.cfg("scoring.entry_threshold")

    try:
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT direction, pnl_pct, signals_at_entry_json
                   FROM trade_learning
                   WHERE strategy_uid = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL
                     AND signals_at_entry_json IS NOT NULL
                     AND signals_at_entry_json != ''
                   ORDER BY entry_time DESC
                   LIMIT 200""",
                (strategy_uid,),
            ).fetchall()

        if len(rows) < min_trades:
            return None, len(rows)

        wins = 0.0
        losses = 0.0
        trades_evaluated = 0

        for row in rows:
            try:
                signals = json.loads(row["signals_at_entry_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(signals, dict) or not signals:
                continue

            direction = row["direction"] or "Long"
            pnl = row["pnl_pct"] if row["pnl_pct"] is not None else 0.0

            score = _compute_score_from_signals(signals, weights, direction)

            # Trade would have been taken if score meets threshold
            if abs(score) >= entry_threshold:
                trades_evaluated += 1
                if pnl > 0:
                    wins += pnl
                else:
                    losses += abs(pnl)

        if losses == 0 or wins == 0:
            return 0.0, len(rows)

        return round(wins / losses, 3), len(rows)

    except Exception as e:
        _log.error("Failed to evaluate weights: %s", e, exc_info=True)
        return None, 0


def _compute_score_from_signals(
    signals: Dict[str, Any],
    weights: Dict[str, float],
    direction: str,
) -> float:
    """Re-score a trade using proposed weights and stored signal states.

    For each indicator with weight > 0:
        - bullish signal -> +weight contribution
        - bearish signal -> -weight contribution
        - neutral signal -> 0 contribution

    Normalized to 0-10 scale (same as ScoreConfluence).

    For Short trades, the score is inverted (bearish is positive direction).
    """
    score = 0.0
    max_score = 0.0
    is_long = direction.lower() in ("long", "buy")

    for indicator, weight in weights.items():
        if weight <= 0:
            continue

        signal_data = signals.get(indicator)
        if not isinstance(signal_data, dict):
            continue

        signal = signal_data.get("signal", "neutral")

        if signal == "bullish":
            score += weight
        elif signal == "bearish":
            score -= weight
        # neutral contributes 0

        max_score += weight

    if max_score == 0:
        return 0.0

    # Normalize to 0-10 scale (same as ScoreConfluence)
    normalized = (score / max_score) * 10.0

    # For short trades, invert: bearish signals are the "positive" direction
    if not is_long:
        normalized = -normalized

    return round(normalized, 2)


def _log_experiment(
    substrate: Any,
    param_changed: str,
    old_value: float,
    new_value: float,
    baseline_pf: Optional[float],
    proposed_pf: Optional[float],
    trade_count: int,
    kept_or_discarded: str,
    reason: str,
) -> None:
    """Log an experiment to the karpathy_log table.

    Never raises — production must not be affected by logging failures.
    """
    try:
        strategy_uid = substrate.strategy.get("uid", "legacy")

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO karpathy_log
                   (strategy_uid, param_changed, old_value, new_value,
                    baseline_profit_factor, proposed_profit_factor,
                    backtest_trades_count, kept_or_discarded, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_uid,
                    param_changed,
                    old_value,
                    new_value,
                    baseline_pf,
                    proposed_pf,
                    trade_count,
                    kept_or_discarded,
                    reason,
                ),
            )
    except Exception as e:
        _log.error(
            "Failed to log karpathy experiment: %s", e, exc_info=True,
        )


