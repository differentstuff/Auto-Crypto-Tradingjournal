"""
enzymes/record_trade_outcome.py -- Synthase enzyme: record trades to learning DB.

Records trade entries and exits to the trade_learning table.
This is a Synthase concern (building learning data from substrate state),
not a Transporter concern (moving data between systems).

Activates when:
  - decisions.action == 'trade_open' → records entry
  - decisions.action == 'trade_closed' → records exit outcome

Runs AFTER ExecuteTrade/ExecuteExit (lower priority = runs later in pipeline).

Writes to: trade_learning table (side-effect only, no substrate changes)

Enzyme class: Synthase
Priority: -1 (runs after all Transporters at priority 0)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _record_trade_entry(trade_approved: dict, strategy_name: str,
                        strategy_uid: str = "legacy",
                        signal_states: dict = None,
                        trajectory_data: dict = None,
                        indicator_data: dict = None) -> None:
    """
    Record a new trade entry in the trade_learning table.

    Called in both paper and live modes so the learning engine
    always has data to work with.

    Args:
        trade_approved: Dict with trade details (symbol, direction, score, etc.)
        strategy_name: Strategy name for DB grouping.
        strategy_uid: Stable strategy UID for learning data isolation.
        signal_states: Dict of {symbol: label} from substrate.analysis.signal_states.
        trajectory_data: Dict of trajectory info from substrate.market.pre_trade_context.
        indicator_data: Dict of {symbol: {tf: {indicator: result}}} from substrate.market.indicators.
        indicator_configs: List of indicator config dicts from strategy (for threshold extraction).
    """
    try:
        from core.database import db_conn
        import json as _json

        # Build signals_at_entry_json from per-indicator signal data
        # This provides the learning engine with precise, objective data about
        # what each indicator was showing at trade entry time — not just a
        # subjective label, but actual values and directional signals.
        #
        # Format: {rsi: {signal: "bullish", value: 65.3}, macd: {signal: "bullish", bias: "bullish_growing"}, ...}
        # This is what the learning engine needs to determine which indicators
        # were actually correct predictors of the trade outcome.
        signals_json = ""
        if trade_approved:
            symbol = trade_approved.get("symbol", "")
            timeframe = trade_approved.get("timeframe", "")
            # Extract per-indicator signals from current market data
            # indicator_data is substrate.market.indicators — the full indicator dict
            indicator_signals = {}
            if indicator_data:
                indicator_signals = _extract_indicator_signals(
                    indicator_data, symbol, timeframe,
                )
            # Also include the confluence label for backward compatibility
            if signal_states and symbol:
                label = signal_states.get(symbol, "")
                if label:
                    indicator_signals["_confluence_label"] = label
            if indicator_signals:
                signals_json = _json.dumps(indicator_signals)

        # Extract trajectory data from pre_trade_context
        # This provides the learning engine with trajectory pattern and coincidence risk
        trajectory_pattern = ""
        coincidence_risk = ""
        if trajectory_data and trade_approved:
            symbol = trade_approved.get("symbol", "")
            traj = trajectory_data.get(symbol, {})
            if isinstance(traj, dict):
                trajectory_pattern = traj.get("trajectory_type", "")
                coincidence_risk = traj.get("coincidence_risk", "")

        with db_conn() as conn:
            conn.execute(
                """INSERT INTO trade_learning
                   (strategy_name, strategy_uid, symbol, direction, entry_time,
                    confluence_score_at_entry, signals_at_entry_json,
                    pre_trade_trajectory_pattern, pre_trade_coincidence_risk)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    strategy_name,
                    strategy_uid,
                    trade_approved.get("symbol", ""),
                    trade_approved.get("direction", ""),
                    _now_iso(),
                    trade_approved.get("score", 0),
                    signals_json,
                    trajectory_pattern,
                    coincidence_risk,
                ),
            )
    except Exception as e:
        _log.warning("Failed to record trade entry in DB: %s", e)


