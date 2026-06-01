"""
learning/hypothetical_tracker.py -- Full paper-trade tracker for the challenger branch.

Runs independently alongside production: scores with challenger weights,
makes its own entry/exit decisions, and tracks hypothetical positions.

The challenger may enter when production doesn't (and vice versa) because
different weights can cross the entry threshold differently. This captures
all four scenarios for an unbiased comparison.

Exit logic mirrors RequestExit + ApproveExit exactly:
  1. Hard SL breach
  2. TP1 hit
  3. Trailing stop hit
  4. Signal reversal
No time-based expiry — the market decides.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


class HypotheticalTracker:
    """Manages hypothetical positions for the challenger paper-trading branch.

    Each cycle, the tracker:
      1. Checks exit conditions for all open hypothetical positions
      2. Scores candidates with challenger weights for new entries
      3. Opens new hypothetical positions where challenger crosses threshold
      4. Records all events to challenger_log
    """

    @staticmethod
    def run_cycle(substrate: Any, challenger_weights: Dict[str, float]) -> None:
        """Run one challenger cycle: check exits, then check entries."""
        challenger = substrate.learning.setdefault("challenger", {})
        positions = challenger.get("positions", [])

        # Step 1: Check exits for open hypothetical positions
        positions = HypotheticalTracker._check_exits(substrate, positions)

        # Step 2: Score candidates with challenger weights and check entries
        positions = HypotheticalTracker._check_entries(substrate, challenger_weights, positions)

        challenger["positions"] = positions

    @staticmethod
    def get_closed_trades(strategy_uid: str) -> List[Dict]:
        """Retrieve closed hypothetical trades from challenger_log."""
        try:
            from core.database import db_conn
            with db_conn() as conn:
                rows = conn.execute(
                    """SELECT symbol, exit_pnl_pct, exit_reason, entry_score
                       FROM challenger_log
                       WHERE strategy_uid = ?
                         AND event_type = 'hypothetical_exit'
                       ORDER BY id""",
                    (strategy_uid,),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            _log.error("Failed to read closed hypothetical trades: %s", e, exc_info=True)
            return []

    # ── Exit logic (mirrors RequestExit + ApproveExit) ────────────────────────

    @staticmethod
    def _check_exits(substrate: Any, positions: List[Dict]) -> List[Dict]:
        """Check exit conditions for all open hypothetical positions.

        Mirrors the same four checks as RequestExit + ApproveExit:
          1. Hard SL breach → immediate exit
          2. TP1 hit → normal exit
          3. Trailing stop hit → immediate exit
          4. Signal reversal → low urgency exit
        No time-based expiry.
        """
        remaining = []
        indicators = substrate.market.get("indicators", {})

        for pos in positions:
            symbol = pos.get("symbol", "")
            direction = pos.get("direction", "Long").lower()
            entry_price = pos.get("entry_price", 0)
            sl_price = pos.get("sl_price", 0)
            tp1 = pos.get("tp1", 0)
            mark_price = pos.get("mark_price", 0)

            # Update mark price from current market data
            last_prices = substrate.market.get("last_prices", {})
            if symbol in last_prices:
                mark_price = last_prices[symbol]
                pos["mark_price"] = mark_price

            if not mark_price:
                remaining.append(pos)
                continue

            # Update trailing stop state
            pos = _update_trailing_stop(pos, substrate)

            should_exit = False
            exit_reason = ""

            # 1. Hard SL breach
            if sl_price:
                if direction == "long" and mark_price <= sl_price:
                    should_exit = True
                    exit_reason = "hard_sl_breach"
                elif direction == "short" and mark_price >= sl_price:
                    should_exit = True
                    exit_reason = "hard_sl_breach"

            # 2. TP1 hit
            if not should_exit and tp1:
                if direction == "long" and mark_price >= tp1:
                    should_exit = True
                    exit_reason = "tp1_hit"
                elif direction == "short" and mark_price <= tp1:
                    should_exit = True
                    exit_reason = "tp1_hit"

            # 3. Trailing stop hit
            if not should_exit:
                trailing_active = pos.get("trailing_active", False)
                trailing_sl = pos.get("trailing_sl")
                if trailing_active and trailing_sl:
                    if direction == "long" and mark_price <= trailing_sl:
                        should_exit = True
                        exit_reason = "trailing_stop_hit"
                    elif direction == "short" and mark_price >= trailing_sl:
                        should_exit = True
                        exit_reason = "trailing_stop_hit"

            # 4. Signal reversal
            if not should_exit:
                sym_indicators = indicators.get(symbol, {})
                if sym_indicators:
                    reversed_count = _count_reversed_signals(pos, sym_indicators, substrate)
                    soft_exit_requires = substrate.cfg("exit_rules.soft_exit.requires_indicators_reversed")
                    if reversed_count >= soft_exit_requires:
                        should_exit = True
                        exit_reason = "signal_reversal"

            if should_exit:
                HypotheticalTracker._close_position(substrate, pos, exit_reason, mark_price)
            else:
                remaining.append(pos)

        return remaining

    @staticmethod
    def _check_entries(
        substrate: Any,
        challenger_weights: Dict[str, float],
        positions: List[Dict],
    ) -> List[Dict]:
        """Score all symbols with challenger weights and open hypothetical entries."""
        from enzymes.score_confluence import ScoreConfluence

        indicators = substrate.market.get("indicators", {})
        if not indicators:
            return positions

        entry_threshold = substrate.cfg("scoring.entry_threshold")
        confluence_min = substrate.cfg("scoring.confluence_min_signals")
        rsi_high = substrate.cfg("scoring.rsi_signal_high")
        rsi_low = substrate.cfg("scoring.rsi_signal_low")
        momentum_cap = substrate.cfg("scoring.momentum_cap")
        momentum_dampening = substrate.cfg("scoring.momentum_dampening")
        modifier_weights = substrate.cfg("scoring.modifier_weights")
        formula = substrate.cfg("scoring.formula")
        min_candidate_pct = substrate.cfg("scoring.min_candidate_pct")

        open_symbols = {p.get("symbol") for p in positions}

        scorer = ScoreConfluence()

        for symbol, sym_data in indicators.items():
            if symbol in open_symbols:
                continue

            total_score = 0.0
            total_max = 0.0
            indicators_aligned = 0

            for tf, tf_inds in sym_data.items():
                if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
                    continue

                tf_score, tf_max, _ = scorer._score_timeframe(
                    tf_inds, challenger_weights, rsi_high, rsi_low,
                    momentum_cap, momentum_dampening, modifier_weights, formula,
                )
                total_score += tf_score
                total_max += tf_max

                for name, w in challenger_weights.items():
                    if w > 0 and name in tf_inds:
                        ind = tf_inds[name]
                        if isinstance(ind, dict):
                            signal = ind.get("signal", ind.get("bias", ind.get("level", "")))
                            if signal and signal not in ("neutral", "mixed", ""):
                                indicators_aligned += 1

            pct = total_score / total_max if total_max else 0.0
            normalized_score = (total_score / total_max * 10) if total_max else 0.0

            if indicators_aligned < confluence_min and abs(pct) < min_candidate_pct:
                continue

            if abs(normalized_score) >= entry_threshold:
                direction = "Long" if normalized_score > 0 else "Short"
                pos = HypotheticalTracker._open_position(
                    substrate, symbol, direction, normalized_score, challenger_weights,
                )
                if pos:
                    positions.append(pos)

        return positions

    @staticmethod
    def _open_position(
        substrate: Any,
        symbol: str,
        direction: str,
        score: float,
        challenger_weights: Dict[str, float],
    ) -> Optional[Dict]:
        """Open a hypothetical position with SL/TP calculated from current market data."""
        last_prices = substrate.market.get("last_prices", {})
        entry_price = last_prices.get(symbol, 0)
        if not entry_price:
            return None

        atr_value = _get_atr_for_symbol(substrate, symbol)
        sl_width_mult = substrate.cfg("exit_rules.hard_stop.width_atr_multiplier")

        if atr_value and atr_value > 0:
            sl_distance = atr_value * sl_width_mult
        else:
            sl_distance = entry_price * 0.02  # 2% fallback

        if direction.lower() == "long":
            sl_price = round(entry_price - sl_distance, 6)
            tp1 = round(entry_price + sl_distance * substrate.cfg("scoring.rr_minimum"), 6)
        else:
            sl_price = round(entry_price + sl_distance, 6)
            tp1 = round(entry_price - sl_distance * substrate.cfg("scoring.rr_minimum"), 6)

        signal_states = _capture_signal_states(substrate, symbol)

        pos = {
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "sl_price": sl_price,
            "tp1": tp1,
            "entry_score": round(score, 2),
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "mark_price": entry_price,
            "atr_value": atr_value,
            "trailing_active": False,
            "trailing_sl": None,
            "peak_price": entry_price,
            "signal_states": signal_states,
        }

        _log.info(
            "Hypothetical ENTRY: %s %s score=%.2f sl=%.2f tp1=%.2f",
            direction, symbol, score, sl_price, tp1,
        )

        from learning.challenger import _log_challenger_event
        _log_challenger_event(
            substrate, "hypothetical_entry",
            source=substrate.learning.get("challenger", {}).get("source", ""),
            symbol=symbol,
            entry_score=score,
            signal_states=signal_states,
        )

        return pos

    @staticmethod
    def _close_position(
        substrate: Any,
        pos: Dict,
        exit_reason: str,
        mark_price: float,
    ) -> None:
        """Close a hypothetical position and record the outcome."""
        entry_price = pos.get("entry_price", 0)
        direction = pos.get("direction", "Long").lower()

        if direction == "long":
            pnl_pct = ((mark_price - entry_price) / entry_price) * 100 if entry_price else 0
        else:
            pnl_pct = ((entry_price - mark_price) / entry_price) * 100 if entry_price else 0

        challenger = substrate.learning.get("challenger", {})
        challenger["trade_count"] = challenger.get("trade_count", 0) + 1

        _log.info(
            "Hypothetical EXIT: %s %s reason=%s pnl=%.2f%%",
            pos.get("direction", "?"), pos.get("symbol", "?"),
            exit_reason, pnl_pct,
        )

        from learning.challenger import _log_challenger_event
        _log_challenger_event(
            substrate, "hypothetical_exit",
            source=challenger.get("source", ""),
            symbol=pos.get("symbol", ""),
            exit_pnl_pct=round(pnl_pct, 3),
            exit_reason=exit_reason,
            trade_count=challenger.get("trade_count", 0),
        )


# ── Trailing stop logic (mirrors ApproveExit._update_trailing_stop) ──────────

def _update_trailing_stop(position: Dict, substrate: Any) -> Dict:
    """Update trailing stop state for a hypothetical position.

    Returns a NEW position dict with updated trailing stop fields.
    Does NOT mutate the original position dict.
    """
    entry_price = position.get("entry_price", 0)
    mark_price = position.get("mark_price", 0)
    direction = position.get("direction", "Long").lower()
    atr_value = position.get("atr_value", 0)

    if not entry_price or not mark_price:
        return position

    trailing_enabled = substrate.cfg("exit_rules.trailing_stop.enabled")
    if not trailing_enabled:
        return position

    activation_pct = substrate.cfg("exit_rules.trailing_stop.activation_profit_pct")
    trail_atr_mult = substrate.cfg("exit_rules.trailing_stop.trail_atr_multiplier")
    breakeven_on_activate = substrate.cfg("exit_rules.trailing_stop.breakeven_at_activation")

    if direction == "long":
        profit_pct = ((mark_price - entry_price) / entry_price) * 100
    else:
        profit_pct = ((entry_price - mark_price) / entry_price) * 100

    trailing_active = position.get("trailing_active", False)
    trailing_sl = position.get("trailing_sl")
    peak_price = position.get("peak_price", mark_price)

    # Update peak price
    if direction == "long":
        if mark_price > peak_price:
            peak_price = mark_price
    else:
        if mark_price < peak_price:
            peak_price = mark_price

    # Activate trailing if not yet active
    if not trailing_active:
        if profit_pct >= activation_pct:
            trailing_active = True
            if breakeven_on_activate:
                trailing_sl = entry_price
            elif atr_value:
                if direction == "long":
                    trailing_sl = mark_price - atr_value * trail_atr_mult
                else:
                    trailing_sl = mark_price + atr_value * trail_atr_mult
            return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl, "peak_price": peak_price}
        return {**position, "peak_price": peak_price}

    # Trailing is active — update trailing_sl
    if direction == "long":
        if atr_value:
            new_sl = mark_price - atr_value * trail_atr_mult
        else:
            new_sl = entry_price
        if trailing_sl is None or new_sl > trailing_sl:
            trailing_sl = new_sl
    else:
        if atr_value:
            new_sl = mark_price + atr_value * trail_atr_mult
        else:
            new_sl = entry_price
        if trailing_sl is None or new_sl < trailing_sl:
            trailing_sl = new_sl

    return {**position, "trailing_active": trailing_active, "trailing_sl": trailing_sl, "peak_price": peak_price}


# ── Signal reversal check (mirrors RequestExit._check_signal_reversal) ────────

def _count_reversed_signals(position: Dict, sym_indicators: Dict, substrate: Any) -> int:
    """Count how many indicators have reversed against the position direction."""
    direction = position.get("direction", "Long").lower()
    reversed_count = 0

    indicator_configs = substrate.cfg("indicators", [])
    weight_map = {cfg.get("name", ""): cfg.get("weight", 0) for cfg in indicator_configs}

    for tf, tf_inds in sym_indicators.items():
        if not isinstance(tf_inds, dict) or not tf_inds.get("ok"):
            continue

        rsi = tf_inds.get("rsi", {})
        if isinstance(rsi, dict) and weight_map.get("rsi", 0) > 0:
            rsi_val = rsi.get("value", 50)
            rsi_reversal_low = substrate.cfg("scoring.rsi_signal_low")
            rsi_reversal_high = substrate.cfg("scoring.rsi_signal_high")
            if direction == "long" and rsi_val < rsi_reversal_low:
                reversed_count += 1
            elif direction == "short" and rsi_val > rsi_reversal_high:
                reversed_count += 1

        macd = tf_inds.get("macd", {})
        if isinstance(macd, dict) and weight_map.get("macd", 0) > 0:
            bias = macd.get("bias", "")
            if direction == "long" and "bearish" in bias:
                reversed_count += 1
            elif direction == "short" and "bullish" in bias:
                reversed_count += 1

        ema = tf_inds.get("ema_stack", {})
        if isinstance(ema, dict) and weight_map.get("ema_stack", 0) > 0:
            alignment = ema.get("alignment", "")
            if direction == "long" and "bearish" in alignment:
                reversed_count += 1
            elif direction == "short" and "bullish" in alignment:
                reversed_count += 1

    return reversed_count


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_atr_for_symbol(substrate: Any, symbol: str) -> float:
    """Extract ATR value from substrate indicators for a given symbol."""
    indicators = substrate.market.get("indicators", {})
    sym_data = indicators.get(symbol, {})
    for tf, tf_inds in sym_data.items():
        if isinstance(tf_inds, dict) and tf_inds.get("ok"):
            atr = tf_inds.get("atr", {})
            if isinstance(atr, dict):
                return atr.get("value", 0.0)
    return 0.0


def _capture_signal_states(substrate: Any, symbol: str) -> Dict:
    """Capture current indicator signal states for a symbol at entry time."""
    indicators = substrate.market.get("indicators", {})
    sym_data = indicators.get(symbol, {})
    states = {}
    for tf, tf_inds in sym_data.items():
        if isinstance(tf_inds, dict) and tf_inds.get("ok"):
            for name, val in tf_inds.items():
                if name in ("ok",) or not isinstance(val, dict):
                    continue
                states[f"{tf}.{name}"] = val.get("signal", val.get("bias", val.get("level", "")))
    return states