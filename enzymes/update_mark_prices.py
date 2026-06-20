"""
enzymes/update_mark_prices.py -- Sensor enzyme: lightweight mark price updates.

Updates mark_price for open positions every cycle without doing a full
position sync. SyncPositions handles full reconciliation every N cycles;
this enzyme provides current prices for SL/TP/trailing-stop evaluation
in between full syncs.

NEVER uses stale or imaginary data. In BOTH paper and live mode, fetches
REAL market prices from the exchange. Paper mode differs ONLY in trade
execution — market data is always real. If the primary exchange fails,
tries the fallback exchange. If both fail, prices are NOT updated
(stale data is unacceptable).

Uses fetch_tickers() for bulk fetching when possible (one API call for
all symbols), falling back to individual fetch_ticker() calls if needed.

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

    In BOTH paper and live mode, fetches REAL market prices from the exchange.
    Paper mode differs ONLY in trade execution — market data is always real.
    If the primary exchange fails, tries the fallback exchange.
    If both fail, prices are NOT updated (stale data is unacceptable).
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
        """Update mark prices for all open positions using REAL market data.

        Creates new position dicts with updated prices and reassigns the
        entire open_positions list (shallow-copy safe: no nested mutation).
        """
        positions = substrate.portfolio.get("open_positions", [])
        if not positions:
            return substrate

        if self.exchange is None:
            _log.error("No Exchange instance — cannot update mark prices. "
                        "Prices will remain stale. This is a configuration error.")
            return substrate

        # Collect unique symbols for bulk fetch
        symbols = list(set(pos.get("symbol", "") for pos in positions if pos.get("symbol")))

        # Bulk fetch all ticker prices in one API call (or fallback to individual)
        tickers = self.exchange.fetch_tickers(symbols) if symbols else {}

        updated = 0
        failed = 0
        failed_symbols = set()
        updated_positions = []

        for pos in positions:
            symbol = pos.get("symbol", "")

            if symbol in tickers and tickers[symbol].get("last"):
                # Real price from bulk fetch — create new position dict
                new_pos = {**pos, "mark_price": tickers[symbol]["last"]}
                updated_positions.append(new_pos)
                updated += 1
            else:
                # Individual fallback for symbols that bulk fetch missed
                ticker = self.exchange.fetch_ticker(symbol)
                if ticker and ticker.get("last"):
                    new_pos = {**pos, "mark_price": ticker["last"]}
                    updated_positions.append(new_pos)
                    updated += 1
                else:
                    # No price available — keep position unchanged
                    updated_positions.append(pos)
                    failed += 1
                    failed_symbols.add(symbol)

        if failed_symbols:
            _log.warning(
                "No real price for %d symbol(s): %s — price remains stale. "
                "RequestExit will not activate until fresh data arrives.",
                len(failed_symbols), ", ".join(sorted(failed_symbols)),
            )

        # Reassign entire list (shallow-copy safe)
        substrate.portfolio["open_positions"] = updated_positions

        if updated > 0:
            self._log.info("Updated mark prices for %d/%d positions", updated, len(positions))
        if failed > 0:
            self._log.warning("Failed to update %d/%d positions (no real price available)", failed, len(positions))

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