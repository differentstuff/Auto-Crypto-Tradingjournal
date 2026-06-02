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
    rr_minimum: float,
    atr_sl_multiplier: float,
    tp2_rr_ratio: float,
) -> dict:
    """
    Compute stop-loss and take-profit levels.

    All parameters are required — they must come from substrate.cfg().
    No hardcoded defaults. Config is the single source of truth.

    SL: max of (ATR-based) and (nearest S/R level), ensuring minimum distance.
    TP1: conservative target at rr_minimum R:R.
    TP2: full target at tp2_rr_ratio R:R.

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
        tp2 = entry_price + risk * tp2_rr_ratio
    else:
        tp1 = entry_price - risk * rr_minimum
        tp2 = entry_price - risk * tp2_rr_ratio

    return {
        "sl_price": round(sl_price, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "rr_ratio": round(rr_ratio, 2),
        "sl_atr_multiple": round(atr_sl / (entry_price * atr_pct / 100), 2) if atr_pct else 0,
        "sl_type": sl_type,
    }


def _parse_llm_verdict(response: str) -> tuple[str, str]:
    """
    Parse LLM response into structured (verdict, reason) tuple.

    Expects format:
        VERDICT: proceed|confirm|concern|adjust
        REASON: one sentence

    Parsing strategy:
    1. Look for 'VERDICT:' line and extract the keyword
    2. Look for 'REASON:' line and extract the text
    3. Fallback: keyword search in full text if no VERDICT: line found
    4. Safe default: 'confirm' if nothing parseable (don't override on failure)

    Returns:
        (verdict, reason) where verdict is one of:
        proceed, confirm, concern, adjust
    """
    if not response:
        return "confirm", ""

    lines = response.strip().split("\n")
    verdict = None
    reason = ""

    # Structured parse: look for VERDICT: and REASON: lines
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.upper().startswith("VERDICT:"):
            verdict_text = line_stripped[len("VERDICT:"):].strip().lower()
            # Match known keywords
            for keyword in ("proceed", "confirm", "concern", "adjust"):
                if keyword in verdict_text:
                    verdict = keyword
                    break
        elif line_stripped.upper().startswith("REASON:"):
            reason = line_stripped[len("REASON:"):].strip()

    # Fallback: keyword search in full text if structured parse failed
    if verdict is None:
        lower = response.lower()
        if "proceed" in lower:
            verdict = "proceed"
        elif "concern" in lower or "flag" in lower:
            verdict = "concern"
        elif "adjust" in lower:
            verdict = "adjust"
        else:
            verdict = "confirm"  # Safe default — don't override on parse failure

    # If no REASON: line found, use the full response (truncated)
    if not reason:
        reason = response.strip()[:200]

    return verdict, reason


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
        rr_minimum = substrate.cfg("scoring.rr_minimum")
        atr_sl_multiplier = substrate.cfg("exit_rules.hard_stop.width_atr_multiplier")
        tp2_rr_ratio = substrate.cfg("exit_rules.tp2_rr_ratio")

        entry_zones = {}

        for candidate in candidates:
            symbol = candidate.get("symbol", "")
            score = candidate.get("score", 0)
            pct = candidate.get("pct", 0)

            # P1: Skip candidates neutralized by confirmation TF misalignment
            if candidate.get("confirmation_tf_misaligned", False):
                self._log.info(
                    "Skipping entry zone for %s: confirmation TF misaligned", symbol
                )
                continue

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
                tp2_rr_ratio=tp2_rr_ratio,
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
        Enrich entry zones with LLM validation.

        The LLM's role is to ENABLE trades that numeric rules would reject.
        It can say "proceed" to allow a sub-threshold trade, or "concern"/
        "adjust" to flag trades for learning analysis. It NEVER blocks a trade.

        Config switches:
          - llm.enabled: system-wide on/off for LLM calls
          - llm.relax_factor: candidates scoring above (threshold * relax_factor)
            but below threshold are sent to LLM for review.

        Verdict parsing:
          - VERDICT: proceed  → trade allowed despite sub-threshold score
          - VERDICT: confirm  → numeric and LLM agree, trade solid
          - VERDICT: concern  → flag for learning, trade still proceeds
          - VERDICT: adjust  → SL/TP suggestion, trade still proceeds

        If call_llm returns None or parsing fails, the entry zone keeps its
        numeric verdict only — LLM is additive, never required.
        """
        # Check system-wide LLM switch
        llm_enabled = substrate.cfg("llm.enabled")
        if not llm_enabled:
            self._log.debug("LLM validation skipped: llm.enabled is false")
            return

        # Check if 'analysis' role is configured in routing
        llm_routing = substrate.cfg("llm.routing", {})
        if not llm_routing or "analysis" not in llm_routing:
            return  # No analysis role configured — skip LLM entirely

        try:
            from llm.router import call_llm
        except ImportError:
            return  # LLM module not available

        entry_threshold = substrate.cfg("scoring.entry_threshold")
        relax_factor = substrate.cfg("llm.relax_factor")
        relaxed_threshold = entry_threshold * relax_factor

        llm_model = llm_routing.get("analysis", {}).get("model", "unknown")

        for symbol, zone in entry_zones.items():
            score = abs(zone.get("score", 0))

            # Send to LLM if score is above relaxed threshold
            # (includes both above-threshold and borderline candidates)
            if score < relaxed_threshold:
                self._log.debug(
                    "Skipping LLM validation for %s: score %.1f below relaxed threshold %.1f",
                    symbol, score, relaxed_threshold,
                )
                continue

            try:
                # Build indicator summary for LLM context
                indicators = substrate.market.get("indicators", {})
                sym_data = indicators.get(symbol, {})
                indicator_summary = self._summarize_indicators(sym_data, zone)

                prompt = (
                    f"Analyze this crypto entry setup:\n"
                    f"Symbol: {symbol}\n"
                    f"Direction: {zone.get('direction', '?')}\n"
                    f"Entry: {zone.get('entry_price', 0):.2f}\n"
                    f"SL: {zone.get('sl_price', 0):.2f} ({zone.get('sl_type', '?')})\n"
                    f"TP1: {zone.get('tp1', 0):.2f} | TP2: {zone.get('tp2', 0):.2f}\n"
                    f"R:R: {zone.get('rr_ratio', 0):.1f}\n"
                    f"Score: {zone.get('score', 0):+.1f} (threshold: {entry_threshold})\n"
                    f"{indicator_summary}\n"
                    f"\nRespond with EXACTLY this format:\n"
                    f"VERDICT: proceed|confirm|concern|adjust\n"
                    f"REASON: one sentence explaining your assessment"
                )

                result = call_llm("analysis", prompt)
                if result:
                    verdict, reason = _parse_llm_verdict(result)
                    zone["llm_verdict"] = verdict
                    zone["llm_reason"] = reason
                    zone["llm_model"] = llm_model
                    zone["llm_enabled"] = True
                    # Flag override: LLM enabled a sub-threshold trade
                    zone["llm_override"] = verdict == "proceed" and score < entry_threshold
                    self._log.info(
                        "LLM verdict for %s: %s (score=%.1f, override=%s)",
                        symbol, verdict, score, zone.get("llm_override", False),
                    )
                else:
                    zone["llm_verdict"] = None
                    zone["llm_reason"] = None
                    zone["llm_model"] = None
                    zone["llm_enabled"] = True
                    zone["llm_override"] = False

            except Exception as exc:
                # Never let LLM errors break the enzyme
                self._log.debug("LLM validation skipped for %s: %s", symbol, exc)
                zone["llm_verdict"] = None
                zone["llm_reason"] = None
                zone["llm_model"] = None
                zone["llm_enabled"] = True
                zone["llm_override"] = False

    @staticmethod
    def _summarize_indicators(sym_data: dict, zone: dict) -> str:
        """Build a compact indicator summary for the LLM prompt."""
        if not sym_data:
            return "Indicators: (no data)"
        # Pick the first timeframe with data
        for tf, tf_data in sym_data.items():
            if isinstance(tf_data, dict) and tf_data.get("ok"):
                parts = []
                for key in ("rsi", "macd", "ema_stack", "adx", "volume"):
                    ind = tf_data.get(key)
                    if isinstance(ind, dict):
                        if key == "rsi":
                            parts.append(f"RSI: {ind.get('value', '?')}")
                        elif key == "macd":
                            parts.append(f"MACD: {ind.get('bias', '?')} (hist {'growing' if ind.get('histogram_growing') else 'fading'})")
                        elif key == "ema_stack":
                            parts.append(f"EMA: {ind.get('alignment', '?')} stack={ind.get('stack', '?')}")
                        elif key == "adx":
                            parts.append(f"ADX: {ind.get('value', '?')} ({ind.get('direction', '?')})")
                        elif key == "volume":
                            parts.append(f"Volume: {ind.get('ratio', '?')}x avg")
                return f"Indicators ({tf}): {', '.join(parts)}" if parts else "Indicators: (summary unavailable)"
        return "Indicators: (no valid timeframe data)"

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: higher when entry zones have strong candidates."""
        if not self.can_activate(substrate):
            return 0.0
        candidates = substrate.analysis.get("candidates", [])
        entry_threshold = substrate.cfg("scoring.entry_threshold")
        if candidates:
            top_score = abs(candidates[0].get("score", 0))
            if top_score >= entry_threshold:
                return 2.0  # Strong candidate — validate promptly
        return 1.0
