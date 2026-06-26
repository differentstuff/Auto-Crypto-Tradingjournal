"""
enzymes/approve_exit.py -- Regulator enzyme: RiskManager exit gate.

Decides whether to approve an exit request from RequestExit or any
other enzyme. Enforces:
  - Hard stop loss breach (immediate approval)
  - Trailing stop hit (immediate approval)
  - Counter breakout (immediate approval — real reversal signal)
  - Soft signal reversal (may approve or deny based on urgency)

Partial exits (position retained, trailing stop protects remainder):
  - TP1 hit — sell tp1_sell_pct of total, trailing stop on remainder
  - TP2 hit — sell tp2_sell_pct of remaining (100 = close all)

Not exits (position retained, trailing stop protects):
  - Structure break — could be Elliott wave 2/4, trailing stop protects
  - Phase range — impulse may be forming, trailing stop protects

Trailing stop state lives on each position dict:
  - trailing_active: bool (False until activation threshold reached)
  - trailing_sl: float or None (trailing stop price)
  - peak_price: float (highest/lowest mark since trailing activated)

NOTE: Trailing stop updates are handled by core/trailing_stop.py, which
runs every cycle in the daemon's post-enzyme step. This enzyme does NOT
update trailing stops — it only evaluates exit requests using the current
trailing stop state (which is always up-to-date from the previous cycle's
maintenance).

Writes: decisions.exit_approved (dict or None)

Enzyme class: Regulator (priority 10)
Activates when: decisions.exit_request is set
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class ApproveExit(Enzyme):
    """
    Regulator enzyme: RiskManager exit gate.

    Evaluates exit requests and decides whether to approve them.
    Only Regulator enzymes can approve exits — RequestExit and other
    enzymes can only request.

    Hard rules (always approve — full close):
      - SL breach (mark_price crosses hard SL)
      - Trailing stop hit (mark_price crosses trailing_sl)
      - Counter breakout (real reversal — trend broke against position)

    Partial exits (position stays open, trailing stop on remainder):
      - TP1 hit — sell tp1_sell_pct of total, activate trailing stop
      - TP2 hit — sell tp2_sell_pct of remaining (100 = full close)

    Soft rules (may approve or deny):
      - Signal reversal (depends on urgency and position health)

    Not exits (position retained, trailing stop protects):
      - Structure break — could be Elliott wave 2/4 retracement within trend
      - Phase range — impulse may still be forming, trend may resume
    """

    name = "ApproveExit"
    enzyme_class = EnzymeClass.REGULATOR
    priority = 10

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        exit_request = substrate.decisions.get("exit_request")
        return exit_request is not None

    def transform(self, substrate: Substrate) -> Substrate:
        """Evaluate exit request and approve or deny."""
        exit_request = substrate.decisions.get("exit_request")
        if exit_request is None:
            return substrate

        symbol = exit_request.get("symbol", "")
        reason = exit_request.get("reason", "")
        urgency = exit_request.get("urgency", "normal")

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
            self._log.warning("Exit request for %s but position not found", symbol)
            substrate.decisions["exit_approved"] = None
            return substrate

        # NOTE: Trailing stop is updated by the daemon's post-enzyme step
        # (core/trailing_stop.maintain_trailing_stops), NOT here.
        # The trailing_sl on the position is always current from the
        # previous cycle's maintenance.

        # Evaluate exit rules using current position state
        should_exit = False
        exit_reason = reason

        entry_price = target_pos.get("entry_price", 0)
        sl_price = target_pos.get("sl_price", 0)
        mark_price = target_pos.get("mark_price", 0)
        direction = target_pos.get("direction", "Long").lower()

        # 1. Hard SL breach — always approve
        if sl_price and mark_price:
            if direction == "long" and mark_price <= sl_price:
                should_exit = True
                exit_reason = "hard_sl_breach"
            elif direction == "short" and mark_price >= sl_price:
                should_exit = True
                exit_reason = "hard_sl_breach"

        # 2. Trailing stop hit — always approve
        trailing_sl = target_pos.get("trailing_sl")
        trailing_active = target_pos.get("trailing_active", False)

        if trailing_active and trailing_sl and mark_price:
            if direction == "long" and mark_price <= trailing_sl:
                should_exit = True
                exit_reason = "trailing_stop_hit"
            elif direction == "short" and mark_price >= trailing_sl:
                should_exit = True
                exit_reason = "trailing_stop_hit"

        # 3. Counter breakout — always approve (real reversal signal)
        #    The market is actively breaking against the position direction.
        #    This is not a pullback — it's a reversal.
        if not should_exit and reason == "counter_breakout":
            should_exit = True
            exit_reason = "counter_breakout"

        # 4. Soft signal reversal — approve based on urgency and position health
        if not should_exit and "signal_reversal" in reason.lower():
            if urgency == "immediate":
                should_exit = True
                exit_reason = "signal_reversal_immediate"
            elif urgency in ("normal", "high"):
                # Approve if position is in loss or barely profitable
                if direction == "long":
                    profit_pct = ((mark_price - entry_price) / entry_price) * 100 if entry_price else 0
                else:
                    profit_pct = ((entry_price - mark_price) / entry_price) * 100 if entry_price else 0
                soft_threshold = substrate.cfg("exit_rules.soft_reversal_profit_threshold")
                if profit_pct < soft_threshold:
                    should_exit = True
                    exit_reason = "signal_reversal_soft"

        # 5. TP1 hit — partial exit. Sell tp1_sell_pct of total position,
        #    trailing stop already active on remainder (daemon maintains it).
        #    Position stays open with reduced size.
        #    Exchange-as-truth: if position was reconciled from exchange,
        #    prefer tp1_taken flag (derived from achievedProfits > 0).
        if not should_exit and reason == "tp1_hit":
            tp1_taken_from_exchange = target_pos.get("tp1_taken", False)
            if tp1_taken_from_exchange and not paper_mode:
                self._log.info(
                    "TP1 already taken on exchange for %s (achievedProfits > 0) — re-approval for substrate cleanup",
                    symbol,
                )
            tp1_sell_pct = substrate.cfg("exit_rules.tp1_sell_pct", 40.0)
            if tp1_sell_pct >= 100.0:
                should_exit = True
                exit_reason = "tp1_full_close"
            else:
                substrate.decisions["exit_approved"] = {
                    "symbol": symbol,
                    "reason": "tp1_partial",
                    "urgency": urgency,
                    "partial": True,
                    "sell_pct": tp1_sell_pct,
                }
                self._log.info(
                    "TP1 partial exit approved: %s sell=%.1f%% — trailing stop active on remainder",
                    symbol, tp1_sell_pct,
                )
                return substrate

        # 6. TP2 hit — partial exit on REMAINING position. Sell tp2_sell_pct
        #    of what's left. If tp2_sell_pct >= 100, close everything.
        if not should_exit and reason == "tp2_hit":
            tp2_sell_pct = substrate.cfg("exit_rules.tp2_sell_pct", 40.0)
            if tp2_sell_pct >= 100.0:
                should_exit = True
                exit_reason = "tp2_full_close"
            else:
                substrate.decisions["exit_approved"] = {
                    "symbol": symbol,
                    "reason": "tp2_partial",
                    "urgency": urgency,
                    "partial": True,
                    "sell_pct": tp2_sell_pct,
                }
                self._log.info(
                    "TP2 partial exit approved: %s sell=%.1f%% of remaining",
                    symbol, tp2_sell_pct,
                )
                return substrate

        # 7. Structure break — not an exit. Could be Elliott wave 2 or 4
        #    (temporary retracement within the trend). The trailing stop
        #    (maintained by the daemon) protects the position.
        #    If the trend truly broke, the trailing stop will close it.
        if not should_exit and reason == "structure_break":
            _log.info(
                "Structure break for %s — position retained "
                "(could be wave 2/4, trailing stop protects downside)",
                symbol,
            )

        # 8. Phase range — not an exit. The impulse may still be forming.
        #    Trailing stop (maintained by the daemon) protects the position.
        if not should_exit and reason == "phase_range":
            _log.info(
                "Phase range for %s — position retained "
                "(impulse may be forming, trailing stop protects downside)",
                symbol,
            )

        if should_exit:
            substrate.decisions["exit_approved"] = {
                "symbol": symbol,
                "reason": exit_reason,
                "urgency": urgency,
            }
            self._log.info("Exit approved: %s reason=%s", symbol, exit_reason)
        else:
            substrate.decisions["exit_approved"] = None

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Regulators always have priority."""
        if self.can_activate(substrate):
            return 10.0
        return 0.0
