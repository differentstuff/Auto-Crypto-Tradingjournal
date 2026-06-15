"""
enzymes/approve_exit.py -- Regulator enzyme: RiskManager exit gate.

Decides whether to approve an exit request from RequestExit or any
other enzyme. Enforces:
  - Hard stop loss breach (immediate approval)
  - Trailing stop hit (immediate approval)
  - Soft signal reversal (may approve or deny based on urgency)

Trailing stop state lives on each position dict:
  - trailing_active: bool (False until activation threshold reached)
  - trailing_sl: float or None (trailing stop price)
  - peak_price: float (highest/lowest mark since trailing activated)

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


def _lookup_trail_schedule(schedule: dict, profit_atr: float) -> float:
    """
    Look up the trail distance (in ATR multiples) for a given profit level.

    The schedule is a dict of {profit_atr_level: trail_distance_atr}.
    Finds the highest level that the profit has reached.

    Example schedule: {0: 1.0, 1: 0.75, 2: 0.5, 3: 0.25}
    If max_profit_atr = 2.3, returns 0.5 (level 2 reached).

    Returns: trail distance in ATR multiples
    """
    if not schedule:
        return 1.0  # default: 1x ATR

    # Find the highest level reached
    best_level = 0
    best_distance = schedule.get(0, 1.0)
    for level_str, distance in schedule.items():
        level = float(level_str)
        if profit_atr >= level and level >= best_level:
            best_level = level
            best_distance = distance

    return best_distance


def _update_trailing_stop(position: dict, substrate: Substrate) -> dict:
    """
    Compute updated trailing stop state for a position.

    Returns a NEW position dict with updated trailing stop fields.
    Does NOT mutate the original position dict (shallow-copy safe).

    Reads all config via substrate.cfg() — no raw dict access.

    Supports two modes:
      1. Progressive trailing stop (exit_rules.progressive_trail: true):
         Trail tightens as profit grows, with structure-aware adjustments.
      2. Standard ATR trailing stop (default, backward compatible):
         Fixed ATR multiplier trail, same as before.

    Updates:
      - peak_price if current mark exceeds it
      - trailing_active and trailing_sl when profit exceeds activation threshold
      - max_profit_atr: highest profit level reached (for progressive trail)
      - Moves SL to breakeven on activation if configured
    """
    entry_price = position.get("entry_price", 0)
    mark_price = position.get("mark_price", 0)
    direction = position.get("direction", "Long").lower()
    atr_value = position.get("atr_value", 0)
    symbol = position.get("symbol", "")

    if not entry_price or not mark_price:
        return position

    trailing_enabled = substrate.cfg("exit_rules.trailing_stop.enabled", False)
    progressive_trail_enabled = substrate.cfg("exit_rules.progressive_trail", False)

    if not trailing_enabled and not progressive_trail_enabled:
        return position

    activation_pct = substrate.cfg("exit_rules.trailing_stop.activation_profit_pct", 0.5)
    breakeven_on_activate = substrate.cfg("exit_rules.trailing_stop.breakeven_at_activation", True)

    # Compute current profit percentage
    if direction == "long":
        profit_pct = ((mark_price - entry_price) / entry_price) * 100
    else:
        profit_pct = ((entry_price - mark_price) / entry_price) * 100

    # Start with current state (no mutation of original)
    trailing_active = position.get("trailing_active", False)
    trailing_sl = position.get("trailing_sl")
    peak_price = position.get("peak_price", mark_price)
    max_profit_atr = position.get("max_profit_atr", 0.0)

    # Update peak price
    if direction == "long":
        if mark_price > peak_price:
            peak_price = mark_price
    else:  # Short
        if mark_price < peak_price:
            peak_price = mark_price

    # ── Progressive trailing stop ──────────────────────────────────────
    if progressive_trail_enabled and atr_value:
        # Compute profit in ATR units
        if direction == "long":
            profit_atr = (mark_price - entry_price) / atr_value
        else:
            profit_atr = (entry_price - mark_price) / atr_value

        # Track the highest profit level reached (trail only tightens, never widens)
        max_profit_atr = max(max_profit_atr, profit_atr)

        # Activate trailing if profit exceeds activation threshold
        if not trailing_active:
            if profit_pct >= activation_pct:
                trailing_active = True
                # Look up initial trail distance from schedule
                schedule = substrate.cfg("exit_rules.progressive_trail_schedule", {0: 1.0})
                trail_distance_atr = _lookup_trail_schedule(schedule, max_profit_atr)

                # Apply structure-aware tightening
                trail_distance_atr = _apply_structure_tightening(
                    trail_distance_atr, symbol, substrate
                )

                if breakeven_on_activate:
                    trailing_sl = entry_price
                else:
                    if direction == "long":
                        trailing_sl = mark_price - trail_distance_atr * atr_value
                    else:
                        trailing_sl = mark_price + trail_distance_atr * atr_value

                _log.info(
                    "Progressive trail activated for %s at profit=%.2f%% (%.1f ATR), trail=%.2f ATR",
                    symbol, profit_pct, profit_atr, trail_distance_atr,
                )
                return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl,
                        "peak_price": peak_price, "max_profit_atr": max_profit_atr}
            return {**position, "peak_price": peak_price, "max_profit_atr": max_profit_atr}

        # Trailing is active — compute progressive trail distance
        schedule = substrate.cfg("exit_rules.progressive_trail_schedule", {0: 1.0})
        trail_distance_atr = _lookup_trail_schedule(schedule, max_profit_atr)

        # Apply structure-aware tightening
        trail_distance_atr = _apply_structure_tightening(
            trail_distance_atr, symbol, substrate
        )

        # Compute new trailing SL
        if direction == "long":
            new_sl = mark_price - trail_distance_atr * atr_value
            # Trail only moves up, never down
            if trailing_sl is None or new_sl > trailing_sl:
                trailing_sl = new_sl
        else:
            new_sl = mark_price + trail_distance_atr * atr_value
            # Trail only moves down, never up
            if trailing_sl is None or new_sl < trailing_sl:
                trailing_sl = new_sl

        return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl,
                "peak_price": peak_price, "max_profit_atr": max_profit_atr}

    # ── Standard ATR trailing stop (backward compatible) ───────────────
    trail_atr_mult = substrate.cfg("exit_rules.trailing_stop.trail_atr_multiplier", 1.0)

    # Apply structure-aware stop adjustment for deep pullback
    effective_mult = trail_atr_mult
    structure_aware_exits = substrate.cfg("exit_rules.structure_aware_exits", False)
    if structure_aware_exits and atr_value:
        geometry_data = substrate.market.get("geometry", {})
        geometry = geometry_data.get(symbol, {})
        if geometry:
            pullback_depth = geometry.get("pullback_depth", "n/a")
            if pullback_depth == "deep":
                deep_mult = substrate.cfg("exit_rules.deep_pullback_stop_multiplier", 0.5)
                effective_mult = deep_mult
                _log.info(
                    "Deep pullback detected for %s — tightening stop to %.2f ATR",
                    symbol, effective_mult,
                )

    # Activate trailing if not yet active and profit exceeds threshold
    if not trailing_active:
        if profit_pct >= activation_pct:
            trailing_active = True
            if breakeven_on_activate:
                trailing_sl = entry_price
            elif atr_value:
                if direction == "long":
                    trailing_sl = mark_price - atr_value * effective_mult
                else:
                    trailing_sl = mark_price + atr_value * effective_mult
            _log.info(
                "Trailing stop activated for %s at profit=%.2f%%",
                position.get("symbol", "?"), profit_pct,
            )
            return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl, "peak_price": peak_price}
        return {**position, "peak_price": peak_price}

    # Trailing is active — update trailing_sl
    if direction == "long":
        # For long: trailing_sl moves up, never down
        if atr_value:
            new_sl = mark_price - atr_value * effective_mult
        else:
            new_sl = entry_price  # fallback to breakeven
        if trailing_sl is None or new_sl > trailing_sl:
            trailing_sl = new_sl
    else:
        # For short: trailing_sl moves down, never up
        if atr_value:
            new_sl = mark_price + atr_value * effective_mult
        else:
            new_sl = entry_price
        if trailing_sl is None or new_sl < trailing_sl:
            trailing_sl = new_sl

    return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl, "peak_price": peak_price}


def _apply_structure_tightening(
    trail_distance_atr: float, symbol: str, substrate: Substrate
) -> float:
    """
    Apply structure-aware tightening to the progressive trail distance.

    Reads substrate.market.geometry[symbol] and adjusts the trail:
      - phase == "pullback" → tighten by pullback_trail_tighten factor
      - pullback_depth == "deep" → tighten further by deep_pullback_trail_tighten factor

    These tightenings are multiplicative and compound.
    Structure break and phase=range are handled by RequestExit (immediate exit),
    not by trail adjustment.

    Returns: adjusted trail_distance_atr
    """
    structure_aware_exits = substrate.cfg("exit_rules.structure_aware_exits", False)
    if not structure_aware_exits:
        return trail_distance_atr

    geometry_data = substrate.market.get("geometry", {})
    geometry = geometry_data.get(symbol, {})
    if not geometry:
        return trail_distance_atr

    # Tighten one level when phase shifts to pullback
    if geometry.get("phase") == "pullback":
        tighten_factor = substrate.cfg("exit_rules.pullback_trail_tighten", 0.75)
        trail_distance_atr *= tighten_factor
        _log.debug(
            "Pullback trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, tighten_factor, trail_distance_atr,
        )

    # Tighten two levels when pullback is deep
    if geometry.get("pullback_depth") == "deep":
        deep_tighten = substrate.cfg("exit_rules.deep_pullback_trail_tighten", 0.5)
        trail_distance_atr *= deep_tighten
        _log.debug(
            "Deep pullback trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, deep_tighten, trail_distance_atr,
        )

    return trail_distance_atr


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

        # Update trailing stop state before evaluating (returns new dict, no mutation)
        updated_pos = _update_trailing_stop(target_pos, substrate)

        # Evaluate exit rules
        should_exit = False
        exit_reason = reason

        # Use updated position for evaluation (trailing stop may have changed)
        entry_price = updated_pos.get("entry_price", 0)
        sl_price = updated_pos.get("sl_price", 0)
        mark_price = updated_pos.get("mark_price", 0)
        direction = updated_pos.get("direction", "Long").lower()

        # 1. Hard SL breach — always approve
        if sl_price and mark_price:
            if direction == "long" and mark_price <= sl_price:
                should_exit = True
                exit_reason = "hard_sl_breach"
            elif direction == "short" and mark_price >= sl_price:
                should_exit = True
                exit_reason = "hard_sl_breach"

        # 2. Trailing stop hit — always approve
        trailing_sl = updated_pos.get("trailing_sl")
        trailing_active = updated_pos.get("trailing_active", False)

        if trailing_active and trailing_sl and mark_price:
            if direction == "long" and mark_price <= trailing_sl:
                should_exit = True
                exit_reason = "trailing_stop_hit"
            elif direction == "short" and mark_price >= trailing_sl:
                should_exit = True
                exit_reason = "trailing_stop_hit"

        # 3. Soft signal reversal — approve based on urgency
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

        # Reassign open_positions with updated position (shallow-copy safe)
        if target_idx is not None:
            updated_positions = list(positions)
            updated_positions[target_idx] = updated_pos
            substrate.portfolio["open_positions"] = updated_positions

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