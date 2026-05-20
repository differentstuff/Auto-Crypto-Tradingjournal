"""
llm/key_manager.py -- API Key Rotation Manager.

Handles multiple API keys per LLM service with automatic rotation
on overload (429/529), server errors (500/502/503), and auth failures (401/403).

Ported from: docs/reaction-design/key_manager.py
Used by: llm/router.py (Phase E)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)


@dataclass
class KeyState:
    """Track health and usage of a single API key."""

    key: str
    provider: str  # "anthropic", "google", "openai", etc.
    label: str  # human-readable: "anthropic-key-1"
    last_error_time: float = 0.0
    last_success_time: float = 0.0
    consecutive_errors: int = 0
    total_requests: int = 0
    total_errors: int = 0
    cooldown_until: float = 0.0
    is_active: bool = True


class KeyManager:
    """
    Manages multiple API keys per provider with automatic rotation.

    Key rotation rules:
    1. Prefer keys with most recent success (round-robin among healthy)
    2. Skip keys in cooldown (recently errored)
    3. If all keys in cooldown: use the one with earliest cooldown expiry
    4. On overload error (429, 529): set cooldown of 30 seconds
    5. On auth error (401, 403): disable key, log alert
    6. On server error (500, 502, 503): short cooldown of 10 seconds
    7. Round-robin among healthy keys to distribute load

    Usage:
        from llm.key_manager import KeyManager
        km = KeyManager(config_loader.get_exchange_keys_config())
        key = km.get_key("anthropic")
        try:
            response = call_api(key)
            km.report_success("anthropic", key)
        except RateLimitError:
            km.report_error("anthropic", key, status_code=429)
    """

    # Cooldown durations by error type (seconds)
    COOLDOWN_OVERLOAD = 30  # rate limit / overload (429, 529)
    COOLDOWN_SERVER = 10  # server error (500, 502, 503)
    COOLDOWN_AUTH = float("inf")  # auth error (401, 403) -- disable key

    def __init__(self, keys_config: Dict[str, List[Dict]]):
        """
        Initialize from exchange.yaml llm_keys section.

        keys_config format:
          {
            "anthropic": [
              {"key": "sk-ant-...", "label": "anthropic-key-1"},
              {"key": "sk-ant-...", "label": "anthropic-key-2"},
            ],
            "google": [
              {"key": "AIza...", "label": "gemini-key-1"},
            ]
          }
        """
        self._providers: Dict[str, List[KeyState]] = {}

        for provider, key_list in keys_config.items():
            self._providers[provider] = []
            for i, entry in enumerate(key_list):
                ks = KeyState(
                    key=entry["key"],
                    provider=provider,
                    label=entry.get(
                        "label", f"{provider}-key-{i + 1}"
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
        1. Active keys not in cooldown (prefer least recently used)
        2. If all in cooldown: return key with earliest cooldown expiry
        3. If no keys at all: return None
        """
        keys = self._providers.get(provider, [])
        if not keys:
            _log.warning("No keys configured for provider: %s", provider)
            return None

        now = time.time()

        # Filter to active keys
        active = [k for k in keys if k.is_active]
        if not active:
            _log.error("All keys disabled for provider: %s", provider)
            return None

        # Find healthy keys (not in cooldown)
        healthy = [k for k in active if now >= k.cooldown_until]
        if healthy:
            # Round-robin: pick the one used longest ago
            healthy.sort(key=lambda k: k.last_success_time)
            selected = healthy[0]
            selected.total_requests += 1
            _log.debug(
                "Selected key: %s (provider: %s)", selected.label, provider
            )
            return selected.key

        # All in cooldown: pick earliest expiry
        active.sort(key=lambda k: k.cooldown_until)
        earliest = active[0]
        wait = earliest.cooldown_until - now
        _log.warning(
            "All keys in cooldown for %s. Earliest expiry in %.1fs (%s)",
            provider,
            wait,
            earliest.label,
        )
        if 0 < wait < 60:
            time.sleep(wait)
        earliest.total_requests += 1
        return earliest.key

    def report_error(
        self, provider: str, key: str, status_code: int = 0
    ) -> None:
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
                ks.label,
                status_code,
                cooldown,
            )
        elif status_code in (500, 502, 503):
            cooldown = self.COOLDOWN_SERVER
            _log.warning(
                "Key %s: server error (status %d), cooldown %ds",
                ks.label,
                status_code,
                cooldown,
            )
        elif status_code in (401, 403):
            cooldown = self.COOLDOWN_AUTH
            ks.is_active = False
            _log.error(
                "Key %s: auth error (status %d). KEY DISABLED. "
                "Check your credentials.",
                ks.label,
                status_code,
            )
        else:
            cooldown = 5
            _log.warning(
                "Key %s: unknown error (status %d), cooldown 5s",
                ks.label,
                status_code,
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

    def get_status(self) -> Dict[str, List[Dict]]:
        """Return health status of all keys (for monitoring)."""
        result: Dict[str, List[Dict]] = {}
        for provider, keys in self._providers.items():
            result[provider] = []
            for ks in keys:
                now = time.time()
                result[provider].append(
                    {
                        "label": ks.label,
                        "active": ks.is_active,
                        "in_cooldown": now < ks.cooldown_until,
                        "cooldown_remaining": max(
                            0, ks.cooldown_until - now
                        ),
                        "consecutive_errors": ks.consecutive_errors,
                        "total_requests": ks.total_requests,
                        "total_errors": ks.total_errors,
                        "error_rate": (
                            ks.total_errors / max(ks.total_requests, 1)
                        )
                        * 100,
                    }
                )
        return result

    def _find_key(self, provider: str, key: str) -> Optional[KeyState]:
        """Find KeyState object by provider and key value."""
        for ks in self._providers.get(provider, []):
            if ks.key == key:
                return ks
        _log.warning("Key not found: provider=%s", provider)
        return None