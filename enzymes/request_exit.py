"""
enzymes/request_exit.py -- Sensor enzyme: monitors open positions for exit conditions.

Scans all open positions and requests exit when:
  - Hard SL is breached (mark_price crosses sl_price)
  - TP1 is hit (mark_price reaches take-profit)
  - Signal reversal detected (indicators reversed vs entry direction)
  - Trailing stop triggered (via ApproveExit evaluation)

Only REQUESTS exits — does not approve or execute them.
The RiskManager (ApproveExit) decides whether to approve.

Writes: decisions.exit_request (dict or None)

Enzyme class: Sensor (monitors, does not decide)
Activates when: portfolio.open_positions not empty

Port of: agent_trade_monitor.py, entry_watcher.py (monitoring logic)
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class RequestExit(Enzyme):
    """
    Sensor enzyme: monitor open positions and request exits when needed.

    This enzyme only REQUESTS exits — it cannot approve or execute them.
    The RiskManager (ApproveExit Regulator) evaluates the request and
    decides whether to approve.

    Checks for each open position:
      1. SL breach (immediate urgency)
      2. TP1 hit (normal urgency)
      3. Signal reversal from current indicators (low urgency)
    """

    name = "RequestExit"
    enzyme_class = EnzymeClass.SENSOR
    priority = 2

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        open_positions = substrate.portfolio.get("open_positions", [])
        exit_request = substrate.decisions.get("exit_request")
        if not open_positions or exit_request is not None:
            return False
        # Require mark_price > 0 for all positions — we cannot evaluate
        # exit conditions on stale prices. UpdateMarkPrices should have
        # run before this enzyme to provide current prices.
        for pos in open_positions:
            if not pos.get("mark_price", 0):
                return False
        return True

    @staticmethod
    def _is_against_position(geometry: dict, direction: str) -> bool:
        """
        Check if the market structure breakout direction opposes the position.

        A bearish breakout against a long, or bullish breakout against a short.
        """
        trend = geometry.get("trend_direction", "")
        if direction == "long" and trend == "bearish":
            return True
        if direction == "short" and trend == "bullish":
            return True
        return False

    def transform(self, substrate: Substrate) -> Substrate:
        """Scan open positions and request exit if conditions are met."""
        positions = substrate.portfolio.get("open_positions", [])
        if not positions:
            return substrate

        # Use current indicator data if available
        indicators = substrate.market.get("indicators", {})
        soft_exit_requires = substrate.cfg("exit_rules.soft_exit.requires_indicators_reversed")

        # Read structure-aware exit config
        structure_aware_exits = substrate.cfg("exit_rules.structure_aware_exits", False)
        geometry_data = substrate.market.get("geometry", {})

        for pos in positions:
            symbol = pos.get("symbol", "")
            direction = pos.get("direction", "Long").lower()
            entry_price = pos.get("entry_price", 0)
            sl_price = pos.get("sl_price", 0)
            tp1 = pos.get("tp1", 0)
            mark_price = pos.get("mark_price", 0)

            if not mark_price:
                continue

            # ── Structure-aware exits (highest priority) ──────────────────
            # Evaluated BEFORE standard SL/TP checks because structure break
            # is an absolute exit — it overrides all other logic.
            if structure_aware_exits and geometry_data:
                geometry = geometry_data.get(symbol, {})
                if geometry:
                    # Priority 1: structure break — EXIT immediately
                    if (geometry.get("structure_break")
                            and substrate.cfg("exit_rules.structure_break_exit", True)):
                        substrate.decisions["exit_request"] = {
                            "symbol": symbol,
                            "reason": "structure_break",
                            "urgency": "immediate",
                        }
                        self._log.warning(
                            "STRUCTURE BREAK: %s %s — immediate exit",
                            symbol, direction,
                        )
                        return substrate

                    # Priority 2: phase shifted to range (was trending)
                    prev_phase = geometry.get("previous_phase", "")
                    if (geometry.get("phase") == "range"
                            and prev_phase in ("impulse", "pullback")
                            and substrate.cfg("exit_rules.phase_range_exit", True)):
                        substrate.decisions["exit_request"] = {
                            "symbol": symbol,
                            "reason": "phase_range",
                            "urgency": "immediate",
                        }
                        self._log.info(
                            "PHASE RANGE: %s was %s, now range — immediate exit",
                            symbol, prev_phase,
                        )
                        return substrate

                    # Priority 3: counter-structure breakout
                    if (geometry.get("phase") == "breakout"
                            and self._is_against_position(geometry, direction)
                            and substrate.cfg("exit_rules.counter_breakout_exit", True)):
                        substrate.decisions["exit_request"] = {
                            "symbol": symbol,
                            "reason": "counter_breakout",
                            "urgency": "immediate",
                        }
                        self._log.info(
                            "COUNTER BREAKOUT: %s %s vs %s trend — immediate exit",
                            symbol, direction, geometry.get("trend_direction", "?"),
                        )
                        return substrate

            # 1. SL breach — immediate urgency
            if sl_price:
                if direction == "long" and mark_price <= sl_price:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "sl_breach",
                        "urgency": "immediate",
                    }
                    self._log.warning(
                        "SL BREACH: %s long mark=%.2f sl=%.2f",
                        symbol, mark_price, sl_price,
                    )
                    return substrate

                if direction == "short" and mark_price >= sl_price:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "sl_breach",
                        "urgency": "immediate",
                    }
                    self._log.warning(
                        "SL BREACH: %s short mark=%.2f sl=%.2f",
                        symbol, mark_price, sl_price,
                    )
                    return substrate

            # 2. TP1 hit — normal urgency
            if tp1:
                if direction == "long" and mark_price >= tp1:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "tp1_hit",
                        "urgency": "normal",
                    }
                    self._log.info(
                        "TP1 HIT: %s long mark=%.2f tp1=%.2f",
                        symbol, mark_price, tp1,
                    )
                    return substrate

                if direction == "short" and mark_price <= tp1:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "tp1_hit",
                        "urgency": "normal",
                    }
                    self._log.info(
                        "TP1 HIT: %s short mark=%.2f tp1=%.2f",
                        symbol, mark_price, tp1,
                    )
                    return substrate

            # 3. Trailing stop check (if active)
            trailing_active = pos.get("trailing_active", False)
            trailing_sl = pos.get("trailing_sl")
            if trailing_active and trailing_sl and mark_price:
                if direction == "long" and mark_price <= trailing_sl:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "trailing_stop_hit",
                        "urgency": "immediate",
                    }
                    self._log.info(
                        "TRAILING STOP HIT: %s long mark=%.2f trail_sl=%.2f",
                        symbol, mark_price, trailing_sl,
                    )
                    return substrate

                if direction == "short" and mark_price >= trailing_sl:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "trailing_stop_hit",
                        "urgency": "immediate",
                    }
                    self._log.info(
                        "TRAILING STOP HIT: %s short mark=%.2f trail_sl=%.2f",
                        symbol, mark_price, trailing_sl,
                    )
                    return substrate

            # 4. Signal reversal — low urgency (soft check)
            sym_indicators = indicators.get(symbol, {})
            if sym_indicators:
                reversed_signals = self._check_signal_reversal(
                    pos, sym_indicators, substrate
                )
                if reversed_signals >= soft_exit_requires:
                    substrate.decisions["exit_request"] = {
                        "symbol": symbol,
                        "reason": "signal_reversal",
                        "urgency": "low",
                    }
                    self._log.info(
                        "SIGNAL REVERSAL: %s (%d indicators reversed)",
                        symbol, reversed_signals,
                    )
                    return substrate

        # No exit needed for any position
        return substrate

    def _check_signal_reversal(
        self, position: dict, sym_indicators: dict, substrate: Substrate
    ) -> int:
        """
        Count how many indicators have reversed against the position direction.

        Returns count of reversed indicators.
        """
        direction = position.get("direction", "Long").lower()
        reversed_count = 0

        # Build weight map from config
        indicator_configs = substrate.cfg("indicators", [])
        weight_map = {}
        for ind_cfg in indicator_configs:
            name = ind_cfg.get("name", "")
            weight = ind_cfg.get("weight", 0)
            weight_map[name] = weight

        for tf, tf_inds in sym_indicators.items():
            if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                continue

            # RSI reversal
            rsi = tf_inds.get("rsi", {})
            if isinstance(rsi, dict) and weight_map.get("rsi", 0) > 0:
                rsi_val = rsi.get("value", 50)
                rsi_reversal_low = substrate.cfg("scoring.rsi_signal_low")
                rsi_reversal_high = substrate.cfg("scoring.rsi_signal_high")
                if direction == "long" and rsi_val < rsi_reversal_low:
                    reversed_count += 1
                elif direction == "short" and rsi_val > rsi_reversal_high:
                    reversed_count += 1

            # MACD reversal
            macd = tf_inds.get("macd", {})
            if isinstance(macd, dict) and weight_map.get("macd", 0) > 0:
                bias = macd.get("bias", "")
                if direction == "long" and "bearish" in bias:
                    reversed_count += 1
                elif direction == "short" and "bullish" in bias:
                    reversed_count += 1

            # EMA reversal
            ema = tf_inds.get("ema_stack", {})
            if isinstance(ema, dict) and weight_map.get("ema_stack", 0) > 0:
                alignment = ema.get("alignment", "")
                if direction == "long" and "bearish" in alignment:
                    reversed_count += 1
                elif direction == "short" and "bullish" in alignment:
                    reversed_count += 1

        return reversed_count

    def flux_score(self, substrate: Substrate) -> float:
        """
        Dynamic flux based on exit urgency.

        SL breach or trailing stop = critical (5.0)
        TP hit = important (3.0)
        Signal reversal = low priority (1.0)
        No exit needed = 0.0
        """
        if not self.can_activate(substrate):
            return 0.0

        # Pre-scan positions for urgency signals
        positions = substrate.portfolio.get("open_positions", [])
        near_sl_threshold = substrate.cfg("exit_rules.near_sl_urgency_pct", 0.5)
        for pos in positions:
            mark_price = pos.get("mark_price", 0)
            sl_price = pos.get("sl_price", 0)
            direction = pos.get("direction", "Long").lower()
            trailing_active = pos.get("trailing_active", False)
            trailing_sl = pos.get("trailing_sl")

            # SL breach — critical
            if sl_price and mark_price:
                if direction == "long" and mark_price <= sl_price:
                    return 5.0
                if direction == "short" and mark_price >= sl_price:
                    return 5.0

            # Trailing stop hit — critical
            if trailing_active and trailing_sl and mark_price:
                if direction == "long" and mark_price <= trailing_sl:
                    return 5.0
                if direction == "short" and mark_price >= trailing_sl:
                    return 5.0

            # Near SL (within threshold) — high urgency
            if sl_price and mark_price:
                if direction == "long":
                    dist_pct = (mark_price - sl_price) / mark_price * 100
                else:
                    dist_pct = (sl_price - mark_price) / mark_price * 100
                if dist_pct < near_sl_threshold:
                    return 4.0

            # TP hit — important
            tp1 = pos.get("tp1", 0)
            if tp1 and mark_price:
                if direction == "long" and mark_price >= tp1:
                    return 3.0
                if direction == "short" and mark_price <= tp1:
                    return 3.0

        # Default: position monitoring is important but not urgent
        return 1.5