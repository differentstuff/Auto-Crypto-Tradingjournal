"""
enzymes/update_rulebook.py -- Synthase enzyme: regenerate the rulebook when conditions are met.

Activation conditions:
  - total_trades_recorded >= min_trades_before_adjusting (default 30)
  - Enough new trades since last rulebook version (retrain_every_n_trades, default 10)

When activated, calls generate_rulebook() which:
  1. Reads all accuracy data (signal, combination, trajectory, idle)
  2. Ranks candidates by statistical weight
  3. Selects top 10 rules
  4. Writes rulebook text to rulebook_versions DB table

After transform, substrate.learning["rulebook"] and
substrate.learning["rulebook_version"] are updated so downstream
enzymes (ScoreConfluence) can reference the latest rulebook.

Does NOT adjust weights directly — that's called by ScoreConfluence
on each cycle via compute_adjusted_weights().

Enzyme class: Synthase (builds new structures from data)
Priority: 0 (same as other Synthases)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.enzyme import Enzyme, EnzymeClass, register_enzyme
from core.substrate import Substrate

_log = logging.getLogger(__name__)


@register_enzyme
class UpdateRulebook(Enzyme):
    """
    Synthase enzyme: regenerate the rulebook from accuracy data.

    Fires when enough new trades have been recorded since the last
    rulebook generation. The generated rulebook includes contrarian
    rules (anti-signals that should be inverted in scoring).
    """

    name = "UpdateRulebook"
    enzyme_class = EnzymeClass.SYNTHASE
    priority = 0

    def __init__(self, config: dict | None = None):
        super().__init__(config=config)
        self._min_trades = 30
        self._retrain_every = 10
        if config:
            learning_cfg = config.get("learning", {})
            self._min_trades = learning_cfg.get("min_trades_before_adjusting", 30)
            self._retrain_every = learning_cfg.get("retrain_every_n_trades", 10)

    def requires(self) -> list[str]:
        return []

    def prohibits(self) -> list[str]:
        return []

    def can_activate(self, substrate: Substrate) -> bool:
        """
        Activate when enough trades have been recorded and enough
        new trades exist since the last rulebook generation.
        """
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")
        total_trades = substrate.learning.get("total_trades_recorded", 0)

        if total_trades < self._min_trades:
            return False

        from learning.rulebook import should_regenerate
        return should_regenerate(
            strategy_name,
            strategy_uid=strategy_uid,
            min_trades=self._min_trades,
            retrain_every_n_trades=self._retrain_every,
        )

    def transform(self, substrate: Substrate) -> Substrate:
        """
        Generate the rulebook and write it to substrate.learning.

        Also writes to rulebook_versions DB table (side effect).
        Does not modify any other substrate fields.
        """
        strategy_name = substrate.strategy.get("name", "")
        strategy_uid = substrate.strategy.get("uid", "legacy")

        from learning.rulebook import generate_rulebook

        try:
            rulebook_text = generate_rulebook(strategy_name, strategy_uid=strategy_uid)

            if rulebook_text:
                # --- Optional LLM formatting ---
                # The rulebook is always generated from accuracy data (deterministic).
                # The LLM only optionally improves the prose formatting.
                # If LLM is unavailable, the raw structured text is used as-is.
                formatted_text = self._optional_llm_format(substrate, rulebook_text)
                final_text = formatted_text if formatted_text else rulebook_text

                substrate.learning["rulebook"] = final_text
                substrate.learning["rulebook_generated_at"] = datetime.now(timezone.utc).isoformat()

                # Read the version we just wrote
                from core.database import db_conn
                with db_conn() as conn:
                    row = conn.execute(
                        """SELECT version FROM rulebook_versions
                           WHERE strategy_uid = ?
                           ORDER BY id DESC LIMIT 1""",
                        (strategy_uid,),
                    ).fetchone()

                if row:
                    substrate.learning["rulebook_version"] = row["version"]

                _log.info("Rulebook updated for '%s': %d characters%s",
                          strategy_name, len(final_text),
                          " (LLM formatted)" if formatted_text else "")
            else:
                _log.info("No rulebook generated for '%s' — insufficient accuracy data",
                          strategy_name)

        except Exception as e:
            _log.error("Failed to update rulebook for '%s': %s",
                       strategy_name, e, exc_info=True)

        return substrate

    def _optional_llm_format(self, substrate: Substrate, raw_rulebook: str) -> str | None:
        """
        Optionally format the rulebook with LLM prose improvement.

        The rulebook is always generated from accuracy data (deterministic).
        The LLM only improves the readability. If call_llm returns None
        (no key, budget exhausted, provider down), the raw text is used.

        Returns formatted text string, or None if LLM is unavailable.
        Never raises — errors are caught and logged.
        """
        # Check if 'rulebook' role is configured
        llm_routing = substrate.cfg("llm.routing", {})
        if not llm_routing or "rulebook" not in llm_routing:
            return None  # No rulebook role configured — skip LLM

        try:
            from llm.router import call_llm
        except ImportError:
            return None  # LLM module not available

        try:
            # The system prompt comes from config/prompts/rulebook.md
            # (loaded automatically by the router from llm.prompts.rulebook).
            # The user prompt is just the raw rulebook data.
            prompt = f"RAW RULEBOOK:\n{raw_rulebook}"
            result = call_llm("rulebook", prompt)
            if result:
                return result.strip()
            return None

        except Exception as exc:
            # Never let LLM errors break the enzyme
            _log.debug("LLM rulebook formatting skipped: %s", exc)
            return None

    def flux_score(self, substrate: Substrate) -> float:
        """Low flux — rulebook generation is important but infrequent."""
        if not self.can_activate(substrate):
            return 0.0
        # Higher urgency when many trades have been recorded since last update
        total_trades = substrate.learning.get("total_trades_recorded", 0)
        min_trades = self._min_trades
        if total_trades >= min_trades * 2:
            return 1.5  # Significant new data — regenerate rulebook
        return 1.0
