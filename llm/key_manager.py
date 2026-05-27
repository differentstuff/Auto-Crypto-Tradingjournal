# llm/key_manager.py -- API Key Rotation Manager
#
# Manages multiple API keys per LLM provider with automatic rotation.
# Handles overload detection, cooldown tracking, and auth error disabling.
#
# Usage by LLM clients:
#   from llm.key_manager import KeyManager
#   km = KeyManager(config.get_exchange_keys)
#   key = km.get_key("anthropic")       # returns best available key, or None
#   km.report_error("anthropic", key, status_code=429)
#   km.report_success("anthropic", key)
#
# IMPORTANT: get_key() never blocks the caller. If all keys are in cooldown,
# it returns None immediately. The calling enzyme is responsible for handling
# the "no key available" case (mark idle, log reason, continue cycle).
# The daemon must never block inside an enzyme call.

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)


@dataclass
class KeyState:
    """Track health and usage of a single API key."""
    key: str
    provider: str                       # "anthropic", "google", "openai", etc.
    label: str                           # human-readable: "anthropic-key-1"
    last_error_time: float = 0.0         # timestamp of last error
    last_success_time: float = 0.0       # timestamp of last success
    consecutive_errors: int = 0
    total_requests: int = 0
    total_errors: int = 0
    cooldown_until: float = 0.0          # timestamp: do not use before this
    is_active: bool = True               # False = auth error, needs manual fix


