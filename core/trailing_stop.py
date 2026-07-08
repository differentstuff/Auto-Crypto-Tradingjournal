"""
core/trailing_stop.py -- Per-cycle trailing stop maintenance.

Updates trailing stop state for ALL open positions every cycle,
independently of exit requests. This ensures trailing stops continue
to track price even after partial exits (TP1/TP2) when no exit_request
exists and ApproveExit would not fire.

Called by the daemon at the END of each cycle, after all enzymes have
fired and mark prices are current.

Supports two modes:
  1. Progressive trailing stop (exit_rules.progressive_trail: true):
     Trail tightens as profit grows, with structure-aware adjustments.
  2. Standard ATR trailing stop (default, backward compatible):
     Fixed ATR multiplier trail.

Structure-aware tightening (structure_aware_exits: true):
  - phase == "pullback" → tighten trail
  - pullback_depth == "deep" → tighten further
  - structure_break == True → tighten significantly (trend compromised, not an exit)
  - phase == "range" → tighten moderately (momentum fading, not an exit)

Based on: approve_exit.py _update_trailing_stop (extracted for per-cycle use)
"""

from __future__ import annotations

import logging

from core.substrate import Substrate

_log = logging.getLogger(__name__)


def lookup_trail_schedule(schedule: dict, profit_atr: float) -> float:
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

    best_level = 0
    best_distance = schedule.get(0, 1.0)
    for level_str, distance in schedule.items():
        level = float(level_str)
        if profit_atr >= level and level >= best_level:
            best_level = level
            best_distance = distance

    return best_distance


def apply_structure_tightening(
    trail_distance_atr: float, symbol: str, substrate: Substrate
) -> float:
    """
    Apply structure-aware tightening to the progressive trail distance.

    Reads substrate.market.geometry[symbol] and adjusts the trail:
      - structure_break → tighten significantly (trend compromised)
      - phase == "range" → tighten moderately (momentum fading)
      - phase == "pullback" → tighten by pullback_trail_tighten factor
      - pullback_depth == "deep" → tighten further

    These tightenings are multiplicative and compound.

    Returns: adjusted trail_distance_atr
    """
    structure_aware_exits = substrate.cfg("exit_rules.structure_aware_exits", False)
    if not structure_aware_exits:
        return trail_distance_atr

    geometry_data = substrate.market.get("geometry", {})
    geometry = geometry_data.get(symbol, {})
    if not geometry:
        return trail_distance_atr

    # Structure break — tighten significantly (trend structure compromised,
    # could be Elliott wave 2/4 but trail protects downside either way)
    if geometry.get("structure_break"):
        tighten = substrate.cfg("exit_rules.structure_break_trail_tighten", 0.5)
        trail_distance_atr *= tighten
        _log.debug(
            "Structure break trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, tighten, trail_distance_atr,
        )

    # Phase range — tighten moderately (momentum fading, impulse may be forming)
    if geometry.get("phase") == "range":
        tighten = substrate.cfg("exit_rules.range_phase_trail_tighten", 0.7)
        trail_distance_atr *= tighten
        _log.debug(
            "Range phase trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, tighten, trail_distance_atr,
        )

    # Tighten when phase shifts to pullback
    if geometry.get("phase") == "pullback":
        tighten_factor = substrate.cfg("exit_rules.pullback_trail_tighten", 0.75)
        trail_distance_atr *= tighten_factor
        _log.debug(
            "Pullback trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, tighten_factor, trail_distance_atr,
        )

    # Tighten further when pullback is deep
    if geometry.get("pullback_depth") == "deep":
        deep_tighten = substrate.cfg("exit_rules.deep_pullback_trail_tighten", 0.5)
        trail_distance_atr *= deep_tighten
        _log.debug(
            "Deep pullback trail tightening for %s: ×%.2f → %.3f ATR",
            symbol, deep_tighten, trail_distance_atr,
        )

    return trail_distance_atr


