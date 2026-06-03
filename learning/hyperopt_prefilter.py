"""
learning/hyperopt_prefilter.py -- Hyperopt prefilter for the Challenger system.

Systematically searches for optimal parameter candidates using Optuna TPE,
then passes best candidates to the CandidateQueue for Challenger validation.

Evaluation method:
    Re-scores historical trades from trade_learning using the stored
    signals_at_entry_json with proposed indicator weights. Computes
    a composite score from profit_factor and sharpe_ratio.

    This is the SAME re-scoring approach as Karpathy (v1), but with
    a richer objective function and multi-dimensional search.

    Composite objective:
        score = profit_factor * (1 + alpha * max(0, sharpe_ratio))

    Where alpha is configurable (hyperopt.sharpe_alpha, default 0.3).
    This means stable weight combinations are preferred over erratic ones
    with the same raw profit_factor.

Known limitation (shared with Karpathy v1):
    Only evaluates trades that were actually taken. Cannot detect
    opportunities that the current weights blocked but shouldn't have.
    This is the "blocked but shouldn't be" blind spot.

    The Challenger's live paper-trading branch partially covers this.
    Karpathy v2 (future) will address it with idle cycle near-misses.

Overfitting detection:
    After the Optuna search, runs PBO (Probability of Backtest Overfitting)
    on the top candidate's re-scored trade returns. If PBO > 0.5, logs a
    warning. Still pushes to Challenger — live validation is the final
    arbiter — but the warning is recorded in hyperopt_log.

Integration:
    - Runs as a post-cycle hook in the daemon (non-blocking)
    - Pushes candidates to CandidateQueue with source="hyperopt"
    - Challenger validates before any candidate reaches production
    - Every search logged to hyperopt_log table

Config keys (from config/default.yaml hyperopt section):
    hyperopt.enabled              -- master switch (default: false)
    hyperopt.n_trials             -- Optuna trials per search (default: 100)
    hyperopt.top_n_candidates     -- candidates pushed to queue (default: 3)
    hyperopt.search_interval_hours -- minimum hours between searches (default: 24)
    hyperopt.search_width         -- neighborhood radius around current weights (default: 0.5)
    hyperopt.min_trades_for_eval  -- minimum historical trades needed (default: 20)
    hyperopt.sharpe_alpha         -- Sharpe bonus weight in composite score (default: 0.3)
"""

from __future__ import annotations

import json
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import optuna

optuna.logging.set_verbosity(optuna.logging.WARNING)

from core.database import db_conn
from learning.metrics import profit_factor, sharpe_ratio, pbo

_log = logging.getLogger(__name__)