class KeyManager:
    """
    Manages multiple API keys per provider with automatic rotation.

    Key rotation rules:
    1. Prefer keys with most recent success (round-robin among healthy keys)
    2. Skip keys in cooldown (recently errored)
    3. If all keys in cooldown: return None immediately (never block)
    4. On overload error (429, 529): set cooldown of 30 seconds
    5. On auth error (401, 403): mark key as inactive and log alert
    6. On server error (500, 502, 503): short cooldown of 10 seconds
    7. On unknown error: short cooldown of 5 seconds

    The caller is responsible for handling None (no key available):
    - Log the reason as an idle cycle
    - Skip the LLM call for this cycle
    - Retry on the next cycle when cooldown expires
    """

    # Cooldown durations by error type (seconds)
    # Defaults match llm.yaml cooldowns section; overridden by config if provided.
    COOLDOWN_OVERLOAD = 30       # rate limit / overload (429, 529)
    COOLDOWN_SERVER = 10         # server error (500, 502, 503)
    COOLDOWN_UNKNOWN = 5         # unknown error
    COOLDOWN_AUTH = float("inf") # auth error (401, 403) -- disable key permanently

    def __init__(self, keys_config: dict, cooldowns: dict = None):
        """
        Initialize from exchange.yaml llm_keys section.

        keys_config format:
          anthropic:
            - key: "sk-ant-..."
              label: "anthropic-key-1"
            - key: "sk-ant-..."
              label: "anthropic-key-2"
          google:
            - key: "AIza..."
              label: "gemini-key-1"

        cooldowns: dict from llm.yaml cooldowns section (optional).
          overload: 30, server: 10, unknown: 5
        """
        # Override class-level cooldowns from config if provided
        if cooldowns:
            self.COOLDOWN_OVERLOAD = cooldowns.get("overload", self.COOLDOWN_OVERLOAD)
            self.COOLDOWN_SERVER = cooldowns.get("server", self.COOLDOWN_SERVER)
            self.COOLDOWN_UNKNOWN = cooldowns.get("unknown", self.COOLDOWN_UNKNOWN)

        self._providers: dict[str, list[KeyState]] = {}

        for provider, key_list in keys_config.items():
            self._providers[provider] = []
            for entry in key_list:
                ks = KeyState(
                    key=entry["key"],
                    provider=provider,
                    label=entry.get(
                        "label",
                        f"{provider}-key-{len(self._providers[provider]) + 1}",
                    ),
                )
                self._providers[provider].append(ks)

        _log.info(
            "KeyManager initialized: %s",
            {p: len(v) for p, v in self._providers.items()},
        )

    def get_key(self, provider: str) -> Optional[str]:
        """
        Return the best available API key for the given provider.

        Selection priority:
        1. Active keys not in cooldown (least recently used for round-robin)
        2. If all in cooldown: return None immediately -- never block

        Returns None if:
        - No keys configured for this provider
        - All keys are disabled (auth errors)
        - All keys are in cooldown (caller should skip this cycle)
        """
        keys = self._providers.get(provider, [])
        if not keys:
            _log.warning("No keys configured for provider: %s", provider)
            return None

        now = time.time()

        # Filter to active keys (not permanently disabled)
        active = [k for k in keys if k.is_active]
        if not active:
            _log.error("All keys disabled for provider: %s", provider)
            return None

        # Find healthy keys (not in cooldown)
        healthy = [k for k in active if now >= k.cooldown_until]
        if healthy:
            # Round-robin: pick the one used longest ago (least recently used)
            healthy.sort(key=lambda k: k.last_success_time)
            selected = healthy[0]
            selected.total_requests += 1
            _log.debug("Selected key: %s (provider: %s)", selected.label, provider)
            return selected.key

        # All keys in cooldown -- return None immediately, never block
        earliest = min(active, key=lambda k: k.cooldown_until)
        wait = earliest.cooldown_until - now
        _log.warning(
            "All keys in cooldown for %s. Earliest expiry in %.1fs (%s). "
            "Skipping LLM call this cycle.",
            provider,
            wait,
            earliest.label,
        )
        return None

    def report_error(self, provider: str, key: str, status_code: int = 0) -> None:
        """
        Report an API error for a key. Triggers cooldown or deactivation.

        status_code mapping:
          429, 529 -> overload cooldown (30s)
          500, 502, 503 -> server cooldown (10s)
          401, 403 -> auth error (disable key, needs manual fix)
          other -> short cooldown (5s)
        """
        ks = self._find_key(provider, key)
        if ks is None:
            return

        ks.consecutive_errors += 1
        ks.total_errors += 1
        ks.last_error_time = time.time()

        if status_code in (429, 529):
            cooldown = self.COOLDOWN_OVERLOAD
            _log.warning(
                "Key %s: overload (status %d), cooldown %ds",
                ks.label, status_code, cooldown,
            )
        elif status_code in (500, 502, 503):
            cooldown = self.COOLDOWN_SERVER
            _log.warning(
                "Key %s: server error (status %d), cooldown %ds",
                ks.label, status_code, cooldown,
            )
        elif status_code in (401, 403):
            cooldown = self.COOLDOWN_AUTH
            ks.is_active = False
            _log.error(
                "Key %s: auth error (status %d). KEY DISABLED. "
                "Check your credentials in exchange.yaml.",
                ks.label, status_code,
            )
        else:
            cooldown = self.COOLDOWN_UNKNOWN
            _log.warning(
                "Key %s: unknown error (status %d), cooldown %ds",
                ks.label, status_code, cooldown,
            )

        ks.cooldown_until = time.time() + cooldown

    def report_success(self, provider: str, key: str) -> None:
        """Report a successful API call. Resets consecutive error count."""
        ks = self._find_key(provider, key)
        if ks is None:
            return
        ks.consecutive_errors = 0
        ks.last_success_time = time.time()
        ks.cooldown_until = 0.0  # clear any remaining cooldown

    def get_status(self) -> dict:
        """Return health status of all keys (for monitoring and logging)."""
        result = {}
        now = time.time()
        for provider, keys in self._providers.items():
            result[provider] = []
            for ks in keys:
                result[provider].append({
                    "label": ks.label,
                    "active": ks.is_active,
                    "in_cooldown": now < ks.cooldown_until,
                    "cooldown_remaining": max(0.0, ks.cooldown_until - now),
                    "consecutive_errors": ks.consecutive_errors,
                    "total_requests": ks.total_requests,
                    "total_errors": ks.total_errors,
                    "error_rate": (ks.total_errors / max(ks.total_requests, 1)) * 100,
                })
        return result

    def _find_key(self, provider: str, key: str) -> Optional[KeyState]:
        """Find KeyState object by provider and key value."""
        for ks in self._providers.get(provider, []):
            if ks.key == key:
                return ks
        _log.warning("Key not found: provider=%s", provider)
        return None
