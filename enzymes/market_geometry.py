"""
enzymes/market_geometry.py -- Sensor enzyme: market structure detection.

Computes swing highs/lows, trend direction, market phase, pullback depth,
and structure break detection from OHLCV price data stored on the substrate
by CollectOHLCV.

Writes to: substrate.market.geometry[symbol] (dict per symbol)

Pipeline position:
  CollectOHLCV → DetectRegime → MarketGeometry → ScoreConfluence → ... → RequestExit

Consumers of MarketGeometry data:
  1. RequestExit — structure-aware exits (immediate exit on structure break)
  2. ApproveExit — progressive trailing stop (tighten trail based on phase/pullback)
  3. ScoreConfluence — conditional scoring modifiers (post-ABC, secondary)

Enzyme class: Sensor
Activates when: market.ohlcv not empty AND market.geometry is empty
"""

from __future__ import annotations

import logging
from typing import Optional

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


# -- Swing detection (module-level for testability) ----------------------------

def detect_swings(highs: list[float], lows: list[float], lookback: int = 5) -> list[dict]:
    """
    Detect swing highs and swing lows from price arrays.

    A swing high: bar where high > high of N bars before AND after.
    A swing low:  bar where low  < low  of N bars before AND after.

    Returns list of swing points ordered by index (ascending):
        [{"type": "high"/"low", "price": float, "index": int}, ...]
    """
    n = len(highs)
    if n < 2 * lookback + 1:
        return []

    swings = []
    for i in range(lookback, n - lookback):
        # Check swing high
        is_high = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and highs[j] >= highs[i]:
                is_high = False
                break
        if is_high:
            swings.append({"type": "high", "price": highs[i], "index": i})

        # Check swing low
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if j != i and lows[j] <= lows[i]:
                is_low = False
                break
        if is_low:
            swings.append({"type": "low", "price": lows[i], "index": i})

    # Sort by index, keep last 6 swing points
    swings.sort(key=lambda s: s["index"])
    return swings[-6:]


def classify_trend_direction(
    swings: list[dict],
    tolerance_pct: float = 0.5,
) -> str:
    """
    Classify trend direction from swing points.

    Bullish: each SH > previous SH AND each SL > previous SL (HH + HL)
    Bearish: each SH < previous SH AND each SL < previous SL (LH + LL)
    Ranging: mixed or insufficient swings

    Tolerance: allow one violation in 4 swings if < tolerance_pct of price.

    Returns: "bullish" | "bearish" | "ranging"
    """
    if len(swings) < 4:
        return "ranging"

    swing_highs = [s for s in swings if s["type"] == "high"]
    swing_lows = [s for s in swings if s["type"] == "low"]

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "ranging"

    # Check bullish: HH + HL
    hh_count = 0
    for i in range(1, len(swing_highs)):
        if swing_highs[i]["price"] > swing_highs[i - 1]["price"]:
            hh_count += 1
        elif abs(swing_highs[i]["price"] - swing_highs[i - 1]["price"]) / swing_highs[i]["price"] * 100 < tolerance_pct:
            hh_count += 1  # within tolerance, count as bullish

    hl_count = 0
    for i in range(1, len(swing_lows)):
        if swing_lows[i]["price"] > swing_lows[i - 1]["price"]:
            hl_count += 1
        elif abs(swing_lows[i]["price"] - swing_lows[i - 1]["price"]) / swing_lows[i]["price"] * 100 < tolerance_pct:
            hl_count += 1  # within tolerance

    bullish_score = hh_count + hl_count

    # Check bearish: LH + LL
    lh_count = 0
    for i in range(1, len(swing_highs)):
        if swing_highs[i]["price"] < swing_highs[i - 1]["price"]:
            lh_count += 1
        elif abs(swing_highs[i]["price"] - swing_highs[i - 1]["price"]) / swing_highs[i]["price"] * 100 < tolerance_pct:
            lh_count += 1

    ll_count = 0
    for i in range(1, len(swing_lows)):
        if swing_lows[i]["price"] < swing_lows[i - 1]["price"]:
            ll_count += 1
        elif abs(swing_lows[i]["price"] - swing_lows[i - 1]["price"]) / swing_lows[i]["price"] * 100 < tolerance_pct:
            ll_count += 1

    bearish_score = lh_count + ll_count

    total_pairs = (len(swing_highs) - 1) + (len(swing_lows) - 1)
    if total_pairs == 0:
        return "ranging"

    # Need > 50% of pairs confirming a direction
    if bullish_score > total_pairs * 0.5 and bullish_score > bearish_score:
        return "bullish"
    if bearish_score > total_pairs * 0.5 and bearish_score > bullish_score:
        return "bearish"
    return "ranging"


