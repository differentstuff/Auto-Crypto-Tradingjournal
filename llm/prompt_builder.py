""" llm/prompt_builder.py -- Dynamic context assembler for LLM prompts.

Single source of truth for assembling the context block that accompanies
any LLM call. Enforces a hard character budget so no single call can
balloon indefinitely. Sections are added in priority order — when the
budget is exhausted, lower-priority sections are dropped entirely.

Priority order (configurable via llm.prompt_priority_order):
  1. strategy_description  -- what this strategy does
  2. rulebook              -- personalised warnings/strengths from learning
  3. signal_states         -- current indicator signals for candidates
  4. pre_trade_context     -- trajectory analysis for candidates
  5. similar_trades        -- recent history for this symbol + direction

Each section is a pure function that takes the substrate dict and returns
a string (or empty string if no data is available). The builder respects
section boundaries when truncating — it never cuts mid-section.

Port of: prompt_builder.py (rewritten for new architecture, no legacy imports)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

_log = logging.getLogger(__name__)

# Default priority order (overridden by llm.prompt_priority_order in config)
DEFAULT_PRIORITY_ORDER = [
    "strategy_description",
    "rulebook",
    "signal_states",
    "pre_trade_context",
    "similar_trades",
]


# ---------------------------------------------------------------------------
# Section builders (pure functions)
# ---------------------------------------------------------------------------

def _section_strategy_description(substrate: dict) -> str:
    """Build the strategy description section."""
    strategy = substrate.get("strategy", {})
    name = strategy.get("name", "")
    description = strategy.get("description", "")
    timeframe = strategy.get("timeframe", "")

    if not name and not description:
        return ""

    parts = []
    if name:
        parts.append(f"STRATEGY: {name}")
    if timeframe:
        parts.append(f"Timeframe: {timeframe}")
    if description:
        parts.append(description)

    return "\n".join(parts)


def _section_rulebook(substrate: dict) -> str:
    """Build the rulebook section from substrate.learning['rulebook']."""
    learning = substrate.get("learning", {})
    rulebook = learning.get("rulebook", "")

    if not rulebook:
        return ""

    return f"RULEBOOK:\n{rulebook}"


def _section_signal_states(substrate: dict) -> str:
    """Build the signal states section from substrate.analysis['candidates']."""
    analysis = substrate.get("analysis", {})
    candidates = analysis.get("candidates", [])

    if not candidates:
        return ""

    lines = ["SIGNAL STATES:"]
    for cand in candidates:
        symbol = cand.get("symbol", "?")
        score = cand.get("score", 0)
        label = cand.get("label", "")
        direction = "Long" if score > 0 else "Short" if score < 0 else "Neutral"
        lines.append(f"  {symbol}: {direction} (score={score:+.1f}, {label})")

        # Include individual signal states if present
        signals = cand.get("signals", {})
        if signals:
            for sig_name, sig_data in signals.items():
                if isinstance(sig_data, dict):
                    sig_signal = sig_data.get("signal", "?")
                    sig_strength = sig_data.get("strength", "?")
                    lines.append(f"    {sig_name}: {sig_signal} (strength={sig_strength})")

    return "\n".join(lines)


def _section_pre_trade_context(substrate: dict) -> str:
    """Build the pre-trade trajectory context section."""
    analysis = substrate.get("analysis", {})
    trajectory = analysis.get("pre_trade_trajectory", {})

    if not trajectory:
        return ""

    pattern = trajectory.get("pattern", "")
    risk = trajectory.get("coincidence_risk", "")
    consistent_ratio = trajectory.get("consistent_ratio", "")

    if not pattern:
        return ""

    parts = [f"PRE-TRADE TRAJECTORY: {pattern}"]
    if risk:
        parts.append(f"  Coincidence risk: {risk}")
    if consistent_ratio:
        parts.append(f"  Consistency ratio: {consistent_ratio:.2f}" if isinstance(consistent_ratio, float) else f"  Consistency ratio: {consistent_ratio}")

    return "\n".join(parts)


def _section_similar_trades(substrate: dict) -> str:
    """Build the similar trades section from substrate.analysis."""
    analysis = substrate.get("analysis", {})
    similar = analysis.get("similar_trades", [])

    if not similar:
        return ""

    lines = ["SIMILAR TRADES:"]
    for trade in similar[:5]:  # cap at 5 recent trades
        symbol = trade.get("symbol", "?")
        direction = trade.get("direction", "?")
        outcome = trade.get("outcome", "?")
        pnl = trade.get("pnl_pct", 0)
        lines.append(f"  {symbol} {direction}: {outcome} ({pnl:+.1f}%)")

    return "\n".join(lines)


# Section name → builder function
_SECTION_BUILDERS = {
    "strategy_description": _section_strategy_description,
    "rulebook": _section_rulebook,
    "signal_states": _section_signal_states,
    "pre_trade_context": _section_pre_trade_context,
    "similar_trades": _section_similar_trades,
}


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_prompt(
    substrate: dict,
    max_chars: int = 4000,
    priority_order: Optional[list[str]] = None,
) -> str:
    """
    Assemble the context block for an LLM prompt.

    Sections are added in priority order. When the remaining budget
    cannot fit the next section, it is dropped entirely (no mid-section
    truncation). This ensures clean section boundaries.

    Args:
        substrate:      Dict-like substrate with strategy, analysis, learning data.
        max_chars:      Hard character budget cap.
        priority_order: Override for section priority order.

    Returns:
        Assembled context string, never exceeding max_chars.
    """
    order = priority_order or DEFAULT_PRIORITY_ORDER
    sections: list[str] = []
    remaining = max_chars

    for section_name in order:
        builder = _SECTION_BUILDERS.get(section_name)
        if builder is None:
            _log.debug("Unknown prompt section: '%s'", section_name)
            continue

        section_text = builder(substrate)
        if not section_text:
            continue

        # Drop entire section if it doesn't fit (clean boundary)
        if len(section_text) > remaining:
            _log.debug(
                "Prompt section '%s' dropped: %d chars > %d remaining budget",
                section_name, len(section_text), remaining,
            )
            continue

        sections.append(section_text)
        remaining -= len(section_text)

    return "\n\n".join(sections)