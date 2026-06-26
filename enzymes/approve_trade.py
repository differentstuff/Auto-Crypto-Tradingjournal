"""
enzymes/approve_trade.py -- Regulator enzyme: RiskManager entry gate.

Validates entry zones from substrate.analysis.entry_zones and decides
whether to approve the trade. Enforces:
  - SL placement correctness (below entry for long, above for short)
  - Position size via Kelly criterion (config-driven)
  - Volatility cap on position size (ATR%-based, asset-price-agnostic)
  - Notional exposure ceiling (flash crash protection)
  - Directional concentration risk
  - Size caps (min/max % of equity, leverage-aware)

ISC enforcement (max positions, noise flag) is handled by the daemon's
ISC gate — this enzyme never fires when ISCs block trades.

Writes: decisions.trade_approved (dict or None), decisions.action

Enzyme class: Regulator (priority 10)
Activates when: analysis.entry_zones not empty AND trade_approved not yet set

Port of: agent_risk_mgmt.py (Kelly sizing, SL validation, concentration checks)
Volatility cap: position_size = min(kelly_size, volatility_cap_size) where
    volatility_cap_size = (equity * volatility_cap_pct) / atr_pct
This ensures volatile assets never risk more than a configurable
percentage of equity per trade. Uses ATR% (relative) so the cap is
asset-price-agnostic — BTC at $80k and SHIB at $0.00001 with the
same ATR% get the same cap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate
from core.position_sizing import (
    kelly_fraction as _kelly_fraction_pure,
    compute_volatility_cap as _compute_volatility_cap_pure,
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


def _compute_volatility_cap(equity: float, atr_pct: float, substrate: Substrate) -> float:
    """Volatility cap wrapper — reads config from substrate, delegates to pure function."""
    volatility_cap_pct = substrate.cfg("portfolio.volatility_cap_pct", None)
    return _compute_volatility_cap_pure(equity, atr_pct, volatility_cap_pct or 0)


def _compute_size(
    equity: float,
    entry_price: float,
    sl_price: float,
    direction: str,
    kelly_fraction: float,
    leverage: int,
    substrate: Substrate,
    atr_pct: float = 0.0,
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
        atr_pct=atr_pct,
        volatility_cap_pct=substrate.cfg("portfolio.volatility_cap_pct", 0),
        max_notional_exposure_pct=substrate.cfg("portfolio.max_notional_exposure_pct", 0),
    )
    if result["volatility_cap_applied"]:
        _log.info(
            "Volatility cap applied: notional %.2f → %.2f (ATR%%=%.2f%%, cap_pct=%.1f%%)",
            result["size_usdt"], result["volatility_cap_notional"], atr_pct,
            substrate.cfg("portfolio.volatility_cap_pct", 0),
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

            # Compute size (with volatility cap)
            atr_pct = zone.get("atr_pct", 0)
            sizing = _compute_size(
                equity=equity,
                entry_price=entry_price,
                sl_price=sl_price,
                direction=direction,
                kelly_fraction=kelly,
                leverage=leverage,
                substrate=substrate,
                atr_pct=atr_pct,
            )

            if sizing["size_usdt"] <= 0:
                self._log.warning("Blocked %s: size computed as 0", symbol)
                continue

            # Fix 4: Per-direction concentration check
            max_same_long = substrate.cfg("portfolio.max_same_direction_long", None)
            max_same_short = substrate.cfg("portfolio.max_same_direction_short", None)
            if direction.lower() == "long":
                max_same = max_same_long if max_same_long is not None else substrate.cfg("portfolio.max_same_direction")
            else:
                max_same = max_same_short if max_same_short is not None else substrate.cfg("portfolio.max_same_direction")
            same_dir_count = sum(
                1 for p in open_positions
                if p.get("direction", "").lower() == direction.lower()
            )
            if same_dir_count >= max_same:
                self._log.warning(
                    "Blocked %s: directional concentration (%d %s positions, max %d)",
                    symbol, same_dir_count, direction, max_same,
                )
                continue

            # Fix 2: Three-layer re-entry guard
            # Layer 0: No duplicate positions for the same symbol
            if any(p.get("symbol") == symbol for p in open_positions):
                self._log.info("Blocked %s: position already open", symbol)
                continue

            # Layer 2A: Cooldown — N candles must have closed since last close
            cooldown_candles = substrate.cfg("strategy.reentry_cooldown_candles", None)
            recently_closed = substrate.market.get("recently_closed", {})
            if cooldown_candles and symbol in recently_closed and recently_closed[symbol]:
                try:
                    from enzymes.collect_ohlcv import timeframe_to_minutes, candle_floor
                    primary_tf = substrate.strategy.get("timeframe", "4H")
                    tf_mins = timeframe_to_minutes(primary_tf)
                    closed_dt = datetime.fromisoformat(recently_closed[symbol])
                    if closed_dt.tzinfo is None:
                        closed_dt = closed_dt.replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    current_candle = candle_floor(now_dt, primary_tf)
                    candles_elapsed = int((current_candle - closed_dt).total_seconds() / (tf_mins * 60))
                    if candles_elapsed < cooldown_candles:
                        self._log.info(
                            "Blocked %s: re-entry cooldown (%d/%d candles elapsed)",
                            symbol, candles_elapsed, cooldown_candles,
                        )
                        continue
                except (ValueError, TypeError, ImportError):
                    pass  # Invalid timestamp or import — skip cooldown

            # Layer 2B: Bar confirmation — at least 1 new candle since last trade
            last_traded = substrate.market.get("last_traded_candle_idx", {})
            if symbol in last_traded and last_traded[symbol]:
                try:
                    from enzymes.collect_ohlcv import timeframe_to_minutes, candle_floor
                    primary_tf = substrate.strategy.get("timeframe", "4H")
                    last_traded_dt = datetime.fromisoformat(last_traded[symbol])
                    if last_traded_dt.tzinfo is None:
                        last_traded_dt = last_traded_dt.replace(tzinfo=timezone.utc)
                    now_dt = datetime.now(timezone.utc)
                    current_candle = candle_floor(now_dt, primary_tf)
                    if current_candle <= last_traded_dt:
                        self._log.info(
                            "Blocked %s: no new candle since last trade (bar confirmation)",
                            symbol,
                        )
                        continue
                except (ValueError, TypeError, ImportError):
                    pass  # Invalid timestamp or import — skip bar confirmation

            # Layer 2C: Signal re-confirmation — candidate must exist in current cycle
            require_signal_confirm = substrate.cfg("strategy.reentry_require_signal_confirm", None)
            if require_signal_confirm and symbol in recently_closed:
                candidates = substrate.analysis.get("candidates", [])
                symbol_candidate = next((c for c in candidates if c.get("symbol") == symbol), None)
                if not symbol_candidate:
                    self._log.info(
                        "Blocked %s: no valid candidate after cooldown (signal re-confirmation)",
                        symbol,
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
                "atr_pct": atr_pct,
                "volatility_cap_applied": sizing["volatility_cap_applied"],
                "volatility_cap_notional": sizing["volatility_cap_notional"],
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
            vol_cap_msg = " [VOL CAP]" if best_approved.get("volatility_cap_applied") else ""
            llm_msg = f" [LLM {best_approved.get('llm_verdict', '?')}]" if best_approved.get("llm_verdict") else ""
            llm_override_msg = " [LLM OVERRIDE]" if best_approved.get("llm_override") else ""
            penalty_msg = " [PENALIZED]" if any(best_approved.get("penalties", {}).values()) else ""
            self._log.info(
                "Approved: %s %s size=%.2f kelly=%.3f eff_score=%.2f%s%s%s%s",
                best_approved["direction"], best_approved["symbol"],
                best_approved["size_usdt"], best_approved["kelly_fraction"],
                best_approved.get("effective_score", 0),
                vol_cap_msg, llm_msg, llm_override_msg, penalty_msg,
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