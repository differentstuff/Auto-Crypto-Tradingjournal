"""
learning/rulebook.py -- Generate a ranked rulebook (max 10 rules) from accuracy data.

Reads from signal_accuracy, combination_accuracy, trajectory_accuracy,
and idle_condition_accuracy. Ranks candidates by statistical weight
(trades * |win_rate - 50|). Returns formatted text and writes to
rulebook_versions table.

Each data source is read independently — if one table is empty or
has a schema issue, the others still contribute rules. This follows
the reaction network principle: no super-tools, modular enzymes.

Contrarian rules:
  A contrarian signal or combination is NOT dropped — it produces a rule
  like "treat macd as anti-signal: invert its contribution when it fires bullish".
  This is actionable information the system uses to improve scoring.

Activation threshold:
  The rulebook is only generated after min_trades_before_adjusting (default 30)
  closed trades. Before that, the system uses the original strategy config
  weights and no rulebook rules.

Connection safety: db_conn() context manager, always closed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)



# ── Should we regenerate? ──────────────────────────────────────────────────

def should_regenerate(
    strategy_name: str,
    strategy_uid: str = "legacy",
    min_trades: int = None,
    retrain_every_n_trades: int = None,
) -> bool:
    """
    Determine whether the rulebook should be regenerated.

    All thresholds are required — they come from the strategy config.
    No hardcoded defaults.

    Conditions:
      1. Total closed trades >= min_trades (threshold)
      2. Enough new trades since last generation (retrain_every_n_trades)

    Args:
        strategy_name:          Strategy to check.
        min_trades:             Minimum total trades before any generation.
        retrain_every_n_trades: How many new trades needed since last version.

    Returns:
        True if regeneration is warranted, False otherwise.
    """
    if min_trades is None or retrain_every_n_trades is None:
        missing = []
        if min_trades is None: missing.append("min_trades")
        if retrain_every_n_trades is None: missing.append("retrain_every_n_trades")
        raise TypeError(
            f"Required parameter(s) not provided to should_regenerate: "
            + ", ".join(missing)
            + ". All thresholds must come from config (learning.*)."
        )

    from core.database import db_conn

    try:
        with db_conn() as conn:
            # Count total closed trades
            total_trades = conn.execute(
                """SELECT COUNT(*) FROM trade_learning
                   WHERE strategy_name = ?
                     AND exit_time IS NOT NULL
                     AND outcome IS NOT NULL""",
                (strategy_name,),
            ).fetchone()[0]

            if total_trades < min_trades:
                return False

            # Find the most recent rulebook version's trade count
            last_version = conn.execute(
                """SELECT trades_recorded_at_generation FROM rulebook_versions
                   WHERE strategy_uid = ?
                   ORDER BY id DESC LIMIT 1""",
                (strategy_uid,),
            ).fetchone()

            if last_version is None:
                # No previous version → generate if above threshold
                return True

            last_trades = last_version["trades_recorded_at_generation"] or 0
            new_trades = total_trades - last_trades

            return new_trades >= retrain_every_n_trades

    except Exception as e:
        _log.error("Failed to check should_regenerate: %s", e, exc_info=True)
        return False


# ── Source readers (modular, independent) ──────────────────────────────────

def _read_combination_accuracy(conn, strategy_uid: str = "legacy") -> List[Dict]:
    """Read combination accuracy candidates. Independent of other sources."""
    candidates = []
    try:
        combo_rows = conn.execute(
            """SELECT combination_name, direction_state, trades, won,
                      win_rate_pct, p_value, significance
               FROM combination_accuracy
               WHERE strategy_uid = ?""",
            (strategy_uid,),
        ).fetchall()

        for row in combo_rows:
            sig = row["significance"]
            if sig in ("insufficient_data", "not_significant"):
                continue

            trades = row["trades"]
            win_rate = row["win_rate_pct"]
            priority = trades * abs(win_rate - 50)

            if sig == "contrarian":
                text = (
                    f"[!] {row['combination_name']} {row['direction_state']}: "
                    f"ANTI-SIGNAL — {win_rate:.0f}% win rate ({row['won']}/{trades}). "
                    f"Invert this combination: when it fires bullish, subtract from long score."
                )
            else:
                text = (
                    f"[x] {row['combination_name']} {row['direction_state']}: "
                    f"{win_rate:.0f}% win rate ({row['won']}/{trades}, "
                    f"p={row['p_value']:.3f})."
                )

            candidates.append({
                "source": "combination",
                "text": text,
                "priority": priority,
            })
    except Exception as e:
        _log.warning("Could not read combination_accuracy: %s", e)

    return candidates


def _read_trajectory_accuracy(conn, strategy_uid: str = "legacy") -> List[Dict]:
    """Read trajectory accuracy candidates. Independent of other sources."""
    candidates = []
    try:
        traj_rows = conn.execute(
            """SELECT trajectory_pattern, trades, won, win_rate_pct, verdict
               FROM trajectory_accuracy
               WHERE strategy_uid = ?""",
            (strategy_uid,),
        ).fetchall()

        for row in traj_rows:
            verdict = row["verdict"]
            if verdict == "insufficient_data":
                continue

            trades = row["trades"]
            win_rate = row["win_rate_pct"]
            priority = trades * abs(win_rate - 50)

            if verdict == "contrarian":
                text = (
                    f"[!] {row['trajectory_pattern']} pattern: "
                    f"ANTI-SIGNAL — {win_rate:.0f}% win ({row['won']}/{trades}). "
                    f"Avoid entries with this trajectory; invert if detected."
                )
            elif verdict == "suppress":
                text = (
                    f"[!] {row['trajectory_pattern']} pattern: "
                    f"{win_rate:.0f}% win ({row['won']}/{trades}). "
                    f"SUPPRESS: reduce position size or skip."
                )
            else:
                text = (
                    f"[x] {row['trajectory_pattern']} pattern: "
                    f"{win_rate:.0f}% win ({row['won']}/{trades})."
                )

            candidates.append({
                "source": "trajectory",
                "text": text,
                "priority": priority,
            })
    except Exception as e:
        _log.warning("Could not read trajectory_accuracy: %s", e)

    return candidates


def _read_signal_accuracy(conn, strategy_uid: str = "legacy") -> List[Dict]:
    """Read signal accuracy candidates. Independent of other sources."""
    candidates = []
    try:
        sig_rows = conn.execute(
            """SELECT indicator_name, total_fired, correct, accuracy_pct, verdict
               FROM signal_accuracy
               WHERE strategy_uid = ?""",
            (strategy_uid,),
        ).fetchall()

        for row in sig_rows:
            verdict = row["verdict"]
            if verdict in ("insufficient_data", "monitor"):
                continue  # monitor = no rule needed, insufficient = no data

            total = row["total_fired"]
            accuracy = row["accuracy_pct"]
            priority = total * abs(accuracy - 50)

            if verdict == "contrarian":
                text = (
                    f"[!] {row['indicator_name']}: ANTI-SIGNAL — "
                    f"{accuracy:.0f}% accuracy ({row['correct']}/{total}). "
                    f"Invert: when it fires bullish, treat as bearish signal."
                )
            elif verdict == "suppress":
                text = (
                    f"[!] {row['indicator_name']}: {accuracy:.0f}% accuracy "
                    f"({row['correct']}/{total}). COIN FLIP — ignore without confirmation."
                )
            elif verdict == "valid":
                text = (
                    f"[x] {row['indicator_name']}: {accuracy:.0f}% accuracy "
                    f"({row['correct']}/{total}). VALID — boost weight."
                )
            else:  # review
                text = (
                    f"[?] {row['indicator_name']}: {accuracy:.0f}% accuracy "
                    f"({row['correct']}/{total}). BORDERLINE — monitor."
                )

            candidates.append({
                "source": "signal",
                "text": text,
                "priority": priority,
            })
    except Exception as e:
        _log.warning("Could not read signal_accuracy: %s", e)

    return candidates


def _read_idle_condition_accuracy(conn, strategy_uid: str = "legacy") -> List[Dict]:
    """Read idle condition accuracy candidates. Independent of other sources."""
    candidates = []
    try:
        idle_rows = conn.execute(
            """SELECT condition_description, idle_cycles,
                      waiting_was_correct_pct, verdict
               FROM idle_condition_accuracy
               WHERE strategy_uid = ?""",
            (strategy_uid,),
        ).fetchall()

        for row in idle_rows:
            verdict = row["verdict"]
            if verdict == "insufficient_data":
                continue

            idle_cycles = row["idle_cycles"]
            correct_pct = row["waiting_was_correct_pct"]
            priority = idle_cycles * correct_pct

            text = (
                f"[!] During {row['condition_description']}: "
                f"waiting was correct {correct_pct:.0f}% of the time "
                f"(n={idle_cycles})."
            )

            candidates.append({
                "source": "idle_condition",
                "text": text,
                "priority": priority,
            })
    except Exception as e:
        _log.warning("Could not read idle_condition_accuracy: %s", e)

    return candidates


# ── Generate rulebook ──────────────────────────────────────────────────────

def generate_rulebook(
    strategy_name: str,
    strategy_uid: str = "legacy",
    max_rules: int = None,
) -> str:
    """
    Generate a ranked rulebook from all accuracy data sources.

    max_rules is required — it comes from the strategy config
    (learning.rulebook_max_rules). No hardcoded default.

    Sources are read independently — if one fails, the others still
    contribute candidates. This follows the reaction network principle
    of modular enzymes (no super-tools).

    Contrarian signals/combinations produce rules that say "invert this signal"
    rather than "ignore this signal". This is the key insight: a reliably wrong
    signal is as valuable as a reliably right one, if you invert its contribution.

    Writes the generated rulebook to rulebook_versions table.

    Args:
        strategy_name: Strategy to generate for.
        max_rules:     Maximum number of rules.

    Returns:
        Rulebook text string. Empty string if no accuracy data exists.
    """
    if max_rules is None:
        raise TypeError(
            "Required parameter 'max_rules' not provided to generate_rulebook. "
            "It must come from config (learning.rulebook_max_rules)."
        )

    from core.database import db_conn

    candidates: List[Dict] = []

    try:
        with db_conn() as conn:
            # Each source is read independently — failures don't abort others
            candidates.extend(_read_combination_accuracy(conn, strategy_uid))
            candidates.extend(_read_trajectory_accuracy(conn, strategy_uid))
            candidates.extend(_read_signal_accuracy(conn, strategy_uid))
            candidates.extend(_read_idle_condition_accuracy(conn, strategy_uid))
    except Exception as e:
        _log.error("Failed to open DB for rulebook generation: %s", e, exc_info=True)
        return ""

    # ── Rank and select top max_rules ──────────────────────────────────────
    candidates.sort(key=lambda c: c["priority"], reverse=True)
    rules = candidates[:max_rules]

    if not rules:
        _log.info("No rulebook candidates for '%s' — all accuracy tables empty or insufficient", strategy_name)
        return ""

    # ── Format rulebook ─────────────────────────────────────────────────────
    rulebook_text = ""
    for i, rule in enumerate(rules):
        rulebook_text += f"Rule {i + 1}: {rule['text']}\n"

    # ── Write to DB ─────────────────────────────────────────────────────────
    version = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    source_counts = {}
    for rule in rules:
        source_counts[rule["source"]] = source_counts.get(rule["source"], 0) + 1

    try:
        with db_conn() as conn:
            # Count total trades at generation time
            total_trades = conn.execute(
                """SELECT COUNT(*) FROM trade_learning
                   WHERE exit_time IS NOT NULL AND outcome IS NOT NULL""",
            ).fetchone()[0]

            conn.execute(
                """INSERT INTO rulebook_versions
                   (strategy_uid, version, rulebook_text, trades_recorded_at_generation, source_counts_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (strategy_uid, version, rulebook_text, total_trades, json.dumps(source_counts)),
            )

        _log.info("Generated rulebook v%s: %d rules from %d sources",
                  version, len(rules), len(source_counts))

    except Exception as e:
        _log.error("Failed to write rulebook to DB: %s", e, exc_info=True)

    return rulebook_text


# ── Read latest rulebook ──────────────────────────────────────────────────

def get_latest_rulebook(strategy_name: str, strategy_uid: str = "legacy") -> Optional[str]:
    """
    Return the most recent rulebook text from the database.

    Returns None if no rulebook versions exist.
    """
    from core.database import db_conn

    try:
        with db_conn() as conn:
            row = conn.execute(
                """SELECT rulebook_text FROM rulebook_versions
                   WHERE strategy_uid = ?
                   ORDER BY id DESC LIMIT 1""",
                (strategy_uid,),
            ).fetchone()

        if row:
            return row["rulebook_text"]
        return None

    except Exception as e:
        _log.error("Failed to read latest rulebook: %s", e, exc_info=True)
        return None