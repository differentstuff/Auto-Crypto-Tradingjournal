"""
enzymes/validate_entry_zone.py -- Oxidoreductase enzyme: entry zone + SL/TP.

Reads candidates from substrate.analysis.candidates, computes entry zones
using ATR and S/R levels, and writes entry zones with SL/TP to
substrate.analysis.entry_zones.

Enzyme class: Oxidoreductase
Activates when: analysis.candidates not empty AND analysis.entry_zones is empty

Port of: agent_trade_prep.py (SL/TP logic)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


def _compute_sl_tp(
    direction: str,
    entry_price: float,
    atr_value: float,
    atr_pct: float,
    sr_levels: list[dict],
    rr_minimum: float = 2.0,
    atr_sl_multiplier: float = 1.5,
) -> dict:
    """
    Compute stop-loss and take-profit levels.

    SL: max of (ATR-based) and (nearest S/R level), ensuring minimum distance.
    TP1: conservative target at rr_minimum R:R.
    TP2: full target at 2.5× R:R.

    Returns {sl_price, tp1, tp2, rr_ratio, sl_atr_multiple, sl_type}
    """
    if not entry_price or not atr_value:
        return {
            "sl_price": 0.0, "tp1": 0.0, "tp2": 0.0,
            "rr_ratio": 0.0, "sl_atr_multiple": 0.0, "sl_type": "none",
        }

    # ATR-based SL
    atr_sl = entry_price * (atr_pct / 100) * atr_sl_multiplier

    # S/R-based SL: find nearest support (for long) or resistance (for short)
    sr_sl = 0.0
    sl_type = "atr"

    if direction == "Long":
        # SL below entry: nearest support or ATR-based
        supports = sorted(
            [l["price"] for l in sr_levels if l.get("type") == "support" and l["price"] < entry_price],
            reverse=True,
        )
        if supports:
            nearest_support = supports[0]
            sr_sl = entry_price - nearest_support
            if sr_sl > atr_sl:
                # S/R level is further than ATR SL — use S/R
                sl_type = "sr"
                atr_sl = sr_sl
            elif sr_sl > atr_sl * 0.5:
                # S/R level is meaningful but closer — use S/R
                sl_type = "sr"
                atr_sl = sr_sl

        sl_price = entry_price - atr_sl
        risk = atr_sl

    else:  # Short
        # SL above entry: nearest resistance or ATR-based
        resistances = sorted(
            [l["price"] for l in sr_levels if l.get("type") == "resistance" and l["price"] > entry_price],
        )
        if resistances:
            nearest_resistance = resistances[0]
            sr_sl = nearest_resistance - entry_price
            if sr_sl > atr_sl:
                sl_type = "sr"
                atr_sl = sr_sl
            elif sr_sl > atr_sl * 0.5:
                sl_type = "sr"
                atr_sl = sr_sl

        sl_price = entry_price + atr_sl
        risk = atr_sl

    # Take-profit levels based on R:R
    if risk <= 0:
        return {
            "sl_price": round(sl_price, 8), "tp1": 0.0, "tp2": 0.0,
            "rr_ratio": 0.0, "sl_atr_multiple": round(atr_sl / (entry_price * atr_pct / 100), 2) if atr_pct else 0,
            "sl_type": sl_type,
        }

    rr_ratio = rr_minimum
    if direction == "Long":
        tp1 = entry_price + risk * rr_minimum
        tp2 = entry_price + risk * 2.5
    else:
        tp1 = entry_price - risk * rr_minimum
        tp2 = entry_price - risk * 2.5

    return {
        "sl_price": round(sl_price, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "rr_ratio": round(rr_ratio, 2),
        "sl_atr_multiple": round(atr_sl / (entry_price * atr_pct / 100), 2) if atr_pct else 0,
        "sl_type": sl_type,
    }


@register_enzyme
class ValidateEntryZone(Enzyme):
    """
    Oxidoreductase enzyme: validate entry zones and compute SL/TP.

    For each candidate with a directional signal, computes:
    - Entry price (current price from indicators)
    - Stop-loss (ATR-based or S/R-based)
    - Take-profit 1 (conservative, 2:1 R:R)
    - Take-profit 2 (full target, 2.5:1 R:R)
    - R:R ratio validation

    Writes to substrate.analysis.entry_zones as:
        {symbol: {direction, entry_price, sl_price, tp1, tp2, rr_ratio, ...}}
    """

    name = "ValidateEntryZone"
    enzyme_class = EnzymeClass.OXIDOREDUCTASE
    priority = 1

    def requires(self) -> list[str]:
        return ["analysis.candidates not empty"]

    def prohibits(self) -> list[str]:
        return ["analysis.entry_zones_evaluated is True"]

    def can_activate(self, substrate: Substrate) -> bool:
        candidates = substrate.analysis.get("candidates", [])
        entry_zones_evaluated = substrate.analysis.get("entry_zones_evaluated", False)
        return bool(candidates) and not entry_zones_evaluated

    def transform(self, substrate: Substrate) -> Substrate:
        """Compute entry zones with SL/TP for each candidate."""
        candidates = substrate.analysis.get("candidates", [])
        indicators = substrate.market.get("indicators", {})
        if not candidates:
            return substrate

        # Config values
        rr_minimum = substrate.cfg("scoring.rr_minimum", 2.0)
        atr_sl_multiplier = substrate.cfg("exit_rules.hard_stop.width_atr_multiplier", 1.5)

        entry_zones = {}

        for candidate in candidates:
            symbol = candidate.get("symbol", "")
            score = candidate.get("score", 0)
            pct = candidate.get("pct", 0)

            # Determine direction from score
            if score > 0 or pct > 0:
                direction = "Long"
            elif score < 0 or pct < 0:
                direction = "Short"
            else:
                continue  # Neutral — no entry zone

            sym_data = indicators.get(symbol, {})
            if not sym_data:
                continue

            # Get data from primary timeframe
            primary_tf = list(sym_data.keys())[0] if sym_data else None
            if not primary_tf:
                continue

            tf_inds = sym_data[primary_tf]
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            # Get current price from EMA data
            ema_data = tf_inds.get("ema_stack", {})
            entry_price = ema_data.get("current_price", 0) if isinstance(ema_data, dict) else 0
            if not entry_price:
                continue

            # Get ATR
            atr_data = tf_inds.get("atr", {})
            atr_value = atr_data.get("value", 0) if isinstance(atr_data, dict) else 0
            atr_pct = atr_data.get("pct", 0) if isinstance(atr_data, dict) else 0
            if not atr_value or not atr_pct:
                continue

            # Get S/R levels
            sr_data = tf_inds.get("sr_levels", [])
            sr_levels = sr_data if isinstance(sr_data, list) else []

            # Compute SL/TP
            sl_tp = _compute_sl_tp(
                direction=direction,
                entry_price=entry_price,
                atr_value=atr_value,
                atr_pct=atr_pct,
                sr_levels=sr_levels,
                rr_minimum=rr_minimum,
                atr_sl_multiplier=atr_sl_multiplier,
            )

            # Validate R:R
            if sl_tp["rr_ratio"] < rr_minimum:
                sl_tp["rr_warning"] = f"R:R {sl_tp['rr_ratio']} below minimum {rr_minimum}"

            entry_zones[symbol] = {
                "direction": direction,
                "entry_price": entry_price,
                "atr_value": atr_value,
                "atr_pct": atr_pct,
                **sl_tp,
                "score": score,
                "label": candidate.get("label", ""),
                "timeframe": primary_tf,
            }

        substrate.analysis["entry_zones"] = entry_zones
        substrate.analysis["entry_zones_evaluated"] = True

        self._log.info(
            "Validated entry zones: %d symbols with valid zones",
            len(entry_zones),
        )

        # --- Optional LLM enrichment for complex patterns ---
        # Only fires when 'analysis' role is configured in llm.routing.
        # If call_llm returns None (no key, budget exhausted, provider down),
        # the entry zones are still valid — LLM is purely additive.
        self._optional_llm_validation(substrate, entry_zones)

        return substrate

    def _optional_llm_validation(self, substrate: Substrate, entry_zones: dict) -> None:
        """
        Optionally enrich entry zones with LLM validation.

        Only fires when 'analysis' role is configured in llm.routing.
        If call_llm returns None, the entry zones remain unchanged —
        LLM is purely additive and never blocks the enzyme.

        The LLM receives a compact summary of the entry zone and returns
        a brief validation string stored in entry_zone['llm_validation'].
        """
        # Check if 'analysis' role is configured
        llm_routing = substrate.cfg("llm.routing", {})
        if not llm_routing or "analysis" not in llm_routing:
            return  # No analysis role configured — skip LLM entirely

        try:
            from llm.router import call_llm
        except ImportError:
            return  # LLM module not available

        for symbol, zone in entry_zones.items():
            try:
                prompt = (
                    f"Validate this crypto entry zone:\n"
                    f"Symbol: {symbol}\n"
                    f"Direction: {zone.get('direction', '?')}\n"
                    f"Entry: {zone.get('entry_price', 0):.2f}\n"
                    f"SL: {zone.get('sl_price', 0):.2f} ({zone.get('sl_type', '?')})\n"
                    f"TP1: {zone.get('tp1', 0):.2f} | TP2: {zone.get('tp2', 0):.2f}\n"
                    f"R:R: {zone.get('rr_ratio', 0):.1f}\n"
                    f"Score: {zone.get('score', 0):+.1f}\n"
                    f"Respond with one sentence: confirm, flag concern, or suggest adjustment."
                )

                result = call_llm("analysis", prompt)
                if result:
                    zone["llm_validation"] = result.strip()
                    self._log.debug("LLM validation added for %s", symbol)

            except Exception as exc:
                # Never let LLM errors break the enzyme
                self._log.debug("LLM validation skipped for %s: %s", symbol, exc)

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: higher when entry zones have strong candidates."""
        if not self.can_activate(substrate):
            return 0.0
        candidates = substrate.analysis.get("candidates", [])
        entry_threshold = substrate.cfg("scoring.entry_threshold", 6.5)
        if candidates:
            top_score = abs(candidates[0].get("score", 0))
            if top_score >= entry_threshold:
                return 2.0  # Strong candidate — validate promptly
        return 1.0
