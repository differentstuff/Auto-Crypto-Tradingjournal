"""
enzymes/approve_trade.py -- Regulator enzyme: RiskManager entry gate.

Validates entry zones from substrate.analysis.entry_zones and decides
whether to approve the trade. Enforces:
  - SL placement correctness (below entry for long, above for short)
  - Position size via Kelly criterion (config-driven)
  - ATR-based volatility cap on position size
  - Max position count
  - Noise flag (ISC-005)
  - Directional concentration risk
  - Size caps (min/max % of equity)

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

_log = logging.getLogger(__name__)


def _kelly_fraction(score: float, substrate: Substrate) -> float:
    """
    Kelly criterion using confluence score as edge proxy.

    Maps score to win_rate proxy, then computes Kelly fraction.
    Capped between kelly_min and kelly_max (from config risk section).
    Uses substrate.cfg() for all config reads — no raw dict access.
    """
    risk_cfg = substrate.cfg("risk")
    kelly_min = substrate.cfg("risk.kelly_min")
    kelly_max = substrate.cfg("risk.kelly_max")
    wr_base = substrate.cfg("risk.kelly_win_rate_base")
    wr_range = substrate.cfg("risk.kelly_win_rate_range")
    avg_win_r = substrate.cfg("risk.kelly_avg_win_r")

    # Map score (0-10) to win_rate proxy
    win_rate = wr_base + (score / 10) * wr_range

    # Kelly: f = (p * b - q) / b where b = avg_win_r, p = win_rate, q = 1-p
    f = (win_rate * avg_win_r - (1 - win_rate)) / avg_win_r

    return round(max(kelly_min, min(kelly_max, f)), 3)


def _compute_atr_cap(equity: float, atr_value: float, substrate: Substrate) -> float:
    """
    ATR-based position size cap.

    Returns the maximum notional position size based on asset volatility.
    Formula: atr_cap_notional = (equity * atr_cap_equity_pct) / ATR_value

    High ATR (volatile asset) → small cap.
    Low ATR (calm asset) → large cap (likely won't bind).

    Returns 0.0 if the cap cannot be computed (missing config or ATR),
    which signals to _compute_size() that the cap should not be applied.
    Uses substrate.cfg() for all config reads.
    """
    if not equity or not atr_value or atr_value <= 0:
        return 0.0
    atr_cap_equity_pct = substrate.cfg("portfolio.atr_cap_equity_pct", None)
    if atr_cap_equity_pct is None or atr_cap_equity_pct <= 0:
        return 0.0
    return (equity * atr_cap_equity_pct) / atr_value


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
    """
    Compute position size based on risk parameters.

    Applies ATR cap as an additional constraint:
        position_size = min(kelly_size, atr_cap_size)

    Returns dict with: size_usdt, margin_usdt, risk_pct, stop_dist_pct,
                        atr_cap_applied, atr_cap_notional
    Uses substrate.cfg() for all config reads.
    """
    _empty = {
        "size_usdt": 0, "margin_usdt": 0, "risk_pct": 0,
        "stop_dist_pct": 0, "atr_cap_applied": False, "atr_cap_notional": 0.0,
    }
    if not equity or not entry_price or not sl_price:
        return _empty

    risk_per_trade_pct = substrate.cfg("portfolio.risk_per_trade_pct")
    max_size_pct = substrate.cfg("risk.max_size_pct_of_equity")
    min_size_pct = substrate.cfg("risk.min_size_pct_of_equity")

    # Stop distance
    stop_dist_pct = abs(entry_price - sl_price) / entry_price
    if stop_dist_pct == 0:
        return _empty

    # Risk amount
    risk_amt = equity * risk_per_trade_pct / 100

    # Notional from risk
    notional = risk_amt / stop_dist_pct

    # Apply Kelly fraction
    notional *= kelly_fraction

    # Cap at max_size_pct of equity
    max_notional = equity * max_size_pct / 100
    if notional > max_notional:
        notional = max_notional

    # ATR cap: reduce position for volatile assets
    atr_cap_applied = False
    atr_cap_notional = 0.0
    if atr_value > 0:
        atr_cap_notional = _compute_atr_cap(equity, atr_value, substrate)
        if atr_cap_notional > 0 and notional > atr_cap_notional:
            atr_cap_equity_pct = substrate.cfg("portfolio.atr_cap_equity_pct")
            _log.info(
                "ATR cap applied: notional %.2f → %.2f (ATR=%.4f, cap_pct=%.1f%%)",
                notional, atr_cap_notional, atr_value,
                atr_cap_equity_pct if atr_cap_equity_pct else 0,
            )
            notional = atr_cap_notional
            atr_cap_applied = True

    # Floor at min_size_pct of equity (only when ATR cap doesn't bind).
    # When ATR cap is applied, it's a hard maximum that overrides the soft
    # floor — the asset is too volatile for a normal-sized position, and
    # the min floor must not override that safety constraint.
    if not atr_cap_applied:
        min_notional = equity * min_size_pct / 100
        if notional < min_notional:
            notional = min_notional

    margin = notional / leverage

    return {
        "size_usdt": round(notional, 2),
        "margin_usdt": round(margin, 2),
        "risk_pct": round(risk_per_trade_pct, 2),
        "stop_dist_pct": round(stop_dist_pct * 100, 3),
        "atr_cap_applied": atr_cap_applied,
        "atr_cap_notional": round(atr_cap_notional, 2),
    }


@register_enzyme
class ApproveTrade(Enzyme):
    """
    Regulator enzyme: RiskManager entry gate.

    Decides whether to approve a trade based on entry zones, risk parameters,
    and ISC conditions. Only Regulator enzymes can approve trades — all other
    enzymes can only request.

    Checks (all config-driven):
      1. SL placement (below entry for long, above for short)
      2. Max positions limit
      3. Noise flag (ISC-005)
      4. Kelly sizing within bounds
      5. ATR volatility cap on position size
      6. Directional concentration
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
        """Evaluate entry zones and approve or block trades."""
        entry_zones = substrate.analysis.get("entry_zones", {})
        if not entry_zones:
            return substrate

        equity = substrate.portfolio.get("equity", 0)
        open_positions = substrate.portfolio.get("open_positions", [])
        noise_flag = substrate.analysis.get("noise_flag", False)
        max_positions = substrate.cfg("strategy.max_positions")
        leverage = substrate.cfg("portfolio.leverage")

        # ISC: max positions
        if len(open_positions) >= max_positions:
            self._log.info(
                "Blocked: max positions reached (%d/%d)",
                len(open_positions), max_positions,
            )
            substrate.decisions["trade_approved"] = None
            return substrate

        # ISC: noise flag
        if noise_flag:
            self._log.info("Blocked: noise flag is set")
            substrate.decisions["trade_approved"] = None
            return substrate

        # Evaluate each entry zone (take the best one)
        best_approved = None
        best_score = -float("inf")

        for symbol, zone in entry_zones.items():
            direction = zone.get("direction", "")
            entry_price = zone.get("entry_price", 0)
            sl_price = zone.get("sl_price", 0)
            score = zone.get("score", 0)
            tp1 = zone.get("tp1", 0)
            tp2 = zone.get("tp2", 0)
            atr_value = zone.get("atr_value", 0)

            # Validate SL placement
            if direction == "Long" and sl_price >= entry_price:
                self._log.warning("Blocked %s: SL above entry for Long", symbol)
                continue
            if direction == "Short" and sl_price <= entry_price:
                self._log.warning("Blocked %s: SL below entry for Short", symbol)
                continue

            # Kelly sizing
            kelly = _kelly_fraction(abs(score), substrate)

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
                "score": score,
            }

            # Track the best candidate by absolute score
            if abs(score) > best_score:
                best_approved = approved
                best_score = abs(score)

        if best_approved:
            substrate.decisions["trade_approved"] = best_approved
            atr_cap_msg = " [ATR cap]" if best_approved.get("atr_cap_applied") else ""
            self._log.info(
                "Approved: %s %s size=%.2f kelly=%.3f%s",
                best_approved["direction"], best_approved["symbol"],
                best_approved["size_usdt"], best_approved["kelly_fraction"],
                atr_cap_msg,
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