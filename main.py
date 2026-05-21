#!/usr/bin/env python3
"""
main.py -- Single entrypoint for the Auto-Trader daemon (v2 Reaction Network).

Usage:
    python3 main.py                           # Paper mode with default strategy
    python3 main.py --paper                   # Paper trading mode (explicit)
    python3 main.py --strategy breakout       # Use a different strategy
    python3 main.py --cycle-once              # Run a single cycle and exit
    python3 main.py --log-level DEBUG         # Verbose logging

The daemon runs 24/7, loading config on every cycle for hot-reload.
No restart needed to adjust strategy, risk limits, or indicator selection.

All logic lives in subdirectories: core/, enzymes/, indicators/, learning/, llm/.
No imports from legacy root-level files.
"""

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# Add project root to Python path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

# v2 imports only — no legacy root files
from core.daemon import Daemon
from core.enzyme import create_enzyme, list_enzymes
from core.exchange import Exchange
from llm import init_router
import enzymes  # noqa: F401 — triggers @register_enzyme decorators


def setup_logging(level: str = "INFO") -> None:
    """
    Configure logging for the daemon.

    Outputs to both stdout and a rotating log file.
    Log file path is read from LOG_FILE env var (default: logs/auto-trader.log).
    """
    log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler(sys.stdout)]

    # File logging — read path from .env / environment
    log_file = os.environ.get("LOG_FILE", "")
    if not log_file:
        log_dir = os.environ.get("LOG_DIR", os.path.join(PROJECT_ROOT, "logs"))
        log_file = os.path.join(log_dir, "auto-trader.log")

    # Ensure log directory exists
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

    # Reduce noise from third-party libraries
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
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging (stdout + rotating file)
    setup_logging(args.log_level)
    log = logging.getLogger("main")

    log.info("=" * 60)
    log.info("Auto-Trader v2.0 -- Reaction Network Architecture")
    log.info("=" * 60)
    log.info("Strategy:  %s", args.strategy)
    log.info("Paper mode: %s", args.paper)
    log.info("Cycle once: %s", args.cycle_once)
    log.info("Log level:  %s", args.log_level)
    log.info("Project:    %s", PROJECT_ROOT)

    # ── Initialize daemon ──────────────────────────────────────────────────────
    daemon = Daemon(
        strategy_name=args.strategy,
        paper_mode=args.paper,
    )

    try:
        daemon.initialize()
    except Exception as e:
        log.error("Failed to initialize daemon: %s", e, exc_info=True)
        sys.exit(1)

    # ── Initialize LLM router ──────────────────────────────────────────────────
    # The router provides call_llm() to all enzymes via llm.call_llm().
    # It reads provider/model routing from default.yaml and API keys from
    # exchange.yaml (llm_keys section). Without keys, call_llm() returns None
    # and enzymes fall back to rule-based logic — the system still runs.
    try:
        merged_config = daemon.config.config
        keys_config = merged_config.get("llm_keys", {})
        router = init_router(config=merged_config, keys_config=keys_config)
        log.info("LLM router initialized: %d roles configured", len(router._routing))
    except Exception as e:
        log.warning("LLM router initialization failed (non-fatal): %s", e)
        log.warning("Enzymes using LLM will fall back to rule-based logic")

    # ── Initialize Exchange ─────────────────────────────────────────────────────
    # The Exchange provides OHLCV data (public, no auth) and trade execution
    # (authenticated, paper-mode guarded). Even without API keys, data fetching
    # works via Binance public endpoints.
    exchange = None
    try:
        exchange = Exchange(daemon.config)
        log.info(
            "Exchange initialized: primary=%s, data_source=%s, paper=%s",
            exchange._primary, exchange._data_source, exchange._paper_mode,
        )
    except Exception as e:
        log.warning("Exchange initialization failed (non-fatal): %s", e)
        log.warning("OHLCV data fetching may not work")

    # ── Register enzymes ────────────────────────────────────────────────────────
    # Each enzyme is created via the registry (no direct class imports).
    # The enzymes package was imported above, triggering @register_enzyme.

    def _register(name: str, **kwargs) -> None:
        """Register an enzyme by name, with optional keyword injection."""
        enz = create_enzyme(name, config=daemon.substrate._config)
        if enz is None:
            log.warning("Enzyme %s not found in registry (available: %s)", name, list_enzymes())
            return
        # Inject extra dependencies via keyword args
        for k, v in kwargs.items():
            if hasattr(enz, k):
                setattr(enz, k, v)
            else:
                log.debug("Enzyme %s has no attribute '%s' to inject", name, k)
        daemon.register_enzyme(enz)

    # Phase B: Sensors and Evaluators
    for name in [
        "CollectOHLCV",
        "ScoreConfluence",
        "DetectNoise",
        "ValidateEntryZone",
        "CollectPreTradeContext",
        "CollectMacroContext",
    ]:
        _register(name)

    # Phase C: Regulators and Transporters
    # Trade enzymes need the Exchange instance for order placement / data fetching
    for name in ["ApproveTrade", "ApproveExit", "RequestExit"]:
        _register(name)

    _register("ExecuteTrade", exchange=exchange)
    _register("ExecuteExit", exchange=exchange)
    _register("SyncPositions", exchange=exchange)
    _register("SendTelegramLog")

    # Phase D: Learning Synthases
    _register("RecordTradeOutcome")
    _register("UpdateRulebook")

    # Wait enzyme (always available, lowest priority)
    _register("Wait")

    # Log registered enzymes
    log.info("Registered %d enzymes: %s", len(daemon.enzymes), [e.name for e in daemon.enzymes])

    # ── Run ─────────────────────────────────────────────────────────────────────
    if args.cycle_once:
        # Single cycle mode (for testing / debugging)
        log.info("Running single cycle...")
        result = daemon.run_cycle()
        log.info("Cycle result: %s", result)
        if log.isEnabledFor(logging.DEBUG):
            log.debug("Substrate: %s", daemon.substrate)
    else:
        # Continuous daemon mode
        log.info("Starting daemon loop (Ctrl+C or SIGTERM to stop)...")
        daemon.run()


if __name__ == "__main__":
    main()