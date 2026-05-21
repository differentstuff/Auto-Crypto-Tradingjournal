"""
enzymes/approve_exit.py -- Regulator enzyme: RiskManager exit gate.

Decides whether to approve an exit request from RequestExit or any
other enzyme. Enforces:
  - Hard stop loss breach (immediate approval)
  - Trailing stop hit (immediate approval)
  - Max hold duration exceeded (approval)
  - Soft signal reversal (may deny if position is healthy)

Trailing stop state lives on each position dict:
  - trailing_active: bool (False until activation threshold reached)
  - trailing_sl: float or None (trailing stop price)
  - peak_price: float (highest/lowest mark since trailing activated)

Writes: decisions.exit_approved (dict or None)

Enzyme class: Regulator (priority 10)
Activates when: decisions.exit_request is set

Port of: agent_risk_mgmt.py, agent_trade_monitor.py (exit logic)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _update_trailing_stop(position: dict, config: dict) -> None:
    """
    Update trailing stop state on a position dict.

    Mutates position in place:
      - Updates peak_price if current mark exceeds it
      - Activates trailing if profit > activation_pct and not yet active
      - Moves trailing_sl according to trail distance when active
      - Moves SL to breakeven on activation if configured

    This is called by ApproveExit before evaluating exit requests,
    so trailing state is always up-to-date for the decision.
    """
    entry_price = position.get("entry_price", 0)
    mark_price = position.get("mark_price", 0)
    direction = position.get("direction", "Long").lower()
    atr_value = position.get("atr_value", 0)

    if not entry_price or not mark_price:
        return

    trailing_cfg = config.get("exit_rules", {}).get("trailing_stop", {})
    if not trailing_cfg.get("enabled", True):
        return

    activation_pct = trailing_cfg.get("activation_pct", 1.5)
    trail_atr_mult = trailing_cfg.get("trail_atr_multiplier", 1.0)
    breakeven_on_activate = trailing_cfg.get("breakeven_at_activation", True)

    # Compute current profit percentage
    if direction == "long":
        profit_pct = ((mark_price - entry_price) / entry_price) * 100
    else:
        profit_pct = ((entry_price - mark_price) / entry_price) * 100

    # Update peak price
    current_peak = position.get("peak_price", mark_price)
    if direction == "long":
        if mark_price > current_peak:
            position["peak_price"] = mark_price
    else:
        if mark_price < current_peak or current_peak == entry_price:
            if mark_price < current_peak:
                position["peak_price"] = mark_price

    # Activate trailing if not yet active and profit exceeds threshold
    if not position.get("trailing_active", False):
        if profit_pct >= activation_pct:
            position["trailing_active"] = True
            if breakeven_on_activate:
                position["trailing_sl"] = entry_price
            elif atr_value:
                if direction == "long":
                    position["trailing_sl"] = mark_price - atr_value * trail_atr_mult
                else:
                    position["trailing_sl"] = mark_price + atr_value * trail_atr_mult
            _log.info(
                "Trailing stop activated for %s at profit=%.2f%%",
                position.get("symbol", "?"), profit_pct,
            )
        return

    # Trailing is active — update trailing_sl
    if direction == "long":
        # For long: trailing_sl moves up, never down
        if atr_value:
            new_sl = mark_price - atr_value * trail_atr_mult
        else:
            new_sl = entry_price  # fallback to breakeven
        current_sl = position.get("trailing_sl")
        if current_sl is None or new_sl > current_sl:
            position["trailing_sl"] = new_sl
    else:
        # For short: trailing_sl moves down, never up
        if atr_value:
            new_sl = mark_price + atr_value * trail_atr_mult
        else:
            new_sl = entry_price
        current_sl = position.get("trailing_sl")
        if current_sl is None or new_sl < current_sl:
            position["trailing_sl"] = new_sl


@register_enzyme
class ApproveExit(Enzyme):
    """
    Regulator enzyme: RiskManager exit gate.

    Evaluates exit requests and decides whether to approve them.
    Only Regulator enzymes can approve exits — RequestExit and other
    enzymes can only request.

    Hard rules (always approve):
      - SL breach (mark_price crosses hard SL)
      - Trailing stop hit (mark_price crosses trailing_sl)
      - Max hold duration exceeded

    Soft rules (may approve or deny):
      - Signal reversal (depends on urgency and position health)
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
        target_pos = None
        for pos in positions:
            if pos.get("symbol") == symbol:
                target_pos = pos
                break

        if target_pos is None:
            self._log.warning("Exit request for %s but position not found", symbol)
            substrate.decisions["exit_approved"] = None
            return substrate

        # Update trailing stop state before evaluating
        _update_trailing_stop(target_pos, substrate._config)

        # Evaluate exit rules
        should_exit = False
        exit_reason = reason

        # 1. Hard SL breach — always approve
        entry_price = target_pos.get("entry_price", 0)
        sl_price = target_pos.get("sl_price", 0)
        mark_price = target_pos.get("mark_price", 0)
        direction = target_pos.get("direction", "Long").lower()

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

        # 3. Max hold duration — always approve
        max_hold_hours = substrate.cfg("exit_rules.max_hold_hours", 72)
        opened_at = target_pos.get("opened_at", "")
        if opened_at:
            try:
                opened_dt = datetime.fromisoformat(opened_at)
                held_hours = (datetime.now(timezone.utc) - opened_dt).total_seconds() / 3600
                if held_hours > max_hold_hours:
                    should_exit = True
                    exit_reason = "max_hold_exceeded"
            except (ValueError, TypeError):
                pass

        # 4. Soft signal reversal — approve based on urgency
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
                if profit_pct < 0.5:
                    should_exit = True
                    exit_reason = "signal_reversal_soft"

        if should_exit:
            substrate.decisions["exit_approved"] = {
                "symbol": symbol,
                "reason": exit_reason,
                "urgency": urgency,
            }
            self._log.info("Exit approved: %s reason=%s", symbol, exit_reason)
        else:
            substrate.decisions["exit_approved"] = None
            self._log.info("Exit denied for %s: no hard rule triggered", symbol)

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Regulators always have priority."""
        if self.can_activate(substrate):
            return 10.0
        return 0.0