def classify_phase(
    trend_direction: str,
    current_price: float,
    swing_highs: list[dict],
    swing_lows: list[dict],
    previous_phase: str = "",
) -> str:
    """
    Classify the current market phase.

    Phase     | Definition                                              |
    ----------|---------------------------------------------------------|
    Impulse   | Price moving in trend direction, making new HH/HL       |
    Pullback  | Price retracing against trend, between last SH and SL   |
    Breakout  | Price breaking above resistance or below support        |
    Range     | No clear HH/HL or LH/LL pattern                        |

    Returns: "impulse" | "pullback" | "breakout" | "range"
    """
    if not swing_highs or not swing_lows:
        return "range"

    last_sh = swing_highs[-1]["price"]
    last_sl = swing_lows[-1]["price"]

    if trend_direction == "bullish":
        if current_price > last_sh:
            # Above last swing high — either impulse or breakout
            if previous_phase in ("range", ""):
                return "breakout"
            return "impulse"
        if current_price >= last_sl:
            # Between last SL and SH — pullback
            return "pullback"
        # Below last swing low — bearish breakout / structure break
        return "breakout"

    if trend_direction == "bearish":
        if current_price < last_sl:
            # Below last swing low — either impulse or breakout
            if previous_phase in ("range", ""):
                return "breakout"
            return "impulse"
        if current_price <= last_sh:
            # Between last SL and SH — pullback
            return "pullback"
        # Above last swing high — bullish breakout / structure break
        return "breakout"

    # Ranging — no clear direction
    return "range"


def compute_pullback_depth(
    trend_direction: str,
    current_price: float,
    swing_highs: list[dict],
    swing_lows: list[dict],
) -> tuple[str, float]:
    """
    Compute pullback depth as retracement percentage of the prior impulse.

    Only applicable when phase == "pullback".

    Classification:
      Shallow:  < 38.2%   → Trend strong
      Moderate: 38.2–61.8% → Classic zone
      Deep:     > 61.8%   → Trend weakening

    Returns: ("shallow"|"moderate"|"deep"|"n/a", retracement_pct)
    """
    if trend_direction == "ranging" or not swing_highs or not swing_lows:
        return "n/a", 0.0

    last_sh = swing_highs[-1]["price"]
    last_sl = swing_lows[-1]["price"]
    impulse_size = last_sh - last_sl

    if impulse_size <= 0:
        return "n/a", 0.0

    if trend_direction == "bullish":
        # Pullback: price has dropped from the swing high
        retracement = (last_sh - current_price) / impulse_size
    else:
        # Bearish: price has risen from the swing low
        retracement = (current_price - last_sl) / impulse_size

    retracement_pct = max(0.0, min(1.0, retracement))

    if retracement_pct < 0.382:
        depth = "shallow"
    elif retracement_pct <= 0.618:
        depth = "moderate"
    else:
        depth = "deep"

    return depth, round(retracement_pct, 4)


