"""
enzymes/execute_exit.py -- Transporter enzyme: position closing.

Closes positions by removing them from the portfolio.
In paper mode, updates portfolio without calling exchange APIs.
In live mode, calls exchange via core/exchange.py.

Exchange-as-truth architecture:
  - On full close: cancel remaining exchange orders, then close position
  - On partial close: update position size in substrate
  - PnL is applied to portfolio.equity for paper trading (net of fees)
  - In live mode, equity comes from exchange.fetch_balance(), not computed PnL

DB recording of trade outcomes is handled by RecordTradeOutcome (Synthase),
which runs after this enzyme in the pipeline.

Fee simulation (paper/replay only):
  - Exit fee is deducted from gross PnL before applying to equity.
  - Entry fee was already deducted at trade open (execute_trade.py).
  - For partial closes, exit fee uses the sold portion's notional only.
  - For full closes, exit fee uses the remaining position's notional.
  - Live mode: no fee deduction (fees baked into broker fills).

Enzyme class: Transporter
Activates when: decisions.exit_approved is set
Writes to: portfolio.open_positions (removes position), decisions.action = 'trade_closed'
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate
from core.fees import compute_exit_fee

_log = logging.getLogger(__name__)


@register_enzyme
class ExecuteExit(Enzyme):
    """
    Transporter enzyme: closes approved positions.

    In paper mode: removes position from portfolio, records outcome to DB.
    In live mode: cancels exchange orders, closes position on exchange, records outcome to DB.

    Handles gracefully when the position is already gone (closed externally).
    """

    name = "ExecuteExit"
    enzyme_class = EnzymeClass.TRANSPORTER
    priority = 0

    def __init__(self, config: Optional[dict] = None, exchange=None):
        super().__init__(config=config)
        self.exchange = exchange

    def can_activate(self, substrate: Substrate) -> bool:
        exit_approved = substrate.decisions.get("exit_approved")
        return exit_approved is not None

    def transform(self, substrate: Substrate) -> Substrate:
        exit_approved = substrate.decisions.get("exit_approved")
        if exit_approved is None:
            return substrate

        symbol = exit_approved.get("symbol", "?")
        exit_reason = exit_approved.get("reason", "unknown")
        paper_mode = substrate.cfg("daemon.paper_mode")
        is_partial = exit_approved.get("partial", False)
        sell_pct = exit_approved.get("sell_pct", 0.0)

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

        # Compute PnL for the portion being sold
        entry_price = target_pos.get("entry_price", 0)
        mark_price = target_pos.get("mark_price", 0)
        direction = target_pos.get("direction", "Long").lower()
        full_size_usdt = target_pos.get("size_usdt", 0)

        # Fix 1: SL/trailing fills use stop price, not mark_price.
        if exit_reason == "hard_sl_breach":
            exit_price = target_pos.get("sl_price", mark_price)
        elif exit_reason == "trailing_stop_hit":
            exit_price = target_pos.get("trailing_sl", mark_price)
        else:
            exit_price = mark_price

        pnl_pct = 0.0
        if entry_price and exit_price:
            if direction == "long":
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
            else:
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100

        fee_rate = substrate.cfg("fees.taker_rate") if paper_mode else 0.0

        if is_partial:
            # -- Partial close: sell a % of the position, keep the rest --
            sold_usdt = full_size_usdt * (sell_pct / 100.0)
            remaining_usdt = round(full_size_usdt - sold_usdt, 2)
            gross_pnl_usdt = sold_usdt * pnl_pct / 100

            exit_fee = compute_exit_fee(sold_usdt + gross_pnl_usdt, fee_rate) if paper_mode else 0.0
            net_pnl_usdt = gross_pnl_usdt - exit_fee

            # Apply net PnL from sold portion to equity
            current_equity = substrate.portfolio.get("equity", 0)
            if current_equity > 0:
                substrate.portfolio["equity"] = round(current_equity + net_pnl_usdt, 2)
                self._log.info(
                    "Equity updated: %.2f → %.2f (partial PnL: %+.2f USDT gross=%.4f net=%.4f fee=%.4f)",
                    current_equity, substrate.portfolio["equity"], net_pnl_usdt,
                    gross_pnl_usdt, net_pnl_usdt, exit_fee,
                )

            # Write PnL back to exit_approved for outcome recording
            exit_approved["pnl_pct"] = round(pnl_pct, 4)
            exit_approved["gross_pnl_usdt"] = round(gross_pnl_usdt, 4)
            exit_approved["net_pnl_usdt"] = round(net_pnl_usdt, 4)
            exit_approved["exit_price"] = exit_price
            exit_approved["sold_usdt"] = round(sold_usdt, 2)
            exit_approved["remaining_usdt"] = remaining_usdt
            substrate.decisions["exit_approved"] = exit_approved

            # Update position: reduce size, mark TP as taken
            updated_pos = {**target_pos, "size_usdt": remaining_usdt}
            if exit_reason == "tp1_partial":
                updated_pos["tp1_taken"] = True

                # Activate native trailing stop on exchange after TP1
                if not paper_mode:
                    self._activate_native_trailing_stop(substrate, updated_pos)

            elif exit_reason == "tp2_partial":
                updated_pos["tp2_taken"] = True

            if paper_mode:
                self._log.info(
                    "PAPER PARTIAL CLOSE: %s %s reason=%s sold=%.2f USDT (%.1f%%) "
                    "remaining=%.2f USDT pnl=%.2f%% gross=%.4f net=%.4f fee=%.4f",
                    direction, symbol, exit_reason, sold_usdt, sell_pct,
                    remaining_usdt, pnl_pct, gross_pnl_usdt, net_pnl_usdt, exit_fee,
                )
            else:
                self._log.info(
                    "LIVE PARTIAL CLOSE: %s %s reason=%s sold=%.2f USDT (%.1f%%) "
                    "remaining=%.2f USDT pnl=%.2f%%",
                    direction, symbol, exit_reason, sold_usdt, sell_pct,
                    remaining_usdt, pnl_pct,
                )

            # Reassign position in list (shallow-copy safe)
            updated_positions = list(positions)
            updated_positions[target_idx] = updated_pos
            substrate.portfolio["open_positions"] = updated_positions

            # Partial close is NOT 'trade_closed' — position still open
            substrate.decisions["action"] = "trade_managed"

            self._log.info(
                "Partial close: %s — reason=%s, sold=%.1f%%, remaining=%.2f USDT",
                symbol, exit_reason, sell_pct, remaining_usdt,
            )

            return substrate

        else:
            # -- Full close: remove position entirely --
            gross_pnl_usdt = full_size_usdt * pnl_pct / 100

            exit_fee = compute_exit_fee(full_size_usdt + gross_pnl_usdt, fee_rate) if paper_mode else 0.0
            net_pnl_usdt = gross_pnl_usdt - exit_fee

            # Apply net PnL to portfolio equity
            current_equity = substrate.portfolio.get("equity", 0)
            if current_equity > 0:
                substrate.portfolio["equity"] = round(current_equity + net_pnl_usdt, 2)
                self._log.info(
                    "Equity updated: %.2f → %.2f (PnL: %+.2f USDT gross=%.4f net=%.4f fee=%.4f)",
                    current_equity, substrate.portfolio["equity"], net_pnl_usdt,
                    gross_pnl_usdt, net_pnl_usdt, exit_fee,
                )

            # Write PnL back to exit_approved
            exit_approved["pnl_pct"] = round(pnl_pct, 4)
            exit_approved["gross_pnl_usdt"] = round(gross_pnl_usdt, 4)
            exit_approved["net_pnl_usdt"] = round(net_pnl_usdt, 4)
            exit_approved["exit_price"] = exit_price
            substrate.decisions["exit_approved"] = exit_approved

            # Cancel exchange orders and close position (live mode)
            if not paper_mode:
                self._cancel_exchange_orders(substrate, target_pos)
                self._close_position_on_exchange(substrate, target_pos)

            if paper_mode:
                self._log.info(
                    "PAPER CLOSE: %s %s reason=%s pnl=%.2f%% gross=%.4f net=%.4f fee=%.4f",
                    target_pos.get("direction", "?"), symbol, exit_reason,
                    pnl_pct, gross_pnl_usdt, net_pnl_usdt, exit_fee,
                )
            else:
                self._log.info(
                    "LIVE CLOSE: %s %s reason=%s pnl=%.2f%%",
                    target_pos.get("direction", "?"), symbol, exit_reason,
                    pnl_pct,
                )

            # Remove position from portfolio (shallow-copy safe)
            substrate.portfolio["open_positions"] = [
                p for i, p in enumerate(positions) if i != target_idx
            ]

            # Fix 2: Record position close for re-entry guard.
            primary_tf = substrate.strategy.get("timeframe", "4H")
            candle_key = f"{symbol}_{primary_tf}"
            last_close_ts = substrate.market.get("last_candle_close_ts", {}).get(candle_key, "")

            rc = dict(substrate.market.get("recently_closed", {}))
            rc[symbol] = last_close_ts
            substrate.market["recently_closed"] = rc

            ltci = dict(substrate.market.get("last_traded_candle_idx", {}))
            ltci[symbol] = last_close_ts
            substrate.market["last_traded_candle_idx"] = ltci

            # Mark position as closed in metadata DB
            self._mark_position_closed(substrate, target_pos)

            # Update decisions
            substrate.decisions["action"] = "trade_closed"

            self._log.info(
                "Position closed: %s — reason=%s, pnl=%.2f%%",
                symbol, exit_reason, pnl_pct,
            )

            return substrate

    def _cancel_exchange_orders(self, substrate: Substrate, position: dict) -> None:
        """Cancel remaining exchange orders for a position being closed."""
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot cancel orders for %s", position.get("symbol", "?"))
            return

        symbol = position.get("symbol", "")
        try:
            self.exchange.cancel_orders(symbol)
            self._log.info("Cancelled exchange orders for %s", symbol)
        except Exception as e:
            self._log.warning("Could not cancel exchange orders for %s: %s", symbol, e)

    def _close_position_on_exchange(self, substrate: Substrate, position: dict) -> None:
        """Close position on the exchange in live mode."""
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot close position on exchange for %s", position.get("symbol", "?"))
            return

        symbol = position.get("symbol", "")
        direction = position.get("direction", "Long")
        size_usdt = position.get("size_usdt", 0)

        try:
            result = self.exchange.close_position(
                symbol=symbol,
                direction=direction,
                size_usdt=size_usdt,
                reduce_only=False,
            )
            if result:
                self._log.info(
                    "Position closed on exchange: %s %s size=%.2f order_id=%s",
                    direction, symbol, size_usdt, result.get("order_id", "?"),
                )
            else:
                self._log.warning("Exchange close_position returned None for %s", symbol)
        except Exception as e:
            self._log.warning("Could not close position on exchange for %s: %s", symbol, e)

    def _activate_native_trailing_stop(self, substrate: Substrate, position: dict) -> None:
        """
        Activate native trailing stop on exchange after TP1 hit.

        The native trail is a daemon-offline backup — wider than ATR-based stop.
        Percentage = (atr_multiplier × ATR / current_price) × 100
        Fallback: configurable default from strategy YAML.
        """
        if self.exchange is None:
            self._log.warning("No Exchange instance — cannot place native trailing stop for %s", position.get("symbol", ""))
            return

        symbol = position.get("symbol", "")
        direction = position.get("direction", "Long")
        atr_value = position.get("atr_value", 0)
        mark_price = position.get("mark_price", 0)
        tp1 = position.get("tp1", 0)

        atr_multiplier = substrate.cfg("exit_rules.native_trail.atr_multiplier", 2.0)

        if atr_value and mark_price:
            trail_pct = (atr_multiplier * atr_value / mark_price) * 100
        else:
            trail_pct = substrate.cfg("exit_rules.native_trail.default_pct", 5.0)

        trigger_price = tp1 if tp1 else mark_price

        try:
            result = self.exchange.place_trailing_stop(
                symbol=symbol,
                direction=direction,
                trigger_price=trigger_price,
                trail_pct=trail_pct,
            )
            if result:
                position["native_trail_order_id"] = result.get("order_id", "")
                self._log.info(
                    "Native trailing stop placed on exchange: %s trail_pct=%.2f%% trigger=%.2f order_id=%s",
                    symbol, trail_pct, trigger_price, result.get("order_id", "?"),
                )
            else:
                self._log.warning("Native trailing stop placement returned None for %s", symbol)
        except Exception as e:
            self._log.warning("Could not place native trailing stop for %s: %s", symbol, e)

    def _mark_position_closed(self, substrate: Substrate, position: dict) -> None:
        """Mark position as closed in the position_metadata DB table."""
        try:
            from core.database import db_conn
            strategy_uid = substrate.strategy.get("uid", "legacy")
            symbol = position.get("symbol", "")
            direction = position.get("direction", "")
            entry_price = position.get("entry_price", 0)

            with db_conn() as conn:
                conn.execute(
                    """UPDATE position_metadata SET closed_at = datetime('now')
                       WHERE strategy_uid = ? AND symbol = ? AND direction = ?
                       AND entry_price = ? AND closed_at IS NULL""",
                    (strategy_uid, symbol, direction, entry_price),
                )
        except Exception as e:
            _log.debug("Could not mark position closed in metadata DB: %s", e)

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: high when exit is approved — close position promptly."""
        if self.can_activate(substrate):
            return 5.0
        return 0.0