def _record_trade_exit(symbol: str, position: dict, exit_reason: str,
                       pnl: dict, strategy_name: str,
                       strategy_uid: str = "legacy") -> None:
    """
    Update trade_learning table with exit data.

    Uses a subquery to find the most recent open trade for this symbol,
    because SQLite does not support ORDER BY / LIMIT in UPDATE statements.
    """
    try:
        from core.database import db_conn

        with db_conn() as conn:
            # SQLite-safe: subquery finds the single row to update
            conn.execute(
                """UPDATE trade_learning
                   SET exit_time = ?,
                       outcome = ?,
                       pnl_pct = ?,
                       pnl_usdt = ?,
                       exit_reason = ?,
                       sl_hit = ?,
                       trailing_stop_hit = ?
                   WHERE id = (
                       SELECT id FROM trade_learning
                       WHERE symbol = ?
                         AND exit_time IS NULL
                         AND strategy_name = ?
                         AND strategy_uid = ?
                       ORDER BY entry_time DESC
                       LIMIT 1
                   )""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    "win" if pnl["pnl_usdt"] >= 0 else "loss",
                    pnl["pnl_pct"],
                    pnl["pnl_usdt"],
                    exit_reason,
                    1 if "sl" in exit_reason.lower() else 0,
                    1 if "trailing" in exit_reason.lower() else 0,
                    symbol,
                    strategy_name,
                    strategy_uid,
                ),
            )
    except Exception as e:
        _log.warning("Failed to record trade exit in DB: %s", e)


def _compute_pnl(position: dict) -> dict:
    """Compute PnL for a closing position."""
    entry_price = position.get("entry_price", 0)
    mark_price = position.get("mark_price", 0)
    direction = position.get("direction", "Long").lower()
    size_usdt = position.get("size_usdt", 0)

    if not entry_price or not mark_price or not size_usdt:
        return {"pnl_pct": 0.0, "pnl_usdt": 0.0}

    if direction == "long":
        pnl_pct = ((mark_price - entry_price) / entry_price) * 100
    else:
        pnl_pct = ((entry_price - mark_price) / entry_price) * 100

    pnl_usdt = size_usdt * pnl_pct / 100

    return {
        "pnl_pct": round(pnl_pct, 2),
        "pnl_usdt": round(pnl_usdt, 2),
    }


@register_enzyme
class RecordTradeOutcome(Enzyme):
    """
    Synthase enzyme: record trade entries and exits to the learning database.

    This enzyme separates the learning/recording concern from the
    execution concern. ExecuteTrade and ExecuteExit handle order
    placement and portfolio updates; RecordTradeOutcome handles
    database recording for the learning engine.

    Activates when:
      - action == 'trade_open' AND trade_approved is set (entry recording)
      - action == 'trade_closed' AND exit_approved is set (exit recording)

    Does NOT modify the substrate — side-effect only (DB writes).
    """

    name = "RecordTradeOutcome"
    enzyme_class = EnzymeClass.SYNTHASE
    priority = -1  # Runs after Transporters (priority 0)

    def requires(self) -> list[str]:
        return ["decisions.action in ('trade_open', 'trade_closed')"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        action = substrate.decisions.get("action", "wait")
        return action in ("trade_open", "trade_closed")

    def transform(self, substrate: Substrate) -> Substrate:
        """Record trade entry or exit to the learning database."""
        action = substrate.decisions.get("action", "wait")
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")

        if action == "trade_open":
            trade_approved = substrate.decisions.get("trade_approved")
            if trade_approved:
                # Pass signal states and trajectory data for learning
                signal_states = substrate.analysis.get("signal_states", {})
                trajectory_data = substrate.market.get("pre_trade_context", {})
                _record_trade_entry(
                    trade_approved, strategy_name, strategy_uid,
                    signal_states=signal_states,
                    trajectory_data=trajectory_data,
                )
                self._log.info(
                    "Recorded trade entry: %s %s",
                    trade_approved.get("direction", "?"),
                    trade_approved.get("symbol", "?"),
                )

        elif action == "trade_closed":
            exit_approved = substrate.decisions.get("exit_approved")
            if exit_approved:
                symbol = exit_approved.get("symbol", "?")
                exit_reason = exit_approved.get("reason", "unknown")

                # Find the position that was just closed
                # (it may already be removed from open_positions by ExecuteExit,
                #  so we compute PnL from exit_approved data if available)
                pnl = {"pnl_pct": 0.0, "pnl_usdt": 0.0}

                # Try to find position in portfolio (may still be there briefly)
                for pos in substrate.portfolio.get("open_positions", []):
                    if pos.get("symbol") == symbol:
                        pnl = _compute_pnl(pos)
                        break

                # Override with exit_approved PnL if available
                if "pnl_pct" in exit_approved:
                    pnl = {
                        "pnl_pct": exit_approved.get("pnl_pct", 0.0),
                        "pnl_usdt": exit_approved.get("pnl_usdt", 0.0),
                    }

                _record_trade_exit(symbol, exit_approved, exit_reason, pnl,
                                   strategy_name, strategy_uid)
                self._log.info(
                    "Recorded trade exit: %s reason=%s pnl=%.2f%%",
                    symbol, exit_reason, pnl["pnl_pct"],
                )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """
        Dynamic flux: high when a trade just happened (must record),
        low otherwise.
        """
        if not self.can_activate(substrate):
            return 0.0
        action = substrate.decisions.get("action", "wait")
        # Trade just opened or closed — record immediately
        if action == "trade_open":
            return 4.0
        if action == "trade_closed":
            return 4.0
        return 0.5
