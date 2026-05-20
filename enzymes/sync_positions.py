"""
enzymes/sync_positions.py -- Sensor enzyme: position synchronization.

Periodically reconciles portfolio state with the exchange:
  - Updates equity and available margin
  - Removes positions closed externally (manual close, liquidation)
  - Updates mark_price for open positions

In paper mode, uses fallback_equity_usdt from config and skips exchange calls.

Enzyme class: Sensor
Activates when: cycle_count % position_sync_every_n_cycles == 0
Writes to: portfolio.equity, portfolio.available_margin, portfolio.open_positions

Port of: bitget_sync.py, blofin_sync.py, sync_base.py
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
    Sensor enzyme: reconciles portfolio state with exchange.

    Activates every N cycles (config-driven: sync.position_sync_every_n_cycles).
    In paper mode, uses fallback_equity_usdt and skips all exchange calls.

    The Exchange instance must be injected via the constructor or by
    setting self.exchange before the enzyme runs. No getattr fallbacks.
    """

    name = "SyncPositions"
    enzyme_class = EnzymeClass.SENSOR
    priority = 0

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize SyncPositions.

        Args:
            config: Strategy config dict (same as all enzymes).
            exchange: core.exchange.Exchange instance for live mode.
                      In paper mode, this can be None.
        """
        super().__init__(config=config)
        self.exchange = exchange

    def can_activate(self, substrate: Substrate) -> bool:
        sync_every = substrate.cfg("sync.position_sync_every_n_cycles", 4)
        cycle = substrate._cycle_count
        return cycle % sync_every == 0

    def _fetch_exchange_data(self, substrate: Substrate) -> tuple[list, dict]:
        """
        Fetch positions and balance from exchange.

        Returns (positions_list, balance_dict).
        Requires self.exchange to be set (injected via __init__ or main.py).
        """
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot fetch live data")
            return [], {}

        try:
            positions = self.exchange.fetch_positions()
            balance = self.exchange.fetch_balance()
            return positions, balance
        except Exception as e:
            self._log.error("Exchange fetch failed: %s", e)
            return [], {}

    def transform(self, substrate: Substrate) -> Substrate:
        paper_mode = substrate.cfg("daemon.paper_mode", True)

        if paper_mode:
            # Paper mode: use fallback equity, no exchange calls
            fallback = substrate.cfg("portfolio.fallback_equity_usdt", 1000.0)
            substrate.portfolio["equity"] = fallback
            substrate.portfolio["available_margin"] = fallback
            self._log.info(
                "Paper sync: equity set to fallback %.2f USDT", fallback,
            )
            return substrate

        # Live mode: fetch from exchange
        try:
            exchange_positions, balance = self._fetch_exchange_data(substrate)

            # Update equity
            if balance:
                substrate.portfolio["equity"] = balance.get("equity", 0)
                substrate.portfolio["available_margin"] = balance.get("available", 0)

            # Reconcile positions
            current_positions = substrate.portfolio.get("open_positions", [])
            exchange_symbols = {p.get("symbol") for p in exchange_positions}

            # Remove positions no longer on exchange (closed externally)
            reconciled = []
            for pos in current_positions:
                symbol = pos.get("symbol", "")
                if symbol in exchange_symbols:
                    # Update mark_price from exchange data
                    for ex_pos in exchange_positions:
                        if ex_pos.get("symbol") == symbol:
                            pos["mark_price"] = ex_pos.get("mark_price", pos.get("mark_price"))
                            break
                    reconciled.append(pos)
                else:
                    self._log.info(
                        "Position %s removed: no longer on exchange (closed externally)",
                        symbol,
                    )

            substrate.portfolio["open_positions"] = reconciled

            self._log.info(
                "Sync complete: equity=%.2f, positions=%d",
                substrate.portfolio.get("equity", 0),
                len(reconciled),
            )

        except Exception as e:
            self._log.warning("Position sync failed: %s", e)

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        if self.can_activate(substrate):
            return 0.5  # Low priority — important but not urgent
        return 0.0