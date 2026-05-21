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
                substrate.learning["rulebook"] = rulebook_text
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

                _log.info("Rulebook updated for '%s': %d characters",
                          strategy_name, len(rulebook_text))
            else:
                _log.info("No rulebook generated for '%s' — insufficient accuracy data",
                          strategy_name)

        except Exception as e:
            _log.error("Failed to update rulebook for '%s': %s",
                       strategy_name, e, exc_info=True)

        return substrate

    def flux_score(self, substrate: Substrate) -> float:
        """Low flux — rulebook generation is important but infrequent."""
        if self.can_activate(substrate):
            return 1.0
        return 0.0