def detect_structure_break(
    trend_direction: str,
    current_price: float,
    swing_highs: list[dict],
    swing_lows: list[dict],
) -> bool:
    """
    Detect whether the market structure has broken.

    Bullish structure break: price makes a Lower Low (LL) after HH+HL pattern
    Bearish structure break: price makes a Higher High (HH) after LH+LL pattern

    This is the strongest exit signal — the structural reason for the trade
    is gone.

    Returns: True if structure break detected
    """
    if trend_direction == "ranging" or len(swing_lows) < 2:
        return False

    if trend_direction == "bullish":
        # Bullish structure: HL pattern. Break = price below previous swing low
        # (a Lower Low after Higher Lows)
        if len(swing_lows) >= 2:
            prev_sl = swing_lows[-2]["price"]
            last_sl = swing_lows[-1]["price"]
            # If the most recent swing low is below the previous one = LL
            # AND current price is below the last swing low
            if last_sl < prev_sl and current_price < last_sl:
                return True

    if trend_direction == "bearish":
        # Bearish structure: LH pattern. Break = price above previous swing high
        # (a Higher High after Lower Highs)
        if len(swing_highs) >= 2:
            prev_sh = swing_highs[-2]["price"]
            last_sh = swing_highs[-1]["price"]
            # If the most recent swing high is above the previous one = HH
            # AND current price is above the last swing high
            if last_sh > prev_sh and current_price > last_sh:
                return True

    return False


def compute_nearest_ema(
    current_price: float,
    indicators: dict,
) -> dict:
    """
    Find the nearest EMA to current price from indicator data.

    Returns: {"name": str, "distance_pct": float} or empty dict if no EMA data.
    """
    ema_data = indicators.get("ema_stack", {})
    if not isinstance(ema_data, dict):
        return {}

    nearest = {}
    min_dist = float("inf")

    # EMA stack may contain individual EMA values
    for key in ["ema21", "ema55", "ema200", "EMA21", "EMA55", "EMA200"]:
        val = ema_data.get(key)
        if val is not None and isinstance(val, (int, float)) and val > 0:
            dist_pct = abs(current_price - val) / current_price * 100
            if dist_pct < min_dist:
                min_dist = dist_pct
                nearest = {"name": key.upper(), "distance_pct": round(dist_pct, 3)}

    return nearest


# -- MarketGeometry Enzyme ----------------------------------------------------

