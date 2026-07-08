#!/usr/bin/env python3
"""
main.py -- Single entrypoint for the Auto-Trader daemon (v2 Reaction Network).

Exchange-as-truth architecture:
  - Substrate is ALWAYS fresh — never loaded from DB
  - Live mode: positions reconciled from exchange on startup AND every cycle
  - Paper mode: positions are runtime-only, no exchange calls
  - Daemon aborts if exchange is unreachable on startup (live mode)

Usage:
    python3 main.py                           # Paper mode with default strategy
    python3 main.py --paper                   # Paper trading mode (explicit)
    python3 main.py --strategy breakout       # Use a different strategy
    python3 main.py --cycle-once              # Run a single cycle and exit
    python3 main.py --log-level DEBUG         # Verbose logging
"""

import argparse
import logging
import os
import sys
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

# Load .env into os.environ (secrets, API keys, config overrides)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
sys.path.insert(0, PROJECT_ROOT)

# v2 imports only — no legacy root files
from core.daemon import Daemon
from core.enzyme import create_enzyme, list_enzymes
from core.exchange import Exchange
from llm import init_router
import enzymes  # noqa: F401 — triggers @register_enzyme decorators


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the daemon."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler(sys.stdout)]

    log_file = os.environ.get("LOG_FILE", "")
    if not log_file:
        log_dir = os.environ.get("LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
        log_file = os.path.join(log_dir, "auto-trader.log")

    log_dir = os.path.dirname(log_file)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(log_format))
        file_handler.setLevel(log_level)
        handlers.append(file_handler)
    except Exception as e:
        print(f"Warning: could not set up file logging at {log_file}: {e}", file=sys.stderr)

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers,
    )

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-Trader v2: 24/7 automated crypto trading daemon (Reaction Network)"
    )
    parser.add_argument(
        "--strategy",
        default="momentum_rising",
        help="Strategy name (must match a YAML file in config/strategies/)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Paper trading mode: all enzymes run, no real orders placed",
    )
    parser.add_argument(
        "--cycle-once",
        action="store_true",
        help="Run a single cycle and exit (for testing)",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: LOG_LEVEL env var or INFO)",
    )

    args = parser.parse_args()

    log_level = args.log_level or os.environ.get("LOG_LEVEL", "INFO")
    setup_logging(log_level)
    log = logging.getLogger("main")

    log.info("=" * 60)
    log.info("Auto-Trader v2.0 -- Reaction Network Architecture")
    log.info("Exchange-as-truth: substrate is ephemeral, positions from exchange")
    log.info("=" * 60)
    log.info("Strategy:  %s", args.strategy)
    log.info("Paper mode: %s", args.paper)
    log.info("Cycle once: %s", args.cycle_once)
    log.info("Log level:  %s", log_level)
    log.info("Project:    %s", PROJECT_ROOT)

    # -- Initialize daemon ------------------------------------------------------
    daemon = Daemon(
        strategy_name=args.strategy,
        paper_mode=args.paper,
    )

    try:
        daemon.initialize()
    except Exception as e:
        log.error("Failed to initialize daemon: %s", e, exc_info=True)
        sys.exit(1)

    # -- Initialize LLM router --------------------------------------------------
    try:
        merged_config = daemon.config.config
        keys_config = _build_llm_keys_from_env()
        router = init_router(config=merged_config, keys_config=keys_config)
        log.info("LLM router initialized: %d roles configured", len(router._routing))
    except Exception as e:
        log.warning("LLM router initialization failed (non-fatal): %s", e)

    # -- Initialize Exchange -----------------------------------------------------
    exchange = None
    try:
        exchange = Exchange(daemon.config)
        log.info(
            "Exchange initialized: primary=%s, data_source=%s, paper=%s",
            exchange._primary, exchange._data_source, exchange._paper_mode,
        )
    except Exception as e:
        log.warning("Exchange initialization failed (non-fatal): %s", e)

    # -- Exchange reachability check (live mode only) ----------------------------
    # If exchange is unreachable in live mode, abort daemon.
    # Paper mode doesn't need exchange connectivity.
    daemon.exchange = exchange
    if not args.paper:
        daemon.check_exchange_reachable()

    # -- Initial reconciliation from exchange (live mode only) -------------------
    if not args.paper:
        try:
            daemon.reconcile_from_exchange()
            log.info("Initial reconciliation from exchange complete")
        except Exception as e:
            log.error("Initial reconciliation failed: %s", e, exc_info=True)
            log.error("Cannot start daemon without exchange reconciliation. Fix connectivity and restart.")
            sys.exit(1)

    # -- Register enzymes --------------------------------------------------------
    def _register(name: str, **kwargs) -> None:
        enz = create_enzyme(name, config=daemon.substrate._config)
        if enz is None:
            log.warning("Enzyme %s not found in registry (available: %s)", name, list_enzymes())
            return
        for k, v in kwargs.items():
            if hasattr(enz, k):
                setattr(enz, k, v)
            else:
                log.debug("Enzyme %s has no attribute '%s' to inject", name, k)
        daemon.register_enzyme(enz)

    # Phase B: Sensors and Evaluators
    _register("DynamicFilter", exchange=exchange)
    _register("CollectOHLCV", exchange=exchange)
    _register("DetectRegime", exchange=exchange)
    _register("MarketGeometry")

    for name in [
        "ScoreConfluence",
        "DetectNoise",
        "ValidateEntryZone",
        "CollectPreTradeContext",
        "CollectMacroContext",
    ]:
        _register(name)

    # Phase C: Regulators and Transporters
    for name in ["ApproveTrade", "ApproveExit", "RequestExit"]:
        _register(name)

    _register("ExecuteTrade", exchange=exchange)
    _register("ExecuteExit", exchange=exchange)
    _register("SyncPositions", exchange=exchange)
    _register("SendTelegramLog")

    # Phase D: Learning Synthases
    _register("RecordTradeOutcome")
    _register("UpdateLearning")
    _register("UpdateRulebook")

    # Price updates for open positions (lightweight, every cycle)
    _register("UpdateMarkPrices", exchange=exchange)

    # Wait enzyme (always available, lowest priority)
    _register("Wait")

    log.info("Registered %d enzymes: %s", len(daemon.enzymes), [e.name for e in daemon.enzymes])

    # -- Run ---------------------------------------------------------------------
    if args.cycle_once:
        log.info("Running single cycle...")
        result = daemon.run_cycle()
        log.info("Cycle result: %s", result)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Substrate: %s", daemon.substrate)
    else:
        log.info("Starting daemon loop (Ctrl+C or SIGTERM to stop)...")
        daemon.run()


def _build_llm_keys_from_env() -> dict:
    """Build LLM keys config from environment variables."""
    provider_env_map = {
        "anthropic":  "ANTHROPIC_API_KEY",
        "google":     "GEMINI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "grok":       "GROK_API_KEY",
    }

    keys_config = {}
    for provider, env_prefix in provider_env_map.items():
        keys = []
        primary_key = os.environ.get(env_prefix, "")
        if primary_key:
            keys.append({"key": primary_key, "label": f"{provider}-env-1"})
        for i in range(2, 6):
            extra_key = os.environ.get(f"{env_prefix}_{i}", "")
            if extra_key:
                keys.append({"key": extra_key, "label": f"{provider}-env-{i}"})
        if keys:
            keys_config[provider] = keys

    return keys_config


if __name__ == "__main__":
    main()
