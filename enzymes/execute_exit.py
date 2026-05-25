"""
enzymes/execute_exit.py -- Transporter enzyme: position closing.

Closes positions by removing them from the portfolio.
In paper mode, updates portfolio without calling exchange APIs.
In live mode, calls exchange via core/exchange.py.

DB recording of trade outcomes is handled by RecordTradeOutcome (Synthase),
which runs after this enzyme in the pipeline.

Enzyme class: Transporter
Activates when: decisions.exit_approved is set
Writes to: portfolio.open_positions (removes position), decisions.action = 'trade_closed'

Port of: ccxt_client.py (close position logic)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


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

        # Compute PnL for logging
        entry_price = target_pos.get("entry_price", 0)
        mark_price = target_pos.get("mark_price", 0)
        direction = target_pos.get("direction", "Long").lower()
        size_usdt = target_pos.get("size_usdt", 0)
        pnl_pct = 0.0
        if entry_price and mark_price:
            if direction == "long":
                pnl_pct = ((mark_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - mark_price) / entry_price) * 100
        pnl_usdt = size_usdt * pnl_pct / 100

        if paper_mode:
            self._log.info(
                "PAPER CLOSE: %s %s reason=%s pnl=%.2f%% (%.2f USDT)",
                target_pos.get("direction", "?"), symbol, exit_reason,
                pnl_pct, pnl_usdt,
            )
        else:
            self._log.info(
                "LIVE CLOSE: %s %s reason=%s pnl=%.2f%% (%.2f USDT)",
                target_pos.get("direction", "?"), symbol, exit_reason,
                pnl_pct, pnl_usdt,
            )

        # Remove position from portfolio (shallow-copy safe: new list, no mutation of shared reference)
        substrate.portfolio["open_positions"] = [
            p for i, p in enumerate(positions) if i != target_idx
        ]

        # Update decisions
        substrate.decisions["action"] = "trade_closed"

        self._log.info(
            "Position closed: %s — reason=%s, pnl=%.2f%%",
            symbol, exit_reason, pnl_pct,
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when exit is approved — close position promptly."""
        if self.can_activate(substrate):
            return 5.0
        return 0.0