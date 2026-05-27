"""
learning/combination.py -- Pairwise signal combination accuracy with chi-squared test.

For each closed trade, finds all indicator signals that were aligned with the
trade direction, then updates every pairwise combination in combination_accuracy.
A chi-squared test against the 50% null hypothesis determines statistical significance.

Contrarian combinations:
  If a combination (e.g. rsi+macd both bullish) consistently LOSES on long trades
  (win_rate < 30%), it's flagged as 'contrarian' — a reliable anti-combination.
  This is actionable: avoid entries when this combination fires, or use it as
  a contrarian filter.

Connection safety:
  Uses db_conn() context manager. Connection always closed. INSERT OR REPLACE
  ensures idempotent updates (no double-counting on repeated calls).
"""

from __future__ import annotations

import json
import logging
import math
from itertools import combinations
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger(__name__)



# ── Chi-squared significance ───────────────────────────────────────────────

def chi_squared_p_value(won: int, trades: int) -> float:
    """
    Compute the chi-squared p-value against the null hypothesis (win_rate = 50%).

    A low p-value (< 0.05) means the observed win rate is statistically
    different from random — the combination is NOT a coin flip.

    Args:
        won:    Number of winning trades for this combination.
        trades: Total number of trades for this combination.

    Returns:
        p-value (0.0–1.0). Returns 1.0 for 0 trades (no evidence).
        Never raises ZeroDivisionError.
    """
    if trades == 0:
        return 1.0

    if trades < 2:
        # With 1 trade, chi-squared is undefined. Return 1.0 (no evidence).
        return 1.0

    observed_wins = won
    observed_losses = trades - won
    expected_wins = trades * 0.5   # null hypothesis: 50% win rate
    expected_losses = trades * 0.5

    # Guard against zero expected values (only possible when trades == 0, handled above)
    chi2 = (
        (observed_wins - expected_wins) ** 2 / expected_wins
        + (observed_losses - expected_losses) ** 2 / expected_losses
    )

    # Chi-squared survival function with df=1
    # Using the approximation: p = exp(-chi2/2) for large chi2
    # For small chi2, use the exact formula via math
    try:
        # scipy is not always available; use the manual approximation
        # For df=1: p = 2 * (1 - norm_cdf(sqrt(chi2)))
        # Approximation: p ≈ exp(-chi2 / 2) * correction
        # More accurate: use the gamma function
        from math import gamma, exp
        # For chi-squared with df=1, the survival function is:
        # P(X > x) = 2 * (1 - Φ(√x)) where Φ is the standard normal CDF
        # Approximation using the complementary error function:
        # P(X > x) ≈ erfc(sqrt(x/2))
        # Python's math.erfc is available since 3.2
        p = math.erfc(math.sqrt(chi2 / 2))
        return max(0.0, min(1.0, p))
    except Exception:
        # Fallback: simple approximation
        return max(0.0, min(1.0, math.exp(-chi2 / 2)))


# ── Pairwise extraction ────────────────────────────────────────────────────

def extract_aligned_pairs(
    signals: Dict[str, Dict], direction: str = "Long"
) -> List[str]:
    """
    Extract sorted pairwise combination names from signals aligned with direction.

    A signal is "aligned" if its direction matches the trade direction:
      - Long trade: bullish/long signals are aligned
      - Short trade: bearish/short signals are aligned

    Pair names are sorted alphabetically so 'macd+rsi' == 'rsi+macd'.

    Args:
        signals:   Dict of {indicator_name: {"signal": "bullish"/"bearish"/"neutral"}}
        direction: Trade direction ("Long" or "Short").

    Returns:
        List of sorted pair names like ["macd+rsi", "ema_stack+macd"].
        Returns [] if fewer than 2 signals are aligned.
    """
    if not signals:
        return []

    direction_lower = direction.lower()
    aligned_names = []

    for name, data in signals.items():
        if not isinstance(data, dict):
            continue
        signal = data.get("signal", "").lower()
        if not signal or signal == "neutral":
            continue

        # Check alignment
        if direction_lower in ("long",) and signal in ("bullish", "long"):
            aligned_names.append(name)
        elif direction_lower in ("short",) and signal in ("bearish", "short"):
            aligned_names.append(name)

    if len(aligned_names) < 2:
        return []

    # Sort names alphabetically for consistent pair naming
    aligned_names.sort()

    pairs = []
    for a, b in combinations(aligned_names, 2):
        pairs.append(f"{a}+{b}")

    return pairs


# ── Direction state label ──────────────────────────────────────────────────

def _direction_state(signals: Dict[str, Dict], direction: str) -> str:
    """
    Determine the direction_state label for a combination.

    Returns:
        'both_bullish', 'both_bearish', or 'conflicting'.
    """
    direction_lower = direction.lower()
    aligned = []
    opposed = []

    for name, data in signals.items():
        if not isinstance(data, dict):
            continue
        signal = data.get("signal", "").lower()
        if not signal or signal == "neutral":
            continue

        if direction_lower in ("long",) and signal in ("bullish", "long"):
            aligned.append(signal)
        elif direction_lower in ("short",) and signal in ("bearish", "short"):
            aligned.append(signal)
        else:
            opposed.append(signal)

    if len(aligned) >= 2 and len(opposed) == 0:
        return "both_bullish" if direction_lower == "long" else "both_bearish"
    elif len(opposed) > 0 and len(aligned) > 0:
        return "conflicting"
    else:
        return "both_bullish" if direction_lower == "long" else "both_bearish"


