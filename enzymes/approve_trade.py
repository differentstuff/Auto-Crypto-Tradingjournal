"""
enzymes/approve_trade.py -- Regulator enzyme: RiskManager entry gate.

Validates entry zones from substrate.analysis.entry_zones and decides
whether to approve the trade. Enforces:
  - SL placement correctness (below entry for long, above for short)
  - Position size via Kelly criterion (config-driven)
  - ATR-based volatility cap on position size
  - Directional concentration risk
  - Size caps (min/max % of equity)

ISC enforcement (max positions, noise flag) is handled by the daemon's
ISC gate — this enzyme never fires when ISCs block trades.

Writes: decisions.trade_approved (dict or None), decisions.action

Enzyme class: Regulator (priority 10)
Activates when: analysis.entry_zones not empty AND trade_approved not yet set

Port of: agent_risk_mgmt.py (Kelly sizing, SL validation, concentration checks)
ATR cap: position size = min(kelly_size, atr_cap_size) where
    atr_cap_size = (equity * atr_cap_equity_pct) / ATR_value
This ensures volatile assets never risk more than a configurable
percentage of equity per trade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate
from core.position_sizing import (
    kelly_fraction as _kelly_fraction_pure,
    compute_atr_cap as _compute_atr_cap_pure,
    compute_size as _compute_size_pure,
)

_log = logging.getLogger(__name__)


def _kelly_fraction(score: float, substrate: Substrate) -> float:
    """Kelly criterion wrapper — reads config from substrate, delegates to pure function."""
    return _kelly_fraction_pure(
        score=score,
        kelly_min=substrate.cfg("risk.kelly_min"),
        kelly_max=substrate.cfg("risk.kelly_max"),
        wr_base=substrate.cfg("risk.kelly_win_rate_base"),
        wr_range=substrate.cfg("risk.kelly_win_rate_range"),
        avg_win_r=substrate.cfg("risk.kelly_avg_win_r"),
    )


def _compute_atr_cap(equity: float, atr_value: float, substrate: Substrate) -> float:
    """ATR cap wrapper — reads config from substrate, delegates to pure function."""
    atr_cap_pct = substrate.cfg("portfolio.atr_cap_equity_pct", None)
    return _compute_atr_cap_pure(equity, atr_value, atr_cap_pct or 0)


def _compute_size(
    equity: float,
    entry_price: float,
    sl_price: float,
    direction: str,
    kelly_fraction: float,
    leverage: int,
    substrate: Substrate,
    atr_value: float = 0.0,
) -> dict:
    """Position sizing wrapper — reads config from substrate, delegates to pure function."""
    result = _compute_size_pure(
        equity=equity,
        entry_price=entry_price,
        sl_price=sl_price,
        direction=direction,
        kelly_frac=kelly_fraction,
        leverage=leverage,
        risk_per_trade_pct=substrate.cfg("portfolio.risk_per_trade_pct"),
        max_size_pct=substrate.cfg("risk.max_size_pct_of_equity"),
        min_size_pct=substrate.cfg("risk.min_size_pct_of_equity"),
        atr_value=atr_value,
        atr_cap_pct=substrate.cfg("portfolio.atr_cap_equity_pct", 0),
    )
    if result["atr_cap_applied"]:
        _log.info(
            "ATR cap applied: notional %.2f → %.2f (ATR=%.4f, cap_pct=%.1f%%)",
            result["size_usdt"], result["atr_cap_notional"], atr_value,
            substrate.cfg("portfolio.atr_cap_equity_pct", 0),
        )
    return result


@register_enzyme
class ApproveTrade(Enzyme):
    """
    Regulator enzyme: RiskManager entry gate.

    Decides whether to approve a trade based on entry zones, risk parameters,
    and ISC conditions. Only Regulator enzymes can approve trades — all other
    enzymes can only request.

    Checks (all config-driven):
      1. SL placement (below entry for long, above for short)
      2. Kelly sizing within bounds
      3. ATR volatility cap on position size
      4. Directional concentration

    ISC enforcement (max positions, noise flag) is handled by the
    daemon's ISC gate — this enzyme never fires when ISCs block trades.
    """

    name = "ApproveTrade"
    enzyme_class = EnzymeClass.REGULATOR
    priority = 10

    def requires(self) -> list[str]:
        return ["analysis.entry_zones not empty"]

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        entry_zones = substrate.analysis.get("entry_zones", {})
        trade_approved = substrate.decisions.get("trade_approved")
        # Activate when entry zones exist and no trade approved yet
        return bool(entry_zones) and trade_approved is None

    def transform(self, substrate: Substrate) -> Substrate:
        """Evaluate entry zones and approve or block trades.

        Applies soft penalties to compute effective_score, then checks
        against approval_threshold. Hard ISCs (SL, size, max positions)
        are still enforced by the daemon's ISC gate.
        """
        entry_zones = substrate.analysis.get("entry_zones", {})
        if not entry_zones:
            return substrate

        equity = substrate.portfolio.get("equity", 0)
        open_positions = substrate.portfolio.get("open_positions", [])
        leverage = substrate.cfg("portfolio.leverage")

        # Evaluate each entry zone (take the best one)
        best_approved = None
        best_effective_score = -float("inf")
        approval_threshold = substrate.cfg("scoring.approval_threshold", 4.5)
        entry_threshold = substrate.cfg("scoring.entry_threshold")
        llm_enabled = substrate.cfg("llm.enabled", False)

        for symbol, zone in entry_zones.items():
            direction = zone.get("direction", "")
            entry_price = zone.get("entry_price", 0)
            sl_price = zone.get("sl_price", 0)
            raw_score = zone.get("score", 0)
            tp1 = zone.get("tp1", 0)
            tp2 = zone.get("tp2", 0)
            atr_value = zone.get("atr_value", 0)
            llm_verdict = zone.get("llm_verdict")
            llm_override = zone.get("llm_override", False)

            # --- Apply soft penalties to compute effective score ---
            effective_score = substrate.compute_effective_score(raw_score)
            penalties = substrate.soft_penalties()
            any_penalty = any(r > 0 for r in penalties.values())

            # --- LLM override gate ---
            # Borderline candidates (effective_score < approval_threshold) need LLM "proceed" to pass.
            # Above-threshold candidates pass on numeric rules alone.
            # LLM NEVER blocks a trade — it only enables sub-threshold ones.
            if abs(effective_score) < approval_threshold:
                if llm_verdict == "proceed" and llm_override:
                    self._log.info(
                        "LLM override: %s approved despite effective_score %.1f < threshold %.1f",
                        symbol, abs(effective_score), approval_threshold,
                    )
                else:
                    # Sub-threshold without LLM proceed — skip this candidate
                    self._log.debug(
                        "Skipping %s: effective_score %.1f < approval_threshold %.1f (raw=%.1f, penalties=%s)",
                        symbol, abs(effective_score), approval_threshold, raw_score, penalties,
                    )
                    continue

            # Validate SL placement
            if direction == "Long" and sl_price >= entry_price:
                self._log.warning("Blocked %s: SL above entry for Long", symbol)
                continue
            if direction == "Short" and sl_price <= entry_price:
                self._log.warning("Blocked %s: SL below entry for Short", symbol)
                continue

            # Kelly sizing (use effective_score for sizing — penalized trades get smaller positions)
            kelly = _kelly_fraction(abs(effective_score), substrate)

            # Compute size (with ATR cap)
            sizing = _compute_size(
                equity=equity,
                entry_price=entry_price,
                sl_price=sl_price,
                direction=direction,
                kelly_fraction=kelly,
                leverage=leverage,
                substrate=substrate,
                atr_value=atr_value,
            )

            if sizing["size_usdt"] <= 0:
                self._log.warning("Blocked %s: size computed as 0", symbol)
                continue

            # Directional concentration check
            max_same = substrate.cfg("portfolio.max_same_direction")
            same_dir_count = sum(
                1 for p in open_positions
                if p.get("direction", "").lower() == direction.lower()
            )
            if same_dir_count >= max_same:
                self._log.warning(
                    "Blocked %s: directional concentration (%d %s positions)",
                    symbol, same_dir_count, direction,
                )
                continue

            # Approved
            approved = {
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tp1": tp1,
                "tp2": tp2,
                "size_usdt": sizing["size_usdt"],
                "kelly_fraction": kelly,
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "atr_value": atr_value,
                "atr_cap_applied": sizing["atr_cap_applied"],
                "atr_cap_notional": sizing["atr_cap_notional"],
                "score": raw_score,
                "effective_score": round(effective_score, 2),
                "penalties": penalties,
                # LLM tracking fields — recorded in trade_learning for analysis
                "llm_verdict": llm_verdict,
                "llm_reason": zone.get("llm_reason"),
                "llm_model": zone.get("llm_model"),
                "llm_enabled": llm_enabled,
                "llm_override": llm_override,
            }

            # Track the best candidate by effective score
            if abs(effective_score) > best_effective_score:
                best_approved = approved
                best_effective_score = abs(effective_score)

        if best_approved:
            substrate.decisions["trade_approved"] = best_approved
            atr_cap_msg = " [ATR cap]" if best_approved.get("atr_cap_applied") else ""
            llm_msg = f" [LLM {best_approved.get('llm_verdict', '?')}]" if best_approved.get("llm_verdict") else ""
            llm_override_msg = " [LLM OVERRIDE]" if best_approved.get("llm_override") else ""
            penalty_msg = " [PENALIZED]" if any(best_approved.get("penalties", {}).values()) else ""
            self._log.info(
                "Approved: %s %s size=%.2f kelly=%.3f eff_score=%.2f%s%s%s%s",
                best_approved["direction"], best_approved["symbol"],
                best_approved["size_usdt"], best_approved["kelly_fraction"],
                best_approved.get("effective_score", 0),
                atr_cap_msg, llm_msg, llm_override_msg, penalty_msg,
            )
        else:
            substrate.decisions["trade_approved"] = None
            self._log.info("No trade approved this cycle")

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Regulators always have priority."""
        if self.can_activate(substrate):
            return 10.0
        return 0.0