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
import os
from datetime import date, datetime, timezone
from typing import Optional

from llm.key_manager import KeyManager

_log = logging.getLogger(__name__)

# Client module mapping: client type → module path
# Used when llm.providers.<name>.client is not set in config.
# The router resolves the client module from config first, then falls back here.
_CLIENT_MODULE_MAP = {
    "openrouter": "llm.openrouter_client",
    "anthropic": "llm.anthropic_client",
    "google": "llm.gemini_client",
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
        self._budget_usd = self._config.get("cost_budget_daily_usd")
        if self._budget_usd is None:
            raise ValueError(
                "llm.cost_budget_daily_usd must be configured in llm.yaml. "
                "No hardcoded default — budget is a safety constraint."
            )
        self._defaults = self._config.get("defaults", {})
        self._providers = self._config.get("providers", {})
        self._validation = self._config.get("validation", {})
        self._prompts = self._config.get("prompts", {})

        # Strategy UID for seed derivation (read from top-level config)
        self._strategy_uid = config.get("strategy", {}).get("uid", "")

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

        # --- Resolve all parameters: role config overrides global defaults ---
        params = self._resolve_params(role, role_cfg)

        # --- Load external system prompt if configured ---
        external_system = self._load_prompt(role)
        if external_system and not system:
            system = external_system

        # --- Inject format enforcement into system prompt ---
        if params.get("response_format") == "json" and system:
            from llm.prompt_builder import format_enforcement_instruction
            system += format_enforcement_instruction("json")

        # --- Try primary provider ---
        result = self._call_provider(role, prompt, system, params)
        if result is not None:
            # Validate response if JSON was expected
            result = self._validate_response(result, role, prompt, system, params)
            return result

        # --- Try fallback ---
        fallback_cfg = self._routing.get("fallback")
        if fallback_cfg is None:
            _log.info("No fallback configured for role '%s'. Returning None.", role)
            return None

        fb_params = self._resolve_params("fallback", fallback_cfg)

        fb_provider = fb_params.get("provider", "")
        fb_model = fb_params.get("model", "")

        if not fb_provider or not fb_model:
            _log.info("Incomplete fallback config for role '%s'. Returning None.", role)
            return None

        # Don't try fallback if it's the same provider+model as primary
        if fb_provider == params.get("provider") and fb_model == params.get("model"):
            _log.info("Fallback same as primary for role '%s'. Returning None.", role)
            return None

        _log.info("Trying fallback for role '%s': provider=%s model=%s", role, fb_provider, fb_model)

        # Load fallback external prompt if configured
        fb_system = system
        fb_external = self._load_prompt("fallback")
        if fb_external and not fb_system:
            fb_system = fb_external

        result = self._call_provider(role, prompt, fb_system, fb_params)
        if result is not None:
            result = self._validate_response(result, role, prompt, fb_system, fb_params)
        return result  # str or None — never raises

    # -----------------------------------------------------------------------
    # Parameter resolution
    # -----------------------------------------------------------------------

    def _resolve_params(self, role: str, role_cfg: dict) -> dict:
        """Merge role config with global defaults, normalizing all LLM params."""
        params = {
            "provider": role_cfg.get("provider", ""),
            "model": role_cfg.get("model", ""),
            "max_tokens": role_cfg.get("max_tokens", self._defaults.get("max_tokens")),
            "temperature": role_cfg.get("temperature", self._defaults.get("temperature")),
            "top_p": role_cfg.get("top_p", self._defaults.get("top_p")),
            "reasoning": self._normalize_reasoning(
                role_cfg.get("reasoning", self._defaults.get("reasoning"))
            ),
            "response_format": self._resolve_response_format(
                role_cfg.get("response_format", self._defaults.get("response_format"))
            ),
            "seed": self._resolve_seed(
                role_cfg.get("seed", self._defaults.get("seed"))
            ),
            "transforms": self._resolve_list(
                role_cfg.get("transforms", self._defaults.get("transforms", []))
            ),
            "provider_order": self._resolve_list(
                role_cfg.get("provider_order", self._defaults.get("provider_order"))
            ),
            "timeout": role_cfg.get("timeout", self._defaults.get("timeout")),
            "cost_per_million_input": role_cfg.get("cost_per_million_input", 0.0),
            "cost_per_million_output": role_cfg.get("cost_per_million_output", 0.0),
        }
        return params

    @staticmethod
    def _normalize_reasoning(reasoning) -> Optional[dict]:
        """Convert reasoning config to provider-compatible dict format.

        Accepts:
          false/None  → None (use model default)
          "none"      → {"effort": "none"}
          "low"       → {"effort": "low"}
          "medium"    → {"effort": "medium"}
          "high"      → {"effort": "high"}
          {"effort": "none", "exclude": true}  → pass through as-is
        """
        if reasoning is None or reasoning is False:
            return None
        if isinstance(reasoning, str):
            return {"effort": reasoning}
        if isinstance(reasoning, dict):
            return reasoning
        return None

    @staticmethod
    def _resolve_response_format(response_format) -> Optional[str]:
        """Convert response_format config value.

        Accepts:
          false/None  → None (free text)
          "json"      → "json"
        """
        if response_format is None or response_format is False:
            return None
        if isinstance(response_format, str):
            return response_format
        return None

    def _resolve_seed(self, seed) -> Optional[int]:
        """Convert seed config value to integer or None.

        Accepts:
          false/None  → None (non-deterministic)
          true        → derive from strategy UID (hash % 2**32)
          integer     → use as-is
        """
        if seed is None or seed is False:
            return None
        if seed is True:
            # Derive deterministic seed from strategy UID
            return hash(self._strategy_uid) % 2**32
        if isinstance(seed, int):
            return seed
        return None

    @staticmethod
    def _resolve_list(value) -> Optional[list]:
        """Convert list config value. false/None → None, list → list."""
        if value is None or value is False:
            return None
        if isinstance(value, list) and len(value) > 0:
            return value
        return None

    # -----------------------------------------------------------------------
    # Prompt loading
    # -----------------------------------------------------------------------

    def _load_prompt(self, role: str) -> Optional[str]:
        """Load external prompt file for a role, if configured.

        Delegates to prompt_builder.load_prompt_file() for the actual file I/O.
        """
        prompt_path = self._prompts.get(role)
        if not prompt_path:
            return None

        from llm.prompt_builder import load_prompt_file
        return load_prompt_file(role, self._prompts) or None

    # -----------------------------------------------------------------------
    # Response validation
    # -----------------------------------------------------------------------

    def _validate_response(
        self,
        result: str,
        role: str,
        prompt: str,
        system: Optional[str],
        params: dict,
    ) -> str:
        """Validate response and optionally retry on failure.

        For JSON roles: try extraction first (salvage markdown-wrapped JSON),
        then retry with stronger prompt if validation is enabled.
        Returns the (possibly extracted) result string, or the original on failure.
        """
        if params.get("response_format") != "json":
            return result

        from llm.response_parser import validate_json, extract_json, build_retry_prompt

        # First pass: is it valid JSON already?
        is_valid, _ = validate_json(result, role)
        if is_valid:
            return result

        # Try extraction (strip markdown fences, extract embedded JSON)
        extracted = extract_json(result)
        if extracted:
            _log.info("Extracted JSON from malformed response for role '%s'", role)
            return extracted

        # Validation failed — retry if enabled
        validation_cfg = self._validation
        if not validation_cfg.get("enabled", True):
            _log.warning("JSON validation failed for role '%s', retry disabled. Returning raw.", role)
            return result

        max_retries = validation_cfg.get("max_retries", 1)
        retry_stronger = validation_cfg.get("retry_with_stronger_prompt", True)

        for attempt in range(max_retries):
            _log.info("JSON validation retry %d/%d for role '%s'", attempt + 1, max_retries, role)

            retry_prompt = prompt
            retry_system = system

            if retry_stronger:
                retry_prompt, retry_system = build_retry_prompt(prompt, system, "json")

            retry_result = self._call_provider(role, retry_prompt, retry_system, params)
            if retry_result is not None:
                # Validate the retry response
                is_valid, _ = validate_json(retry_result, role)
                if is_valid:
                    return retry_result

                # Try extraction on retry response too
                extracted = extract_json(retry_result)
                if extracted:
                    _log.info("Extracted JSON from retry response for role '%s'", role)
                    return extracted

        _log.warning("JSON validation failed after %d retries for role '%s'. Returning raw.", max_retries, role)
        return result

    # -----------------------------------------------------------------------
    # Provider dispatch
    # -----------------------------------------------------------------------

    def _call_provider(
        self,
        role: str,
        prompt: str,
        system: Optional[str],
        params: dict,
    ) -> Optional[str]:
        """
        Call a single provider. Returns str on success, None on failure.
        Never raises. Handles key lookup, client call, error reporting,
        cost tracking, and token logging.
        """
        provider = params.get("provider", "")
        model = params.get("model", "")

        # --- Get API key ---
        key = self._km.get_key(provider)
        if key is None:
            _log.info(
                "No key available for provider '%s' (role '%s'). Skipping.",
                provider, role,
            )
            return None

        # --- Resolve client module from config ---
        provider_cfg = self._providers.get(provider, {})
        client_type = provider_cfg.get("client", provider)
        module_name = _CLIENT_MODULE_MAP.get(client_type)

        if module_name is None:
            _log.warning("Unknown provider client: '%s'. No module registered.", client_type)
            return None

        try:
            import importlib
            client_mod = importlib.import_module(module_name)
        except ImportError as exc:
            _log.error("Cannot import client module '%s': %s", module_name, exc)
            return None

        # --- Build kwargs for client ---
        send_kwargs = {
            "key": key,
            "prompt": prompt,
            "system": system,
            "max_tokens": params["max_tokens"],
            "model": model,
            "reasoning": params.get("reasoning"),
            "temperature": params.get("temperature"),
            "top_p": params.get("top_p"),
            "response_format": params.get("response_format"),
            "seed": params.get("seed"),
            "transforms": params.get("transforms"),
            "provider_order": params.get("provider_order"),
        }

        # Add base_url if configured for this provider
        base_url = provider_cfg.get("base_url")
        if base_url:
            send_kwargs["base_url"] = base_url

        # Add timeout if configured for this provider
        timeout = provider_cfg.get("timeout") or params.get("timeout")
        if timeout:
            send_kwargs["timeout"] = timeout

        # --- Call the provider ---
        try:
            result_text = client_mod.send(**send_kwargs)
        except TypeError as exc:
            # Client may not accept all kwargs — try with core params only
            _log.debug("Client %s doesn't accept all params, trying core params: %s", client_type, exc)
            try:
                core_kwargs = {
                    "key": key,
                    "prompt": prompt,
                    "system": system,
                    "max_tokens": params["max_tokens"],
                    "model": model,
                }
                result_text = client_mod.send(**core_kwargs)
            except Exception as inner_exc:
                status_code = getattr(inner_exc, "status_code", 0)
                self._km.report_error(provider, key, status_code=status_code)
                _log.warning(
                    "LLM call failed: role='%s' provider='%s' model='%s': %s",
                    role, provider, model, inner_exc,
                )
                return None
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
        cost_input = params.get("cost_per_million_input", 0.0)
        cost_output = params.get("cost_per_million_output", 0.0)

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