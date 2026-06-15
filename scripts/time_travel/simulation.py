"""
scripts/time_travel/simulation.py -- Exit simulation, position sizing, per-trade dollar math.

Contains:
  - simulate_exit(): Walk-forward exit simulation (SL, TP, trailing stop)
  - TradeCooldown: Prevents re-entering the same signal on consecutive bars
  - compute_backtest_trade(): Per-trade position sizing + P&L for backtest
  - TradeResult: Per-trade result dataclass

Uses core.position_sizing for position sizing and P&L math.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from core.position_sizing import (
    compute_net_pnl,
    compute_pnl,
    compute_size,
    kelly_fraction,
)

_log = logging.getLogger("time_travel.simulation")


@dataclass
class TradeResult:
    """Per-trade result from backtest simulation."""
    symbol: str
    direction: str
    threshold: float
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl_pct: float
    duration_bars: int
    sl_hit: int
    trailing_stop_hit: int
    mfe_pct: float
    mae_pct: float
    confluence_score: float
    indicators_aligned: int
    # Dollar-math fields
    position_size_usd: float = 0.0
    gross_pnl_usd: float = 0.0
    net_pnl_usd: float = 0.0
    entry_fee_usd: float = 0.0
    exit_fee_usd: float = 0.0
    total_fees_usd: float = 0.0
    is_winner: bool = False
    atr_cap_applied: bool = False


def compute_backtest_trade(
    entry_price: float,
    exit_price: float,
    direction: str,
    sl_price: float,
    atr_value: float,
    confluence_score: float,
    equity: float,
    leverage: int,
    risk_per_trade_pct: float,
    max_size_pct: float,
    min_size_pct: float,
    atr_cap_pct: float,
    kelly_min: float,
    kelly_max: float,
    wr_base: float,
    wr_range: float,
    avg_win_r: float,
    fee_rate: float,
    exit_reason: str,
    pnl_pct: float,
    duration_bars: int,
    sl_hit: int,
    trailing_stop_hit: int,
    mfe_pct: float,
    mae_pct: float,
    symbol: str = "",
    threshold: float = 0.0,
    indicators_aligned: int = 0,
) -> Optional[TradeResult]:
    """Compute position sizing and dollar P&L for a single backtest trade.

    Uses the same position sizing logic as live trading (core.position_sizing)
    to ensure backtest results are comparable with live results.
    """
    # Compute Kelly fraction from confluence score
    kf = kelly_fraction(
        score=abs(confluence_score),
        kelly_min=kelly_min,
        kelly_max=kelly_max,
        wr_base=wr_base,
        wr_range=wr_range,
        avg_win_r=avg_win_r,
    )

    # Compute position size using the same logic as live trading
    sizing = compute_size(
        equity=equity,
        entry_price=entry_price,
        sl_price=sl_price,
        direction=direction,
        kelly_frac=kf,
        leverage=leverage,
        risk_per_trade_pct=risk_per_trade_pct,
        max_size_pct=max_size_pct,
        min_size_pct=min_size_pct,
        atr_value=atr_value,
        atr_cap_pct=atr_cap_pct,
    )

    position_size = sizing["size_usdt"]
    if position_size <= 0:
        return None

    # Compute gross P&L
    gross = compute_pnl(entry_price, exit_price, direction, position_size)

    # Compute net P&L (after simulated fees)
    net = compute_net_pnl(gross["pnl_usdt"], position_size, fee_rate)

    return TradeResult(
        symbol=symbol,
        direction=direction,
        threshold=threshold,
        entry_price=entry_price,
        exit_price=exit_price,
        exit_reason=exit_reason,
        pnl_pct=pnl_pct,
        duration_bars=duration_bars,
        sl_hit=sl_hit,
        trailing_stop_hit=trailing_stop_hit,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        confluence_score=confluence_score,
        indicators_aligned=indicators_aligned,
        position_size_usd=position_size,
        gross_pnl_usd=gross["pnl_usdt"],
        net_pnl_usd=net["net_pnl_usdt"],
        entry_fee_usd=net["entry_fee_usdt"],
        exit_fee_usd=net["exit_fee_usdt"],
        total_fees_usd=net["total_fees_usdt"],
        is_winner=net["net_pnl_usdt"] > 0,
        atr_cap_applied=sizing["atr_cap_applied"],
    )


# ── Exit simulation ─────────────────────────────────────────────────────────


def simulate_exit(
    df: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    direction: str,
    atr_value: float,
    config: dict,
) -> Optional[dict]:
    """Walk forward from entry to determine exit.

    Uses ATR-based SL/TP from config exit_rules.
    Returns dict with exit info or None if no exit found within data range.

    IMPORTANT: The returned dict now includes 'sl_price' for position sizing.
    """
    exit_rules = config.get("exit_rules", {})
    hard_stop = exit_rules.get("hard_stop", {})
    trailing_stop = exit_rules.get("trailing_stop", {})

    atr_mult = hard_stop.get("width_atr_multiplier", 1.5)
    rr_minimum = config.get("scoring", {}).get("rr_minimum", 2.0)

    is_long = direction.lower() in ("long", "buy")

    # SL distance in price
    sl_distance = atr_value * atr_mult
    # TP distance = SL * RR ratio
    tp_distance = sl_distance * rr_minimum

    if is_long:
        sl_price = entry_price - sl_distance
        tp_price = entry_price + tp_distance
    else:
        sl_price = entry_price + sl_distance
        tp_price = entry_price - tp_distance

    # Trailing stop parameters
    trail_enabled = trailing_stop.get("enabled", True)
    trail_activation_pct = trailing_stop.get("activation_profit_pct", 0.5) / 100
    trail_atr_mult = trailing_stop.get("trail_atr_multiplier", 1.0)
    breakeven_at_activation = trailing_stop.get("breakeven_at_activation", True)

    # Walk forward
    highest_favorable = entry_price
    lowest_adverse = entry_price
    trail_activated = False
    trail_price = sl_price

    max_bars = len(df) - entry_idx - 1
    max_bars = min(max_bars, 200)

    for i in range(1, max_bars + 1):
        bar_idx = entry_idx + i
        bar = df.iloc[bar_idx]

        high = float(bar["high"])
        low = float(bar["low"])

        if is_long:
            highest_favorable = max(highest_favorable, high)

            if trail_enabled and not trail_activated:
                if high >= entry_price * (1 + trail_activation_pct):
                    trail_activated = True
                    trail_price = max(sl_price, entry_price if breakeven_at_activation else sl_price)

            if trail_activated:
                new_trail = high - atr_value * trail_atr_mult
                trail_price = max(trail_price, new_trail)

            if low <= sl_price:
                exit_price = sl_price
                exit_reason = "hard_stop"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 1,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((highest_favorable - entry_price) / entry_price) * 100, 3),
                    "mae_pct": round(((low - entry_price) / entry_price) * 100, 3) if low < entry_price else 0.0,
                }

            if trail_activated and low <= trail_price:
                exit_price = trail_price
                exit_reason = "trailing_stop"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 1,
                    "mfe_pct": round(((highest_favorable - entry_price) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

            if high >= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((high - entry_price) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

        else:  # Short
            lowest_adverse = min(lowest_adverse, low)

            if trail_enabled and not trail_activated:
                if low <= entry_price * (1 - trail_activation_pct):
                    trail_activated = True
                    trail_price = min(sl_price, entry_price if breakeven_at_activation else sl_price)

            if trail_activated:
                new_trail = low + atr_value * trail_atr_mult
                trail_price = min(trail_price, new_trail)

            if high >= sl_price:
                exit_price = sl_price
                exit_reason = "hard_stop"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 1,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((entry_price - lowest_adverse) / entry_price) * 100, 3),
                    "mae_pct": round(((high - entry_price) / entry_price) * 100, 3) if high > entry_price else 0.0,
                }

            if trail_activated and high >= trail_price:
                exit_price = trail_price
                exit_reason = "trailing_stop"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 1,
                    "mfe_pct": round(((entry_price - lowest_adverse) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

            if low <= tp_price:
                exit_price = tp_price
                exit_reason = "take_profit"
                pnl_pct = ((entry_price - exit_price) / entry_price) * 100
                return {
                    "exit_bar": bar_idx,
                    "exit_price": exit_price,
                    "sl_price": sl_price,
                    "exit_reason": exit_reason,
                    "pnl_pct": round(pnl_pct, 3),
                    "duration_bars": i,
                    "sl_hit": 0,
                    "trailing_stop_hit": 0,
                    "mfe_pct": round(((entry_price - low) / entry_price) * 100, 3),
                    "mae_pct": 0.0,
                }

    # No exit found within data range
    return None


# ── Trade deduplication ─────────────────────────────────────────────────────


class TradeCooldown:
    """Prevent re-entering the same signal at the same threshold."""

    def __init__(self, cooldown_bars: int = 3):
        self._cooldown_bars = cooldown_bars
        self._active: Dict[str, Dict[float, dict]] = {}

    def can_enter(self, symbol: str, threshold: float, direction: str, bar_idx: int,
                  current_score: float) -> bool:
        sym = self._active.get(symbol, {})
        entry = sym.get(threshold)
        if entry is None:
            return True
        if entry["direction"] != direction:
            return True
        if abs(current_score) < threshold:
            return True
        if bar_idx - entry["bar_idx"] >= self._cooldown_bars:
            return True
        return False

    def record_entry(self, symbol: str, threshold: float, direction: str, bar_idx: int):
        if symbol not in self._active:
            self._active[symbol] = {}
        self._active[symbol][threshold] = {
            "direction": direction,
            "bar_idx": bar_idx,
        }