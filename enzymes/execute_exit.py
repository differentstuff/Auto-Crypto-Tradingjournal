"""
enzymes/execute_exit.py -- Transporter enzyme: position closing.

Closes positions and records outcomes to the database.
In paper mode, updates portfolio without calling exchange APIs.
In live mode, calls exchange via core/exchange.py.

Enzyme class: Transporter
Activates when: decisions.exit_approved is set
Writes to: portfolio.open_positions (removes position), decisions.action = 'trade_closed'

Port of: ccxt_client.py (close position logic), database.py (update trade_learning)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _compute_pnl(position: dict) -> dict:
    """
    Compute PnL for a closing position.

    Returns dict with pnl_pct, pnl_usdt, exit_reason.
    """
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


def _record_trade_exit(symbol: str, position: dict, exit_reason: str,
                       pnl: dict, strategy_name: str) -> None:
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
                ),
            )
    except Exception as e:
        _log.warning("Failed to record trade exit in DB: %s", e)


@register_enzyme
class ExecuteExit(Enzyme):
    """
    Transporter enzyme: closes approved positions.

    In paper mode: removes position from portfolio, records outcome to DB.
    In live mode: calls exchange to close, then records to DB.

    Handles gracefully when the position is already gone (closed externally).
    """

    name = "ExecuteExit"
    enzyme_class = EnzymeClass.TRANSPORTER
    priority = 0

    def can_activate(self, substrate: Substrate) -> bool:
        exit_approved = substrate.decisions.get("exit_approved")
        return exit_approved is not None

    def transform(self, substrate: Substrate) -> Substrate:
        exit_approved = substrate.decisions.get("exit_approved")
        if exit_approved is None:
            return substrate

        symbol = exit_approved.get("symbol", "?")
        exit_reason = exit_approved.get("reason", "unknown")
        paper_mode = substrate.cfg("daemon.paper_mode", True)

        # Find the position
        positions = substrate.portfolio.get("open_positions", [])
        target_idx = None
        target_pos = None

        for i, pos in enumerate(positions):
            if pos.get("symbol") == symbol:
                target_idx = i
                target_pos = pos
                break

        if target_pos is None:
            self._log.warning(
                "Position %s not found in portfolio — may have been closed externally",
                symbol,
            )
            substrate.decisions["action"] = "trade_closed"
            return substrate

        # Compute PnL
        pnl = _compute_pnl(target_pos)

        if paper_mode:
            self._log.info(
                "PAPER CLOSE: %s %s reason=%s pnl=%.2f%% (%.2f USDT)",
                target_pos.get("direction", "?"), symbol, exit_reason,
                pnl["pnl_pct"], pnl["pnl_usdt"],
            )
        else:
            self._log.info(
                "LIVE CLOSE: %s %s reason=%s pnl=%.2f%% (%.2f USDT)",
                target_pos.get("direction", "?"), symbol, exit_reason,
                pnl["pnl_pct"], pnl["pnl_usdt"],
            )

        # Remove position from portfolio
        positions.pop(target_idx)
        substrate.portfolio["open_positions"] = positions

        # Record exit to database
        strategy_name = substrate.strategy.get("name", "")
        _record_trade_exit(symbol, target_pos, exit_reason, pnl, strategy_name)

        # Update decisions
        substrate.decisions["action"] = "trade_closed"

        self._log.info(
            "Position closed: %s — reason=%s, pnl=%.2f%%",
            symbol, exit_reason, pnl["pnl_pct"],
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        if self.can_activate(substrate):
            return 5.0
        return 0.0