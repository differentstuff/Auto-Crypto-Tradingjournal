"""
learning/challenger.py -- Weight Challenger and CandidateQueue.

Manages the challenger paper-trading branch that validates weight
adjustments before they reach production. The challenger runs as a
parallel branch in the daemon — same cycle, same market data, but
strictly separated from production decisions.

CandidateQueue:
    Unified interface for all candidate sources (weight_adjuster,
    Karpathy, Hyperopt). Candidates are queued and evaluated one
    at a time by the active challenger.

WeightChallenger:
    Stores challenger weights in substrate.learning["challenger"],
    handles promotion (replace production weights) and discard
    (clear challenger), and logs every event to challenger_log.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

_MAX_QUEUE_SIZE = 10


class CandidateQueue:
    """FIFO queue of weight candidates awaiting challenger evaluation.

    Candidates come from weight_adjuster, Karpathy, or Hyperopt.
    Only one challenger is active at a time; the queue holds pending
    candidates for sequential evaluation after the current challenger
    is resolved (promoted or discarded).
    """

    @staticmethod
    def push(
        weights: Dict[str, float],
        source: str,
        substrate: Any,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Push a candidate to the queue in substrate.learning.challenger.candidate_queue.

        If the queue is full, the oldest candidate is evicted (FIFO).
        """
        challenger = substrate.learning.setdefault("challenger", {})
        queue = challenger.setdefault("candidate_queue", [])

        entry = {
            "weights": weights,
            "source": source,
            "metadata": metadata or {},
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        queue.append(entry)

        if len(queue) > _MAX_QUEUE_SIZE:
            evicted = queue.pop(0)
            _log.warning(
                "CandidateQueue full (%d) — evicted oldest from source '%s'",
                _MAX_QUEUE_SIZE, evicted.get("source", "?"),
            )

        challenger["candidate_queue"] = queue
        _log.info("CandidateQueue: pushed candidate from '%s' (queue depth: %d)", source, len(queue))

        _log_challenger_event(substrate, "candidate_queued", source=source, challenger_weights=weights)

    @staticmethod
    def pop_next(substrate: Any) -> Optional[Dict]:
        """Pop the next candidate from the queue. Returns None if empty."""
        challenger = substrate.learning.get("challenger", {})
        queue = challenger.get("candidate_queue", [])
        if not queue:
            return None
        entry = queue.pop(0)
        challenger["candidate_queue"] = queue
        _log.info("CandidateQueue: popped candidate from '%s'", entry.get("source", "?"))
        return entry


class WeightChallenger:
    """Manages the active challenger: activation, promotion, and discard.

    Challenger weights are stored in substrate.learning["challenger"]["weights"].
    They are NEVER used for production trades until explicitly promoted.
    Every promotion/discard is logged to the challenger_log DB table.
    """

    @staticmethod
    def activate_next_candidate(substrate: Any) -> bool:
        """Activate the next candidate from the queue as the active challenger.

        Returns True if a challenger was activated, False if the queue was empty.
        """
        entry = CandidateQueue.pop_next(substrate)
        if entry is None:
            return False

        challenger = substrate.learning.setdefault("challenger", {})
        challenger["weights"] = entry["weights"]
        challenger["source"] = entry["source"]
        challenger["created_at"] = datetime.now(timezone.utc).isoformat()
        challenger["trade_count"] = 0
        challenger["positions"] = challenger.get("positions", [])

        _log.info(
            "Challenger activated: source='%s', weights=%s",
            entry["source"], list(entry["weights"].keys()),
        )
        _log_challenger_event(
            substrate, "activated",
            source=entry["source"],
            challenger_weights=entry["weights"],
        )
        return True

    @staticmethod
    def create_from_weights(
        weights: Dict[str, float],
        source: str,
        substrate: Any,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Create a new challenger directly (replaces any existing one).

        Used by weight_adjuster when it produces new weights. If an
        active challenger already exists, it is replaced — only one
        challenger at a time.
        """
        challenger = substrate.learning.setdefault("challenger", {})

        old_weights = challenger.get("weights")
        if old_weights:
            _log.info("Replacing existing challenger with new one from '%s'", source)

        challenger["weights"] = weights
        challenger["source"] = source
        challenger["created_at"] = datetime.now(timezone.utc).isoformat()
        challenger["trade_count"] = 0
        challenger["positions"] = []

        _log.info(
            "Challenger created: source='%s', weights=%s",
            source, list(weights.keys()),
        )
        _log_challenger_event(
            substrate, "activated",
            source=source,
            challenger_weights=weights,
            metadata=metadata,
        )

    @staticmethod
    def promote(substrate: Any, reason: str, metrics: Optional[Dict] = None) -> None:
        """Promote challenger weights to production.

        Replaces substrate.learning["adjusted_weights"] with the challenger
        weights, clears the active challenger, and logs the promotion.
        """
        challenger = substrate.learning.get("challenger", {})
        challenger_weights = challenger.get("weights")
        if not challenger_weights:
            _log.warning("Promote called but no active challenger exists")
            return

        current_weights = substrate.learning.get("adjusted_weights", {})

        # Replace production weights
        substrate.learning["adjusted_weights"] = dict(challenger_weights)

        metrics = metrics or {}
        _log_challenger_event(
            substrate, "promoted",
            source=challenger.get("source", ""),
            challenger_weights=challenger_weights,
            current_weights=current_weights,
            reason=reason,
            promoted=True,
            production_profit_factor=metrics.get("production_profit_factor"),
            challenger_profit_factor=metrics.get("challenger_profit_factor"),
            trade_count=challenger.get("trade_count", 0),
        )

        # Clear active challenger
        challenger["weights"] = None
        challenger["source"] = ""
        challenger["created_at"] = ""
        challenger["trade_count"] = 0
        challenger["positions"] = []

        _log.info("Challenger PROMOTED: %s", reason)

    @staticmethod
    def discard(substrate: Any, reason: str, metrics: Optional[Dict] = None) -> None:
        """Discard the active challenger without promoting.

        Clears the active challenger and logs the discard.
        """
        challenger = substrate.learning.get("challenger", {})
        challenger_weights = challenger.get("weights")
        if not challenger_weights:
            _log.warning("Discard called but no active challenger exists")
            return

        current_weights = substrate.learning.get("adjusted_weights", {})

        metrics = metrics or {}
        _log_challenger_event(
            substrate, "discarded",
            source=challenger.get("source", ""),
            challenger_weights=challenger_weights,
            current_weights=current_weights,
            reason=reason,
            promoted=False,
            production_profit_factor=metrics.get("production_profit_factor"),
            challenger_profit_factor=metrics.get("challenger_profit_factor"),
            trade_count=challenger.get("trade_count", 0),
        )

        # Clear active challenger
        challenger["weights"] = None
        challenger["source"] = ""
        challenger["created_at"] = ""
        challenger["trade_count"] = 0
        challenger["positions"] = []

        _log.info("Challenger DISCARDED: %s", reason)


def _log_challenger_event(
    substrate: Any,
    event_type: str,
    source: str = "",
    challenger_weights: Optional[Dict] = None,
    current_weights: Optional[Dict] = None,
    reason: str = "",
    promoted: bool = False,
    production_profit_factor: Optional[float] = None,
    challenger_profit_factor: Optional[float] = None,
    trade_count: int = 0,
    symbol: str = "",
    entry_score: Optional[float] = None,
    exit_pnl_pct: Optional[float] = None,
    exit_reason: str = "",
    signal_states: Optional[Dict] = None,
    metadata: Optional[Dict] = None,
) -> None:
    """Write an event to the challenger_log table.

    All DB writes use the standard db_conn() context manager.
    Failures are logged but never raise — production must not be affected.
    """
    try:
        from core.database import db_conn

        strategy_uid = substrate.strategy.get("uid", "legacy")

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO challenger_log
                   (strategy_uid, event_type, source, challenger_weights_json,
                    current_weights_json, reason, production_profit_factor,
                    challenger_profit_factor, promoted, trade_count, symbol,
                    entry_score, exit_pnl_pct, exit_reason, signal_states_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_uid,
                    event_type,
                    source,
                    json.dumps(challenger_weights) if challenger_weights else None,
                    json.dumps(current_weights) if current_weights else None,
                    reason,
                    production_profit_factor,
                    challenger_profit_factor,
                    int(promoted),
                    trade_count,
                    symbol or None,
                    entry_score,
                    exit_pnl_pct,
                    exit_reason or None,
                    json.dumps(signal_states) if signal_states else None,
                ),
            )
    except Exception as e:
        _log.error("Failed to log challenger event '%s': %s", event_type, e, exc_info=True)