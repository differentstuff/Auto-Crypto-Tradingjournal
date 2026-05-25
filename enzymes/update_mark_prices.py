"""
enzymes/update_mark_prices.py -- Sensor enzyme: lightweight mark price updates.

Updates mark_price for open positions every cycle without doing a full
position sync. SyncPositions handles full reconciliation every N cycles;
this enzyme provides current prices for SL/TP/trailing-stop evaluation
in between full syncs.

In paper mode, uses the last close price from CollectOHLCV's indicator
data (substrate.market.last_prices) as a lightweight fallback.

Enzyme class: Sensor
Activates when: portfolio.open_positions not empty
Writes to: portfolio.open_positions[*].mark_price

Based on: Gap Analysis 2e (Mark-Price-Updates only every N cycles)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class UpdateMarkPrices(Enzyme):
    """
    Sensor enzyme: update mark prices for open positions.

    Provides current mark prices for SL/TP evaluation without the overhead
    of a full position sync. SyncPositions still runs every N cycles for
    reconciliation (equity, position changes, etc.).

    In paper mode, uses the last close price from CollectOHLCV as fallback.
    """

    name = "UpdateMarkPrices"
    enzyme_class = EnzymeClass.SENSOR
    priority = 6  # After CollectOHLCV (5), before evaluators

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize UpdateMarkPrices.

        Args:
            config: Strategy config dict.
            exchange: core.exchange.Exchange instance for live mode.
        """
        super().__init__(config=config)
        self.exchange = exchange

    def requires(self) -> list[str]:
        return ["portfolio.open_positions not empty"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Activate when positions exist and need current prices."""
        positions = substrate.portfolio.get("open_positions", [])
        return len(positions) > 0

    def transform(self, substrate: Substrate) -> Substrate:
        """Update mark prices for all open positions."""
        positions = substrate.portfolio.get("open_positions", [])
        if not positions:
            return substrate

        paper_mode = substrate.cfg("daemon.paper_mode", True)
        last_prices = substrate.market.get("last_prices", {})
        updated = 0

        for pos in positions:
            symbol = pos.get("symbol", "")

            if paper_mode or self.exchange is None:
                # Paper mode: use last close price from CollectOHLCV
                if symbol in last_prices:
                    new_price = last_prices[symbol]
                    old_price = pos.get("mark_price", 0)
                    if new_price != old_price:
                        pos["mark_price"] = new_price
                        updated += 1
                else:
                    _log.debug(
                        "No last_price for %s in paper mode — keeping stale price",
                        symbol,
                    )
            else:
                # Live mode: fetch current ticker price
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and ticker.get("last"):
                    new_price = ticker["last"]
                    old_price = pos.get("mark_price", 0)
                    if new_price != old_price:
                        pos["mark_price"] = new_price
                        updated += 1
                else:
                    # Fallback to last_prices if ticker fetch failed
                    if symbol in last_prices:
                        pos["mark_price"] = last_prices[symbol]
                        updated += 1
                    else:
                        _log.warning(
                            "No price update for %s — ticker failed and no fallback",
                            symbol,
                        )

        if updated > 0:
            self._log.info("Updated mark prices for %d/%d positions", updated, len(positions))
        else:
            self._log.debug("No mark price changes this cycle")

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when positions exist (current prices are critical for risk)."""
        if not self.can_activate(substrate):
            return 0.0
        positions = substrate.portfolio.get("open_positions", [])
        # More positions = higher urgency (more prices to update)
        if len(positions) >= 2:
            return 3.5
        return 3.0