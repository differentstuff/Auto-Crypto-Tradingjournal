"""
core/config_loader.py -- YAML config reader with hot-reload and defaults merging.

Merges: default.yaml < strategy.yaml

Secrets (API keys) are read from .env via environment variables,
not from YAML files. See .env.example for all available env vars.

The daemon reads config on every cycle. No restart needed to adjust
strategy, risk limits, or indicator selection.
"""

from __future__ import annotations

import copy
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import yaml

_log = logging.getLogger(__name__)

# Resolve paths relative to project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override into base.
    Override values take precedence. Lists are replaced, not appended.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: str) -> dict:
    """Load a YAML file, returning empty dict on error."""
    if not os.path.exists(path):
        _log.warning("Config file not found: %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        _log.error("Failed to load config %s: %s", path, e)
        return {}


class ConfigLoader:
    """
    Loads and merges configuration from YAML files.

    Merge order (later overrides earlier):
      1. config/default.yaml -- system defaults, never hand-edit
      2. config/strategies/<name>.yaml -- strategy-specific overrides

    Secrets (API keys) come from .env via environment variables.

    Provides dot-access via get() method. The daemon calls reload()
    on every cycle to pick up changes without restart.
    """

    def __init__(
        self,
        strategy_name: str = "momentum_rising",
        config_dir: Optional[str] = None,
    ):
        self.strategy_name = strategy_name
        self.config_dir = config_dir or _CONFIG_DIR
        self._config: Dict[str, Any] = {}
        self._last_loaded: Optional[datetime] = None
        self._load()

    def _load(self) -> None:
        """Load and merge all config files."""
        # 1. Default config (base layer)
        default_path = os.path.join(self.config_dir, "default.yaml")
        default_cfg = _load_yaml(default_path)

        # 2. LLM config (second layer)
        llm_path = os.path.join(self.config_dir, "llm.yaml")
        llm_cfg = _load_yaml(llm_path)

        # 3. Strategy config (override layer)
        strategy_path = os.path.join(
            self.config_dir, "strategies", f"{self.strategy_name}.yaml"
        )
        strategy_cfg = _load_yaml(strategy_path)

        # Merge: default < llm < strategy (secrets come from .env, not YAML)
        merged = _deep_merge(default_cfg, llm_cfg)
        merged = _deep_merge(merged, strategy_cfg)

        # Inject provider base_urls from environment variables
        self._inject_provider_base_urls(merged)

        # Ensure strategy name is set
        if not merged.get("strategy", {}).get("name"):
            merged.setdefault("strategy", {})["name"] = self.strategy_name

        # Ensure strategy uid is set (auto-generate if missing/empty)
        # The uid is a stable identity for learning data. It persists across
        # renames, parameter changes, and reordering. Only clearing it manually
        # (setting to "" in the YAML) triggers a fresh uid on next load.
        strategy_uid = merged.get("strategy", {}).get("uid", "")
        if not strategy_uid:
            strategy_uid = str(uuid.uuid4())
            merged.setdefault("strategy", {})["uid"] = strategy_uid
            # Write the generated uid back to the strategy YAML file only
            self._write_uid_to_yaml(strategy_path, strategy_uid)
            _log.info("Generated new strategy uid: %s", strategy_uid)

        self._config = merged
        self._last_loaded = datetime.now(timezone.utc)
        _log.info(
            "Config loaded: strategy=%s, last_loaded=%s",
            self.strategy_name,
            self._last_loaded.isoformat(),
        )

    def _write_uid_to_yaml(self, strategy_path: str, uid: str) -> None:
        """
        Write the generated uid back to the strategy YAML file.

        Reads the file, updates only the uid field under strategy:,
        and writes it back. Preserves comments and formatting by
        doing a targeted string replacement rather than a full dump.

        If the file cannot be written (permissions, missing dir),
        logs a warning but does not raise — the uid is still in memory.
        """
        try:
            if not os.path.exists(strategy_path):
                return

            with open(strategy_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Replace uid value (handles "", '', "old-uuid", 'old-uuid', or bare word)
            import re
            new_content = re.sub(
                r'(uid:\s*)(?:"[^"]*"|\'[^\']*\'|[^\s#]*)',
                f'\\1"{uid}"',
                content,
                count=1,
            )

            if new_content != content:
                with open(strategy_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                _log.info("Wrote strategy uid to %s", strategy_path)
        except Exception as e:
            _log.warning("Could not write uid to %s: %s", strategy_path, e)

    def reload(self) -> bool:
        """
        Reload config from files. Returns True if config changed.
        Called by daemon on every cycle for hot-reload.
        """
        old_config = copy.deepcopy(self._config)
        self._load()
        changed = self._config != old_config
        if changed:
            _log.info("Config changed after reload")
        return changed

    @property
    def config(self) -> dict:
        """Return the full merged config dict."""
        return self._config

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """
        Get a config value by dotted path.

        Examples:
            config.get("strategy.name")  -> "momentum_rising"
            config.get("scoring.entry_threshold")  -> 6.5
            config.get("indicators")  -> [...]
        """
        parts = dotted_path.split(".")
        obj = self._config
        for part in parts:
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return default
            if obj is None:
                return default
        return obj

    # Provider name -> env var mapping for LLM API keys
    _LLM_ENV_MAP = {
        "anthropic":  "ANTHROPIC_API_KEY",
        "google":     "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "grok":       "GROK_API_KEY",
    }

    # Provider name -> env var mapping for LLM base URLs
    _LLM_BASE_URL_ENV_MAP = {
        "openrouter": "OPENROUTER_BASE_URL",
        "anthropic":  "ANTHROPIC_BASE_URL",
        "google":     "GEMINI_BASE_URL",
    }

    def _inject_provider_base_urls(self, merged: dict) -> None:
        """
        Inject provider base_urls from environment variables into merged config.

        Base URLs are system config (they never change between models for the
        same provider), so they live in .env, not in YAML. This method reads
        them from env and injects into llm.providers.<name>.base_url.
        """
        providers = merged.get("llm", {}).get("providers", {})
        for provider_name, env_var in self._LLM_BASE_URL_ENV_MAP.items():
            base_url = os.environ.get(env_var, "")
            if base_url and provider_name in providers:
                providers[provider_name]["base_url"] = base_url

    def get_exchange_keys(self, provider: str) -> list:
        """
        Get LLM API keys for a provider from environment variables.

        Env var mapping:
          ANTHROPIC_API_KEY -> anthropic
          GEMINI_API_KEY -> google
          OPENROUTER_API_KEY -> openrouter
          GROK_API_KEY -> grok

        Returns list of dicts: [{"key": "...", "label": "..."}]
        """
        env_var = self._LLM_ENV_MAP.get(provider, f"{provider.upper()}_API_KEY")
        key_val = os.environ.get(env_var, "")
        if key_val:
            return [{"key": key_val, "label": f"{provider}-env-1"}]
        return []

    def get_exchange_creds(self, exchange_name: str) -> dict:
        """
        Get exchange API credentials from environment variables.

        Env var mapping:
          BITGET_API_KEY, BITGET_SECRET_KEY, BITGET_PASSPHRASE
          BINANCE_API_KEY, BINANCE_SECRET_KEY
          BYBIT_API_KEY, BYBIT_SECRET_KEY

        Returns dict: {"api_key": "...", "secret_key": "...", "passphrase": "...", "sandbox": false}
        """
        prefix = exchange_name.upper()
        return {
            "api_key": os.environ.get(f"{prefix}_API_KEY", ""),
            "secret_key": os.environ.get(f"{prefix}_SECRET_KEY", ""),
            "passphrase": os.environ.get(f"{prefix}_PASSPHRASE", ""),
            "sandbox": False,
        }

    @property
    def strategy_description(self) -> str:
        """Human-readable strategy description."""
        return self._config.get("description", "")

    @property
    def indicators(self) -> list:
        """List of indicator configs."""
        return self._config.get("indicators", [])

    @property
    def enabled_indicators(self) -> list:
        """List of indicators with weight > 0 (scoring indicators)."""
        return [i for i in self.indicators if i.get("weight", 0) > 0]

    @property
    def modules(self) -> dict:
        """Module toggles."""
        return self._config.get("modules", {})

    def is_module_enabled(self, module_name: str) -> bool:
        """Check if a module is enabled."""
        return self.modules.get(module_name, False)

    @property
    def symbols_always_watch(self) -> list:
        """Symbols to always watch."""
        symbols = self._config.get("symbols", {})
        return symbols.get("always_watch", [])

    @property
    def symbols_never_trade(self) -> list:
        """Symbols to never trade."""
        symbols = self._config.get("symbols", {})
        return symbols.get("never_trade", [])

    @property
    def paper_mode(self) -> bool:
        """Check if paper trading mode is enabled."""
        return self._config.get("daemon", {}).get("paper_mode", False) or \
               self._config.get("exchange", {}).get("mode", "live") == "paper"

    def __repr__(self) -> str:
        return (
            f"ConfigLoader(strategy={self.strategy_name}, "
            f"last_loaded={self._last_loaded})"
        )