def update_trailing_stop(position: dict, substrate: Substrate) -> dict:
    """
    Compute updated trailing stop state for a position.

    Returns a NEW position dict with updated trailing stop fields.
    Does NOT mutate the original position dict (shallow-copy safe).

    Reads all config via substrate.cfg() — no raw dict access.

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

    # -- Progressive trailing stop --------------------------------------
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
                schedule = substrate.cfg("exit_rules.progressive_trail_schedule", {0: 1.0})
                trail_distance_atr = lookup_trail_schedule(schedule, max_profit_atr)
                trail_distance_atr = apply_structure_tightening(
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
        trail_distance_atr = lookup_trail_schedule(schedule, max_profit_atr)
        trail_distance_atr = apply_structure_tightening(
            trail_distance_atr, symbol, substrate
        )

        # Compute new trailing SL
        if direction == "long":
            new_sl = mark_price - trail_distance_atr * atr_value
            if trailing_sl is None or new_sl > trailing_sl:
                trailing_sl = new_sl
        else:
            new_sl = mark_price + trail_distance_atr * atr_value
            if trailing_sl is None or new_sl < trailing_sl:
                trailing_sl = new_sl

        return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl,
                "peak_price": peak_price, "max_profit_atr": max_profit_atr}

    # -- Standard ATR trailing stop (backward compatible) ---------------
    trail_atr_mult = substrate.cfg("exit_rules.trailing_stop.trail_atr_multiplier", 1.0)

    # Apply structure-aware stop adjustment
    effective_mult = trail_atr_mult
    structure_aware_exits = substrate.cfg("exit_rules.structure_aware_exits", False)
    if structure_aware_exits and atr_value:
        geometry_data = substrate.market.get("geometry", {})
        geometry = geometry_data.get(symbol, {})
        if geometry:
            # Structure break — tighten significantly
            if geometry.get("structure_break"):
                sb_mult = substrate.cfg("exit_rules.structure_break_stop_multiplier", 0.5)
                effective_mult = sb_mult
                _log.info(
                    "Structure break detected for %s — tightening stop to %.2f ATR",
                    symbol, effective_mult,
                )
            # Phase range — tighten moderately
            elif geometry.get("phase") == "range":
                rp_mult = substrate.cfg("exit_rules.range_phase_stop_multiplier", 0.7)
                effective_mult = rp_mult
                _log.info(
                    "Range phase detected for %s — tightening stop to %.2f ATR",
                    symbol, effective_mult,
                )
            # Deep pullback — tighten (existing logic)
            elif geometry.get("pullback_depth") == "deep":
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
        if atr_value:
            new_sl = mark_price - atr_value * effective_mult
        else:
            new_sl = entry_price  # fallback to breakeven
        if trailing_sl is None or new_sl > trailing_sl:
            trailing_sl = new_sl
    else:
        if atr_value:
            new_sl = mark_price + atr_value * effective_mult
        else:
            new_sl = entry_price
        if trailing_sl is None or new_sl < trailing_sl:
            trailing_sl = new_sl

    return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl, "peak_price": peak_price}


def maintain_trailing_stops(substrate: Substrate) -> None:
    """
    Update trailing stop state for all open positions.

    Called by the daemon at the END of each cycle, after all enzymes have
    fired. This ensures trailing stops continue to track price even when
    no exit_request exists (e.g., after TP1 partial exit when tp1_taken=True
    and no other exit condition triggers).

    Modifies substrate.portfolio["open_positions"] in place (new list, no
    mutation of shared references).
    """
    positions = substrate.portfolio.get("open_positions", [])
    if not positions:
        return

    updated = False
    updated_positions = []
    for pos in positions:
        new_pos = update_trailing_stop(pos, substrate)
        if new_pos is not pos:
            updated = True
        updated_positions.append(new_pos)

    if updated:
        substrate.portfolio["open_positions"] = updated_positions
