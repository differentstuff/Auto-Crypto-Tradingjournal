""" llm/router.py -- Provider-agnostic LLM dispatcher.

The single entry point for all enzymes that need LLM access.
Enzymes never import a specific client directly — they call
router.call_llm(role, prompt) and handle None as "skip LLM this cycle".

Router contract (the golden rule):
  call_llm() NEVER raises. It returns str on success, None on any failure.
  The calling enzyme is responsible for handling None gracefully.
  The daemon must never block inside an enzyme call.

Budget tracking:
  Cost = (input_tokens / 1_000_000) * cost_per_million_input
       + (output_tokens / 1_000_000) * cost_per_million_output
  Tokens estimated as len(text) / 4 (character-based).
  cost_per_million_input/output live inline in llm.routing.<role> config.
  If either rate is absent, cost defaults to 0 (safe default).
  Budget resets at UTC midnight.

Fallback:
  If the primary provider for a role fails, the router tries
  llm.routing.fallback once. If that also fails, returns None.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from llm.key_manager import KeyManager

_log = logging.getLogger(__name__)

# Provider → client module send function (lazy import to avoid circular deps)
_PROVIDER_CLIENTS = {
    "anthropic": "llm.anthropic_client",
    "google": "llm.gemini_client",
    "openrouter": "llm.openrouter_client",
}


class LLMRouter:
    """
    Provider-agnostic LLM dispatcher with budget tracking and key rotation.

    Usage:
        router = LLMRouter(config=merged_config, keys_config=exchange_llm_keys)
        result = router.call_llm("analysis", "Analyze this setup.")
        if result is None:
            # skip LLM this cycle
    """

    def __init__(self, config: dict, keys_config: dict):
        """
        Initialize the router.

        Args:
            config:      Merged config dict (must contain llm.routing, llm.cost_budget_daily_usd).
            keys_config: llm_keys section from exchange.yaml (passed to KeyManager).
        """
        self._config = config.get("llm", {})
        self._routing = self._config.get("routing", {})
        self._budget_usd = self._config.get("cost_budget_daily_usd", 2.00)
        self._max_tokens = self._config.get("max_tokens", {})

        # KeyManager for key rotation
        self._km = KeyManager(keys_config)

        # Budget tracking state
        self._daily_spend_usd: float = 0.0
        self._budget_date: date = datetime.now(timezone.utc).date()

    def call_llm(
        self,
        role: str,
        prompt: str,
        system: Optional[str] = None,
    ) -> Optional[str]:
        """
        Call the LLM configured for the given role.

        Reads llm.routing.<role> from config to determine provider + model.
        Gets key from KeyManager (returns None if all keys in cooldown).
        If key is None: logs idle reason, returns None.
        If provider call fails: tries llm.routing.fallback before giving up.
        Tracks token usage against daily budget.

        NEVER raises. Returns str on success, None on any failure.

        Args:
            role:   Routing role (e.g. "analysis", "rulebook", "pre_filter").
            prompt: User prompt text.
            system: Optional system prompt.

        Returns:
            Response text string, or None on any failure.
        """
        # --- Budget reset check (UTC midnight) ---
        self._check_budget_reset()

        # --- Budget exhausted check ---
        if self._daily_spend_usd >= self._budget_usd:
            _log.info(
                "LLM budget exhausted: $%.4f / $%.2f. Skipping role '%s'.",
                self._daily_spend_usd, self._budget_usd, role,
            )
            return None

        # --- Resolve role config ---
        role_cfg = self._routing.get(role)
        if role_cfg is None:
            _log.warning("Unknown LLM role: '%s'. No routing configured.", role)
            return None

        provider = role_cfg.get("provider", "")
        model = role_cfg.get("model", "")
        max_tokens = self._max_tokens.get(role, 1024)

        if not provider or not model:
            _log.warning("Incomplete routing for role '%s': provider=%s model=%s", role, provider, model)
            return None

        # --- Try primary provider ---
        result = self._call_provider(role, prompt, system, provider, model, max_tokens)
        if result is not None:
            return result

        # --- Try fallback ---
        fallback_cfg = self._routing.get("fallback")
        if fallback_cfg is None:
            _log.info("No fallback configured for role '%s'. Returning None.", role)
            return None

        fb_provider = fallback_cfg.get("provider", "")
        fb_model = fallback_cfg.get("model", "")

        if not fb_provider or not fb_model:
            _log.info("Incomplete fallback config for role '%s'. Returning None.", role)
            return None

        # Don't try fallback if it's the same provider+model as primary
        if fb_provider == provider and fb_model == model:
            _log.info("Fallback same as primary for role '%s'. Returning None.", role)
            return None

        fb_max_tokens = self._max_tokens.get(role, 1024)
        _log.info("Trying fallback for role '%s': provider=%s model=%s", role, fb_provider, fb_model)

        result = self._call_provider(role, prompt, system, fb_provider, fb_model, fb_max_tokens)
        return result  # str or None — never raises

    def _call_provider(
        self,
        role: str,
        prompt: str,
        system: Optional[str],
        provider: str,
        model: str,
        max_tokens: int,
    ) -> Optional[str]:
        """
        Call a single provider. Returns str on success, None on failure.
        Never raises. Handles key lookup, client call, error reporting,
        cost tracking, and token logging.
        """
        # --- Get API key ---
        key = self._km.get_key(provider)
        if key is None:
            _log.info(
                "No key available for provider '%s' (role '%s'). Skipping.",
                provider, role,
            )
            return None

        # --- Resolve client module ---
        module_name = _PROVIDER_CLIENTS.get(provider)
        if module_name is None:
            _log.warning("Unknown provider: '%s'. No client module registered.", provider)
            return None

        try:
            import importlib
            client_mod = importlib.import_module(module_name)
        except ImportError as exc:
            _log.error("Cannot import client module '%s': %s", module_name, exc)
            return None

        # --- Call the provider ---
        try:
            result_text = client_mod.send(
                key=key,
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                model=model,
            )
        except Exception as exc:
            status_code = getattr(exc, "status_code", 0)
            self._km.report_error(provider, key, status_code=status_code)
            _log.warning(
                "LLM call failed: role='%s' provider='%s' model='%s' status=%d: %s",
                role, provider, model, status_code, exc,
            )
            return None

        # --- Success ---
        self._km.report_success(provider, key)

        # --- Track cost ---
        role_cfg = self._routing.get(role, {})
        cost_input = role_cfg.get("cost_per_million_input", 0.0)
        cost_output = role_cfg.get("cost_per_million_output", 0.0)

        est_input_tokens = len(prompt) / 4
        est_output_tokens = len(result_text) / 4 if result_text else 0

        call_cost = (
            (est_input_tokens / 1_000_000) * cost_input
            + (est_output_tokens / 1_000_000) * cost_output
        )
        self._daily_spend_usd += call_cost

        _log.debug(
            "LLM call success: role='%s' provider='%s' model='%s' "
            "est_tokens=%d+%d cost=$%.6f daily_total=$%.4f",
            role, provider, model,
            int(est_input_tokens), int(est_output_tokens),
            call_cost, self._daily_spend_usd,
        )

        # --- Log token usage to DB ---
        self._log_token_usage(
            module=f"llm.{role}",
            model=f"{provider}/{model}",
            input_tokens=int(est_input_tokens),
            output_tokens=int(est_output_tokens),
        )

        return result_text

    def _check_budget_reset(self) -> None:
        """Reset daily spend if the UTC date has changed."""
        today = datetime.now(timezone.utc).date()
        if today != self._budget_date:
            _log.info(
                "LLM budget reset: $%.4f spent on %s. New day starts at $0.00.",
                self._daily_spend_usd, self._budget_date,
            )
            self._daily_spend_usd = 0.0
            self._budget_date = today

    def _log_token_usage(
        self, module: str, model: str, input_tokens: int, output_tokens: int,
    ) -> None:
        """Record token usage to the token_usage table (best-effort, never raises)."""
        try:
            from core.database import db_conn
            with db_conn() as conn:
                conn.execute(
                    "INSERT INTO token_usage (module, model, input_tokens, output_tokens, cached_tokens) "
                    "VALUES (?,?,?,?,?)",
                    (module, model, input_tokens, output_tokens, 0),
                )
                conn.commit()
        except Exception as exc:
            _log.debug("Token usage log failed (non-critical): %s", exc)

    def get_status(self) -> dict:
        """Return router status for monitoring: budget, key health."""
        return {
            "daily_spend_usd": round(self._daily_spend_usd, 6),
            "budget_usd": self._budget_usd,
            "budget_date": str(self._budget_date),
            "budget_remaining_usd": round(max(0, self._budget_usd - self._daily_spend_usd), 6),
            "keys": self._km.get_status(),
        }


# ---------------------------------------------------------------------------
# Module-level convenience function
# ---------------------------------------------------------------------------

# The module-level router instance. Initialized by the daemon on startup.
_router: Optional[LLMRouter] = None


def init_router(config: dict, keys_config: dict) -> LLMRouter:
    """
    Initialize the global router instance. Called once by the daemon at startup.

    Args:
        config:      Merged config dict (must contain 'llm' section).
        keys_config: llm_keys section from exchange.yaml.

    Returns:
        The initialized LLMRouter instance.
    """
    global _router
    _router = LLMRouter(config=config, keys_config=keys_config)
    _log.info(
        "LLM router initialized: %d roles, budget=$%.2f",
        len(_router._routing), _router._budget_usd,
    )
    return _router


def call_llm(role: str, prompt: str, system: Optional[str] = None) -> Optional[str]:
    """
    Call the LLM configured for the given role using the global router.

    This is the function that enzymes import and call:
        from llm.router import call_llm
        result = call_llm("analysis", "Analyze this setup.")

    If the router has not been initialized, returns None immediately.

    NEVER raises. Returns str on success, None on any failure.
    """
    if _router is None:
        _log.warning("LLM router not initialized. Call init_router() first.")
        return None
    return _router.call_llm(role, prompt, system)