@register_enzyme
class MarketGeometry(Enzyme):
    """
    Sensor enzyme: compute market structure from OHLCV price data.

    Reads raw OHLCV arrays from substrate.market.ohlcv (written by
    CollectOHLCV) and computes swing highs/lows, trend direction,
    market phase, pullback depth, and structure break detection.

    Writes to substrate.market.geometry[symbol] — one dict per symbol.

    Pipeline: CollectOHLCV → MarketGeometry → ScoreConfluence → ... → RequestExit
    """

    name = "MarketGeometry"
    enzyme_class = EnzymeClass.SENSOR
    priority = 5

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """Activate when OHLCV data is available and geometry is empty."""
        ohlcv = substrate.market.get("ohlcv", {})
        geometry = substrate.market.get("geometry", {})
        # Activate if we have OHLCV data but haven't computed geometry yet
        if ohlcv and not geometry:
            return True
        # Also activate if geometry exists but OHLCV was updated more recently
        # (i.e., a new candle closed — geometry should be recomputed)
        if ohlcv and geometry:
            # Check if any symbol in ohlcv is missing from geometry
            for symbol in ohlcv:
                if symbol not in geometry:
                    return True
        return False

    def transform(self, substrate: Substrate) -> Substrate:
        """Compute market geometry for all symbols with OHLCV data."""
        ohlcv = substrate.market.get("ohlcv", {})
        existing_geometry = dict(substrate.market.get("geometry", {}))
        timeframe = substrate.strategy.get("timeframe", "1h")

        # Read structure config
        swing_lookback = substrate.cfg("structure.swing_lookback", 5)
        tolerance_pct = substrate.cfg("structure.trend_violation_tolerance_pct", 0.5)

        for symbol, sym_ohlcv in ohlcv.items():
            tf_data = sym_ohlcv.get(timeframe, sym_ohlcv.get(list(sym_ohlcv.keys())[0], {}))
            if not tf_data:
                continue

            highs = tf_data.get("high", [])
            lows = tf_data.get("low", [])
            closes = tf_data.get("close", [])

            if not highs or not lows or not closes or len(highs) < 2 * swing_lookback + 1:
                self._log.debug(
                    "Insufficient OHLCV data for %s (%d bars, need %d)",
                    symbol, len(highs), 2 * swing_lookback + 1,
                )
                continue

            current_price = closes[-1]

            # Step 1: Swing detection
            swings = detect_swings(highs, lows, lookback=swing_lookback)

            if not swings:
                # Not enough swing points — classify as ranging
                existing_geometry[symbol] = {
                    "trend_direction": "ranging",
                    "phase": "range",
                    "previous_phase": "",
                    "pullback_depth": "n/a",
                    "pullback_pct": 0.0,
                    "nearest_ema": {},
                    "structure_break": False,
                    "last_swing_high": 0.0,
                    "last_swing_low": 0.0,
                    "impulse_size_pct": 0.0,
                    "swing_points": [],
                }
                continue

            # Separate swing highs and lows
            swing_highs = [s for s in swings if s["type"] == "high"]
            swing_lows = [s for s in swings if s["type"] == "low"]

            # Step 2: Trend direction
            trend_direction = classify_trend_direction(swings, tolerance_pct)

            # Step 3: Phase classification (need previous phase from existing geometry)
            previous_phase = ""
            if symbol in existing_geometry:
                prev_geom = existing_geometry[symbol]
                if isinstance(prev_geom, dict):
                    previous_phase = prev_geom.get("phase", "")

            phase = classify_phase(
                trend_direction, current_price, swing_highs, swing_lows, previous_phase
            )

            # Step 4: Pullback depth
            pullback_depth, pullback_pct = compute_pullback_depth(
                trend_direction, current_price, swing_highs, swing_lows
            )

            # Step 5: Nearest EMA (from indicators if available)
            indicators = substrate.market.get("indicators", {})
            sym_indicators = indicators.get(symbol, {})
            primary_tf = list(sym_indicators.keys())[0] if sym_indicators else None
            tf_inds = sym_indicators.get(primary_tf, {}) if primary_tf else {}
            nearest_ema = compute_nearest_ema(current_price, tf_inds)

            # Step 6: Structure break detection
            structure_break = detect_structure_break(
                trend_direction, current_price, swing_highs, swing_lows
            )

            # Compute impulse size as percentage of price
            last_sh = swing_highs[-1]["price"] if swing_highs else current_price
            last_sl = swing_lows[-1]["price"] if swing_lows else current_price
            impulse_size_pct = round((last_sh - last_sl) / current_price * 100, 2) if current_price else 0.0

            # Write geometry data for this symbol
            existing_geometry[symbol] = {
                "trend_direction": trend_direction,
                "phase": phase,
                "previous_phase": previous_phase,
                "pullback_depth": pullback_depth,
                "pullback_pct": pullback_pct,
                "nearest_ema": nearest_ema,
                "structure_break": structure_break,
                "last_swing_high": last_sh,
                "last_swing_low": last_sl,
                "impulse_size_pct": impulse_size_pct,
                "swing_points": swings,
            }

            self._log.info(
                "MarketGeometry %s: trend=%s phase=%s pullback=%s(%s) break=%s",
                symbol, trend_direction, phase, pullback_depth,
                f"{pullback_pct:.0%}" if pullback_depth != "n/a" else "n/a",
                structure_break,
            )

        substrate.market["geometry"] = existing_geometry
        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Dynamic flux: important when positions are open (exit decisions depend on geometry)."""
        if not self.can_activate(substrate):
            return 0.0
        positions = substrate.portfolio.get("open_positions", [])
        if positions:
            return 3.0  # Positions exist — geometry needed for exit decisions
        return 1.5  # No positions — useful for entry scoring but less urgent