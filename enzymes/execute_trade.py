"""
enzymes/execute_trade.py -- Transporter enzyme: execute approved trades.

Places orders (paper or live) and records them to the database.
In paper mode: adds position to portfolio.open_positions, sets action = 'trade_open'.
In live mode: calls exchange API with preset SL/TP, then records to database.

Exchange-as-truth architecture:
  - SL + TP2 are pushed to exchange via presetStopLossPrice/presetStopSurplusPrice
  - TP1 partial order is placed via place_tpsl_order (40% of position)
  - Exchange order IDs are stored in the position dict for modify-tpsl-order
  - atr_pct is stored for TP1/TP2 recalculation after daemon restart
  - Position metadata is saved to position_metadata DB table

Writes: portfolio.open_positions (adds position), decisions.action = 'trade_open'

Enzyme class: Transporter
Activates when: decisions.trade_approved is set AND action != 'trade_open'
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate
from core.fees import compute_entry_fee

_log = logging.getLogger(__name__)


@register_enzyme
class ExecuteTrade(Enzyme):
    """
    Transporter enzyme: execute approved trades.

    In paper mode:
      - Adds position to portfolio.open_positions
      - Sets decisions.action = 'trade_open'

    In live mode:
      - Calls exchange API with preset SL/TP
      - Places TP1 partial order via place_tpsl_order
      - Sets decisions.action = 'trade_open'

    The Exchange instance must be injected via the constructor or
    by setting self.exchange before the enzyme runs.
    """

    name = "ExecuteTrade"
    enzyme_class = EnzymeClass.TRANSPORTER
    priority = 0

    def __init__(self, config: Optional[dict] = None, exchange=None):
        super().__init__(config=config)
        self.exchange = exchange

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        trade_approved = substrate.decisions.get("trade_approved")
        action = substrate.decisions.get("action", "wait")
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
        atr_pct = trade_approved.get("atr_pct", 0)
        paper_mode = substrate.cfg("daemon.paper_mode")
        leverage = substrate.cfg("portfolio.leverage")

        # Exchange order IDs (populated in live mode)
        sl_order_id = ""
        tp1_order_id = ""
        tp2_order_id = ""

        if paper_mode:
            self._log.info(
                "PAPER ENTRY: %s %s entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f size=%.2f atr_pct=%.4f",
                direction, symbol, entry_price, sl_price, tp1, tp2, size_usdt, atr_pct,
            )
            entry_fee = compute_entry_fee(size_usdt, substrate.cfg("fees.taker_rate"))
            substrate.portfolio["equity"] = round(
                substrate.portfolio.get("equity", 0) - entry_fee, 2
            )
            self._log.info(
                "Entry fee deducted: %.4f USDT (rate=%.4f notional=%.2f)",
                entry_fee, substrate.cfg("fees.taker_rate"), size_usdt,
            )
            trade_approved["entry_fee_usdt"] = entry_fee
        else:
            # Live mode: place order with preset SL/TP
            if self.exchange is not None:
                # Step 1: Place main order with preset SL + TP2
                # SL and TP2 are set on the order itself (single API call)
                result = self.exchange.place_order(
                    symbol=symbol,
                    direction=direction,
                    size_usdt=size_usdt,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    tp_price=tp2,  # TP2 is the full exit target
                    leverage=leverage,
                )
                if result is None:
                    self._log.error("Live order failed for %s — skipping", symbol)
                    substrate.decisions["trade_approved"] = None
                    return substrate

                self._log.info(
                    "LIVE ENTRY: %s %s order_id=%s sl=%.2f tp2=%.2f",
                    direction, symbol, result.get("order_id", "?"), sl_price, tp2,
                )

                # Step 2: Place TP1 partial order (40% of position)
                if tp1:
                    tp1_result = self.exchange.place_tpsl_order(
                        symbol=symbol,
                        direction=direction,
                        trigger_price=tp1,
                        size_pct=substrate.cfg("exit_rules.tp1_sell_pct", 40.0),
                        size_usdt=size_usdt,
                        order_type="tp",
                        reduce_only=True,
                    )
                    if tp1_result:
                        tp1_order_id = tp1_result.get("order_id", "")
                        self._log.info(
                            "TP1 partial order placed: %s tp1=%.2f order_id=%s",
                            symbol, tp1, tp1_order_id,
                        )
                    else:
                        self._log.error(
                            "TP1 partial order FAILED for %s — position open but TP1 not on exchange",
                            symbol,
                        )
            else:
                self._log.warning("No Exchange instance — cannot place live order")

        # Add position to portfolio
        now_iso = substrate.now_iso()
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
            "atr_pct": atr_pct,  # Store for TP recalculation after restart
            "opened_at": now_iso,
            # Trailing stop state — always present on every position
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": entry_price,
            # Partial exit tracking — each TP can only fire once
            "tp1_taken": False,
            "tp2_taken": False,
            # Exchange order IDs (for modify-tpsl-order and reconciliation)
            "pos_id": "",  # Populated by reconcile_from_exchange on next cycle
            "sl_order_id": sl_order_id,
            "tp1_order_id": tp1_order_id,
            "tp2_order_id": tp2_order_id,
            "native_trail_order_id": "",
            "max_profit_atr": 0.0,
        }

        # Add position to portfolio (shallow-copy safe: new list, no mutation of shared reference)
        current_positions = substrate.portfolio.get("open_positions", [])
        substrate.portfolio["open_positions"] = current_positions + [new_position]

        # Update decisions
        substrate.decisions["action"] = "trade_open"

        self._log.info(
            "Trade executed: %s %s size=%.2f action=%s atr_pct=%.4f",
            direction, symbol, size_usdt, "paper" if paper_mode else "live", atr_pct,
        )

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when trade is approved — execute promptly."""
        if self.can_activate(substrate):
            return 5.0
        return 0.0
