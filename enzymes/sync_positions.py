"""
enzymes/sync_positions.py -- Sensor enzyme: position synchronization.

Exchange-as-truth architecture:
  - LIVE mode: reconciliation is handled by daemon.reconcile_from_exchange()
    which runs every cycle. This enzyme still fetches balance data.
  - PAPER mode: preserves rolling equity (updated by ExecuteExit PnL)
    and falls back to fallback_equity_usdt only on first cycle or reset.
  - REPLAY mode: no exchange calls.

The reconciliation logic (rebuilding positions from exchange data) has been
moved to daemon.reconcile_from_exchange() to centralize it and ensure it
runs at the right point in the cycle (after all enzymes have fired).

Enzyme class: Sensor
Activates when: cycle_count % position_sync_every_n_cycles == 0
Writes to: portfolio.equity, portfolio.available_margin
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class SyncPositions(Enzyme):
    """
    Sensor enzyme: syncs balance from exchange.

    In live mode: fetches balance and triggers reconciliation
    (handled by daemon.reconcile_from_exchange() after this enzyme runs).
    In paper mode: preserves rolling equity.

    The Exchange instance must be injected via the constructor or by
    setting self.exchange before the enzyme runs.
    """

    name = "SyncPositions"
    enzyme_class = EnzymeClass.SENSOR
    priority = 0

    def __init__(self, config: Optional[dict] = None, exchange=None):
        super().__init__(config=config)
        self.exchange = exchange

    def can_activate(self, substrate: Substrate) -> bool:
        sync_every = substrate.cfg("sync.position_sync_every_n_cycles")
        cycle = substrate._cycle_count
        return cycle % sync_every == 0

    def transform(self, substrate: Substrate) -> Substrate:
        paper_mode = substrate.cfg("daemon.paper_mode")

        if paper_mode:
            # Paper mode: preserve rolling equity (updated by ExecuteExit PnL),
            # fall back to configured value only on first cycle or reset.
            fallback = substrate.cfg("portfolio.fallback_equity_usdt")
            current_equity = substrate.portfolio.get("equity", 0)
            if current_equity <= 0:
                substrate.portfolio["equity"] = fallback
                self._log.info(
                    "Paper sync: equity initialized to fallback %.2f USDT", fallback,
                )
            else:
                self._log.info(
                    "Paper sync: preserving rolling equity %.2f USDT", current_equity,
                )
            substrate.portfolio["available_margin"] = substrate.portfolio["equity"]
            return substrate

        # Live mode: fetch balance from exchange
        # Position reconciliation is handled by daemon.reconcile_from_exchange()
        # which runs after all enzymes have fired in the cycle.
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot fetch live data")
            return substrate

        try:
            balance = self.exchange.fetch_balance()
            if balance:
                substrate.portfolio["equity"] = balance.get("equity", 0)
                substrate.portfolio["available_margin"] = balance.get("available", 0)
                self._log.info(
                    "Live sync: equity=%.2f, available=%.2f",
                    balance.get("equity", 0), balance.get("available", 0),
                )
        except Exception as e:
            self._log.warning("Balance sync failed: %s", e)

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """
        Dynamic flux: high when positions exist but haven't been synced,
        low when no positions or already synced this cycle.
        """
        if not self.can_activate(substrate):
            return 0.0
        # High urgency if we have open positions — need current mark prices
        positions = substrate.portfolio.get("open_positions", [])
        if positions:
            return 3.0
        # No positions — just equity check, lower priority
        return 0.5