# ── Update combination accuracy ────────────────────────────────────────────

def update_combination_accuracy(
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades: int = None,
    significance_level: float = None,
    contrarian_win_rate: float = None,
) -> None:
    """
    Recompute combination_accuracy from all closed trades for the given strategy.

    All thresholds are required — they come from the strategy config.
    None of them have hardcoded defaults; the caller must supply values
    read from substrate.cfg().

    Idempotent: uses INSERT OR REPLACE. Safe to call multiple times.

    Contrarian detection: combinations with win_rate ≤ contrarian_win_rate
    are flagged as 'contrarian' in the significance field, not silently dropped.
    """
    if min_trades is None or significance_level is None or contrarian_win_rate is None:
        missing = []
        if min_trades is None: missing.append("min_trades")
        if significance_level is None: missing.append("significance_level")
        if contrarian_win_rate is None: missing.append("contrarian_win_rate")
        raise TypeError(
            f"Required parameter(s) not provided to update_combination_accuracy: "
            + ", ".join(missing)
            + ". All learning thresholds must come from config (learning.*)."
        )

    from core.database import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT id, direction, outcome, signals_at_entry_json
                   FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL
                     AND signals_at_entry_json IS NOT NULL
                     AND signals_at_entry_json != ''""",
                (strategy_name,),
            ).fetchall()

            if not rows:
                _log.debug("No closed trades with signals for strategy '%s'", strategy_name)
                return

            # Aggregate per-combination stats
            combo_stats: Dict[str, Dict] = {}  # "name|state" → {trades, won, pnl_sum}

            for row in rows:
                direction = row["direction"] or ""
                outcome = row["outcome"].lower() if row["outcome"] else ""

                if not direction or not outcome:
                    continue

                try:
                    signals = json.loads(row["signals_at_entry_json"])
                except (json.JSONDecodeError, TypeError):
                    continue

                if not isinstance(signals, dict):
                    continue

                trade_won = outcome in ("win", "won")
                pairs = extract_aligned_pairs(signals, direction)
                dir_state = _direction_state(signals, direction)

                for pair_name in pairs:
                    key = f"{pair_name}|{dir_state}"
                    if key not in combo_stats:
                        combo_stats[key] = {"trades": 0, "won": 0, "pnl_sum": 0.0}

                    combo_stats[key]["trades"] += 1
                    if trade_won:
                        combo_stats[key]["won"] += 1

            # Write to combination_accuracy table
            for key, data in combo_stats.items():
                pair_name, dir_state = key.split("|", 1)
                trades = data["trades"]
                won = data["won"]
                win_rate_pct = (won / trades * 100) if trades > 0 else 0.0
                avg_pnl_pct = data["pnl_sum"] / trades if trades > 0 else 0.0

                p_value = chi_squared_p_value(won, trades)

                # Determine significance
                if trades < min_trades:
                    significance = "insufficient_data"
                elif win_rate_pct <= contrarian_win_rate:
                    # Contrarian anti-combination: reliably loses
                    significance = "contrarian"
                elif p_value < significance_level:
                    significance = "significant"
                else:
                    significance = "not_significant"

                conn.execute(
                    """INSERT OR REPLACE INTO combination_accuracy
                       (strategy_uid, combination_name, direction_state, trades, won,
                        win_rate_pct, avg_pnl_pct, p_value, significance)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_uid, pair_name, dir_state, trades, won,
                     win_rate_pct, avg_pnl_pct, p_value, significance),
                )

            _log.info(
                "Updated combination accuracy for '%s': %d combinations",
                strategy_name, len(combo_stats),
            )

    except Exception as e:
        _log.error("Failed to update combination accuracy for '%s': %s", strategy_name, e, exc_info=True)


# ── Read significant combinations ──────────────────────────────────────────

def get_significant_combinations(strategy_name: str, strategy_uid: str = "legacy") -> List[Dict]:
    """
    Return combinations with significance='significant' or 'contrarian'.

    Each dict has: combination_name, direction_state, win_rate_pct, p_value, significance.
    """
    from core.database import db_conn

    try:
        with db_conn() as conn:
            rows = conn.execute(
                """SELECT combination_name, direction_state, win_rate_pct,
                          p_value, significance, trades, won
                   FROM combination_accuracy
                   WHERE strategy_uid = ?
                     AND significance IN ('significant', 'contrarian')""",
                (strategy_uid,),
            ).fetchall()

        return [
            {
                "combination_name": row["combination_name"],
                "direction_state": row["direction_state"],
                "win_rate_pct": row["win_rate_pct"],
                "p_value": row["p_value"],
                "significance": row["significance"],
                "trades": row["trades"],
                "won": row["won"],
            }
            for row in rows
        ]

    except Exception as e:
        _log.error("Failed to read significant combinations: %s", e, exc_info=True)
        return []