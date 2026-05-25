"""
enzymes/execute_trade.py -- Transporter enzyme: execute approved trades.

Places orders (paper or live) and records them to the database.
In paper mode: adds position to portfolio.open_positions, sets action = 'trade_open'.
In live mode: calls exchange API, then records to database.

Writes: portfolio.open_positions (adds position), decisions.action = 'trade_open'

Enzyme class: Transporter
Activates when: decisions.trade_approved is set AND action != 'trade_open'

Port of: bitget_client.py (order functions), database.py (insert)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class ExecuteTrade(Enzyme):
    """
    Transporter enzyme: execute approved trades.

    In paper mode:
      - Adds position to portfolio.open_positions
      - Sets decisions.action = 'trade_open'

    In live mode:
      - Calls exchange API via core.exchange.Exchange
      - Sets decisions.action = 'trade_open'

    The Exchange instance must be injected via the constructor or
    by setting self.exchange before the enzyme runs.
    """

    name = "ExecuteTrade"
    enzyme_class = EnzymeClass.TRANSPORTER
    priority = 0

    def __init__(self, config: Optional[dict] = None, exchange=None):
        """
        Initialize ExecuteTrade.

        Args:
            config: Strategy config dict (same as all enzymes).
            exchange: core.exchange.Exchange instance for live mode.
        """
        super().__init__(config=config)
        self.exchange = exchange

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        trade_approved = substrate.decisions.get("trade_approved")
        action = substrate.decisions.get("action", "wait")
        # Activate when trade is approved and not yet executed
        return trade_approved is not None and action != "trade_open"

    def transform(self, substrate: Substrate) -> Substrate:
        """Execute the approved trade."""
        trade_approved = substrate.decisions.get("trade_approved")
        if trade_approved is None:
            return substrate

        symbol = trade_approved.get("symbol", "")
        direction = trade_approved.get("direction", "")
        entry_price = trade_approved.get("entry_price", 0)
        sl_price = trade_approved.get("sl_price", 0)
        tp1 = trade_approved.get("tp1", 0)
        tp2 = trade_approved.get("tp2", 0)
        size_usdt = trade_approved.get("size_usdt", 0)
        atr_value = trade_approved.get("atr_value", 0)
        paper_mode = substrate.cfg("daemon.paper_mode", True)
        leverage = substrate.cfg("portfolio.leverage", 5)

        if paper_mode:
            self._log.info(
                "PAPER ENTRY: %s %s entry=%.2f sl=%.2f tp1=%.2f size=%.2f",
                direction, symbol, entry_price, sl_price, tp1, size_usdt,
            )
        else:
            # Live mode: call exchange
            if self.exchange is not None:
                result = self.exchange.place_order(
                    symbol=symbol,
                    direction=direction,
                    size_usdt=size_usdt,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp1,
                    leverage=leverage,
                )
                if result is None:
                    self._log.error("Live order failed for %s — skipping", symbol)
                    substrate.decisions["trade_approved"] = None
                    return substrate
                self._log.info(
                    "LIVE ENTRY: %s %s order_id=%s",
                    direction, symbol, result.get("order_id", "?"),
                )
            else:
                self._log.warning("No Exchange instance — cannot place live order")

        # Add position to portfolio
        now_iso = datetime.now(timezone.utc).isoformat()
        new_position = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "mark_price": entry_price,  # Mark at entry initially
            "sl_price": sl_price,
            "tp1": tp1,
            "tp2": tp2,
            "size_usdt": size_usdt,
            "atr_value": atr_value,
            "opened_at": now_iso,
            # Trailing stop state — always present on every position
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": entry_price,
        }

        # Add position to portfolio (shallow-copy safe: new list, no mutation of shared reference)
        current_positions = substrate.portfolio.get("open_positions", [])
        substrate.portfolio["open_positions"] = current_positions + [new_position]

        # Update decisions
        substrate.decisions["action"] = "trade_open"

        self._log.info(
            "Trade executed: %s %s size=%.2f action=%s",
            direction, symbol, size_usdt, "paper" if paper_mode else "live",
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when trade is approved — execute promptly."""
        if self.can_activate(substrate):
            return 5.0
        return 0.0