class HyperoptPrefilter:
    """Hyperopt prefilter: systematic parameter search via Optuna TPE.

    Searches the indicator weight space using Optuna's TPE sampler,
    evaluates candidates by re-scoring historical trades, and pushes
    the top-N to the CandidateQueue for Challenger validation.
    """

    @staticmethod
    def run_search(substrate: Any) -> None:
        """Run one hyperopt search cycle and push top candidates.

        This is the main entry point, called from the daemon's post-cycle hook.
        All errors are caught and logged — production must never be affected.
        """
        try:
            enabled = substrate.cfg("hyperopt.enabled", False)
            if not enabled:
                return

            # Rate limiting: don't run if last run was too recent
            if _too_soon_to_run(substrate):
                _log.debug("Hyperopt: skipping — last search was too recent")
                return

            # Get current production weights as starting point
            current_weights = _get_current_weights(substrate)
            if not current_weights:
                _log.debug("Hyperopt: no indicator weights to search")
                return

            # Deduplication check: if current weights are already queued,
            # skip the entire search to avoid wasted computation.
            if _current_weights_already_queued(substrate):
                _log.debug("Hyperopt: current weights already in CandidateQueue — skipping")
                return

            n_trials = substrate.cfg("hyperopt.n_trials", 100)
            top_n = substrate.cfg("hyperopt.top_n_candidates", 3)
            min_trades = substrate.cfg("hyperopt.min_trades_for_eval", 20)
            search_width = substrate.cfg("hyperopt.search_width", 0.5)
            sharpe_alpha = substrate.cfg("hyperopt.sharpe_alpha", 0.3)

            # Record last run timestamp NOW — any path past this point counts
            # as "we tried" and should rate-limit the next attempt.
            substrate.learning["hyperopt_last_run_at"] = datetime.now(
                timezone.utc,
            ).isoformat()

            # Compute baseline profit_factor from historical trades
            baseline_pf, baseline_sharpe, baseline_pnls, trade_count = (
                _evaluate_weights(current_weights, substrate, min_trades)
            )
            if baseline_pf is None:
                _log.debug(
                    "Hyperopt: insufficient trade history (%d < %d)",
                    trade_count, min_trades,
                )
                _log_search(
                    substrate, n_trials=n_trials, baseline_pf=None,
                    best_pf=None, candidates_pushed=0,
                    search_space=current_weights, best_weights=None,
                    duration_sec=0.0,
                    reason=f"insufficient trades ({trade_count} < {min_trades})",
                )
                return

            if baseline_pf <= 0.0:
                _log.debug(
                    "Hyperopt: baseline profit_factor is %.3f — nothing to improve from",
                    baseline_pf,
                )
                _log_search(
                    substrate, n_trials=n_trials, baseline_pf=baseline_pf,
                    best_pf=None, candidates_pushed=0,
                    search_space=current_weights, best_weights=None,
                    duration_sec=0.0,
                    reason=f"baseline pf {baseline_pf:.3f} <= 0",
                )
                return

            # Run Optuna search
            t0 = _time.time()
            candidates = _run_optuna_search(
                current_weights=current_weights,
                search_width=search_width,
                substrate=substrate,
                n_trials=n_trials,
                min_trades=min_trades,
                sharpe_alpha=sharpe_alpha,
            )
            duration_sec = round(_time.time() - t0, 1)

            if not candidates:
                _log.info(
                    "Hyperopt: no candidates with pf > baseline (%.3f) in %d trials (%.1fs)",
                    baseline_pf, n_trials, duration_sec,
                )
                _log_search(
                    substrate, n_trials=n_trials, baseline_pf=baseline_pf,
                    best_pf=None, candidates_pushed=0,
                    search_space=current_weights, best_weights=None,
                    duration_sec=duration_sec,
                    reason="no improvement found",
                )
                return

            # Filter: only candidates better than baseline
            improved = [
                (w, pf, sr) for w, pf, sr in candidates
                if pf > baseline_pf
            ]

            if not improved:
                _log.info(
                    "Hyperopt: %d candidates found but none beat baseline pf %.3f",
                    len(candidates), baseline_pf,
                )
                _log_search(
                    substrate, n_trials=n_trials, baseline_pf=baseline_pf,
                    best_pf=candidates[0][1] if candidates else None,
                    candidates_pushed=0,
                    search_space=current_weights,
                    best_weights=candidates[0][0] if candidates else None,
                    duration_sec=duration_sec,
                    reason="no candidate beat baseline",
                )
                return

            # Deduplicate: skip candidates already in the CandidateQueue
            unique_improved = []
            for w, pf, sr in improved:
                if not _weights_already_queued(w, substrate):
                    unique_improved.append((w, pf, sr))

            # Take top-N
            top_candidates = unique_improved[:top_n]

            # Overfitting check on the best candidate
            best_weights, best_pf, best_sr = top_candidates[0]
            _, _, best_pnls, _ = _evaluate_weights(
                best_weights, substrate, min_trades,
            )
            overfitting_warning = None
            if best_pnls and len(best_pnls) >= 40:
                pbo_val = pbo(best_pnls)
                if pbo_val == pbo_val and pbo_val > 0.5:  # not NaN
                    overfitting_warning = (
                        f"PBO={pbo_val:.3f} > 0.5 — candidate may be overfitted"
                    )
                    _log.warning(
                        "Hyperopt: %s (pf=%.3f, sharpe=%.2f)",
                        overfitting_warning, best_pf, best_sr,
                    )

            # Push candidates to CandidateQueue
            pushed = HyperoptPrefilter.push_top_candidates(
                top_candidates, substrate,
                metadata={
                    "baseline_profit_factor": baseline_pf,
                    "n_trials": n_trials,
                    "search_width": search_width,
                    "overfitting_warning": overfitting_warning,
                },
            )

            _log.info(
                "Hyperopt: %d/%d candidates pushed (best pf %.3f->%.3f, "
                "sharpe %.2f, %d trials, %.1fs)",
                pushed, len(top_candidates), baseline_pf, best_pf,
                best_sr, n_trials, duration_sec,
            )

            _log_search(
                substrate, n_trials=n_trials, baseline_pf=baseline_pf,
                best_pf=best_pf, candidates_pushed=pushed,
                search_space=current_weights, best_weights=best_weights,
                duration_sec=duration_sec,
                reason=(
                    f"best pf {baseline_pf:.3f}->{best_pf:.3f}, "
                    f"sharpe {best_sr:.2f}"
                    + (f", {overfitting_warning}" if overfitting_warning else "")
                ),
            )

        except Exception as e:
            _log.error(
                "Hyperopt search failed (production unaffected): %s",
                e, exc_info=True,
            )

    @staticmethod
    def push_top_candidates(
        candidates: List[Tuple[Dict[str, float], float, float]],
        substrate: Any,
        metadata: Optional[Dict] = None,
    ) -> int:
        """Push top-N candidates from hyperopt search to the CandidateQueue.

        Args:
            candidates: List of (weights, profit_factor, sharpe_ratio) tuples.
            substrate: The substrate object.
            metadata: Optional shared metadata for all candidates.

        Returns:
            Number of candidates actually pushed.
        """
        from learning.challenger import CandidateQueue

        pushed = 0
        for weights, pf, sr in candidates:
            candidate_meta = dict(metadata or {})
            candidate_meta["proposed_profit_factor"] = pf
            candidate_meta["proposed_sharpe_ratio"] = round(sr, 3)
            candidate_meta["source_module"] = "hyperopt_prefilter"

            CandidateQueue.push(
                weights,
                source="hyperopt",
                substrate=substrate,
                metadata=candidate_meta,
            )
            pushed += 1

        return pushed


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _too_soon_to_run(substrate: Any) -> bool:
    """Check if not enough time has passed since the last Hyperopt run."""
    interval_hours = substrate.cfg("hyperopt.search_interval_hours", 24)
    last_run = substrate.learning.get("hyperopt_last_run_at", "")
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

    Reuses the same logic as Karpathy: prefers adjusted_weights
    (learning-adjusted) over config defaults. Only returns indicators
    with weight > 0.
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
    """Check if an identical weight set from hyperopt is already in the queue."""
    challenger = substrate.learning.get("challenger", {})
    queue = challenger.get("candidate_queue", [])
    for entry in queue:
        if entry.get("source") != "hyperopt":
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
) -> Tuple[Optional[float], Optional[float], Optional[List[float]], int]:
    """Evaluate a set of indicator weights against historical trade data.

    Re-scores past trades using proposed weights and computes profit_factor
    and sharpe_ratio from the re-scored trades.

    Returns (profit_factor, sharpe_ratio, pnl_list, trade_count).
    Returns (None, None, None, count) if insufficient data.
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
            return None, None, None, len(rows)

        pnls = []

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
                pnls.append(pnl)

        if not pnls:
            return 0.0, 0.0, [], len(rows)

        pf = profit_factor(pnls)
        sr = sharpe_ratio(pnls)

        return pf, sr, pnls, len(rows)

    except Exception as e:
        _log.error("Failed to evaluate weights: %s", e, exc_info=True)
        return None, None, None, 0


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


def _run_optuna_search(
    current_weights: Dict[str, float],
    search_width: float,
    substrate: Any,
    n_trials: int,
    min_trades: int,
    sharpe_alpha: float,
) -> List[Tuple[Dict[str, float], float, float]]:
    """Run an Optuna TPE search over the indicator weight space.

    Returns a list of (weights, profit_factor, sharpe_ratio) tuples,
    sorted by composite score descending. Only includes candidates
    that were successfully evaluated (non-None profit_factor).
    """
    indicator_names = list(current_weights.keys())

    # Cache trade data for the entire search — avoids N DB queries
    trade_rows = _load_trade_rows(substrate)
    if len(trade_rows) < min_trades:
        _log.debug(
            "Hyperopt: only %d trade rows (need %d) — aborting search",
            len(trade_rows), min_trades,
        )
        return []

    entry_threshold = substrate.cfg("scoring.entry_threshold")

    def objective(trial: optuna.Trial) -> float:
        """Optuna objective: propose weights, evaluate, return composite score."""
        proposed = {}
        for name in indicator_names:
            current_val = current_weights[name]
            low = max(0.0, current_val - search_width)
            high = current_val + search_width
            proposed[name] = trial.suggest_float(name, low, high)

        # Evaluate proposed weights using cached trade data
        pf, sr = _evaluate_from_cache(
            proposed, trade_rows, entry_threshold,
        )

        if pf is None or pf <= 0.0:
            return 0.0  # Penalty: no valid evaluation

        # Composite score: profit_factor * (1 + alpha * max(0, sharpe))
        composite = pf * (1.0 + sharpe_alpha * max(0.0, sr))
        return composite

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
    )

    try:
        study.optimize(objective, n_trials=n_trials, n_jobs=1, show_progress_bar=False)
    except Exception as e:
        _log.error("Optuna optimization failed: %s", e, exc_info=True)
        return []

    # Collect completed trials with their weights and scores
    completed = [
        t for t in study.trials
        if t.state == optuna.trial.TrialState.COMPLETE and t.value is not None and t.value > 0
    ]

    if not completed:
        return []

    # Sort by composite score descending
    completed.sort(key=lambda t: t.value, reverse=True)

    # Build result list with actual profit_factor and sharpe_ratio
    results = []
    seen_weights = set()

    for trial in completed:
        weights = {name: trial.params[name] for name in indicator_names}
        # Round weights to avoid near-duplicate candidates
        weight_key = tuple(round(weights[k], 4) for k in sorted(weights.keys()))
        if weight_key in seen_weights:
            continue
        seen_weights.add(weight_key)

        pf, sr = _evaluate_from_cache(
            weights, trade_rows, entry_threshold,
        )
        if pf is not None and pf > 0.0:
            results.append((weights, pf, sr))

    # Sort by profit_factor descending (primary metric for Challenger)
    results.sort(key=lambda x: x[1], reverse=True)

    return results


def _load_trade_rows(substrate: Any) -> List[dict]:
    """Load historical trade rows from trade_learning for evaluation.

    Caches the data for the duration of the Optuna search so we don't
    hit the DB on every trial.
    """
    strategy_uid = substrate.strategy.get("uid", "legacy")

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

        # Convert sqlite3.Row to plain dicts for caching
        return [
            {
                "direction": row["direction"],
                "pnl_pct": row["pnl_pct"],
                "signals_at_entry_json": row["signals_at_entry_json"],
            }
            for row in rows
        ]

    except Exception as e:
        _log.error("Failed to load trade rows: %s", e, exc_info=True)
        return []


def _evaluate_from_cache(
    weights: Dict[str, float],
    trade_rows: List[dict],
    entry_threshold: float,
) -> Tuple[Optional[float], Optional[float]]:
    """Evaluate weights against pre-loaded trade rows (no DB access).

    Returns (profit_factor, sharpe_ratio) or (None, None) if no valid trades.
    """
    pnls = []

    for row in trade_rows:
        try:
            signals = json.loads(row["signals_at_entry_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(signals, dict) or not signals:
            continue

        direction = row["direction"] or "Long"
        pnl = row["pnl_pct"] if row["pnl_pct"] is not None else 0.0

        score = _compute_score_from_signals(signals, weights, direction)

        if abs(score) >= entry_threshold:
            pnls.append(pnl)

    if not pnls:
        return None, None

    pf = profit_factor(pnls)
    sr = sharpe_ratio(pnls)

    return pf, sr


def _log_search(
    substrate: Any,
    n_trials: int,
    baseline_pf: Optional[float],
    best_pf: Optional[float],
    candidates_pushed: int,
    search_space: Dict[str, float],
    best_weights: Optional[Dict[str, float]],
    duration_sec: float,
    reason: str,
) -> None:
    """Log a hyperopt search to the hyperopt_log table.

    Never raises — production must not be affected by logging failures.
    """
    try:
        strategy_uid = substrate.strategy.get("uid", "legacy")

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO hyperopt_log
                   (strategy_uid, n_trials, baseline_profit_factor,
                    best_profit_factor, candidates_pushed,
                    search_space_json, best_weights_json,
                    duration_seconds, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_uid,
                    n_trials,
                    baseline_pf,
                    best_pf,
                    candidates_pushed,
                    json.dumps(search_space) if search_space else None,
                    json.dumps(best_weights) if best_weights else None,
                    duration_sec,
                    reason,
                ),
            )
    except Exception as e:
        _log.error(
            "Failed to log hyperopt search: %s", e, exc_info=True,
        )