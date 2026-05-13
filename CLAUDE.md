# Trading Journal — Claude Code Context

## Project Overview
Self-hosted crypto futures trading journal. Flask 3.1 / Python 3.13 / SQLite WAL.
Runs as a systemd service on a Raspberry Pi 5 (<Pi-IP>). Accessible from any browser on the local network.

## Deployment
- **Pi SSH:** `<user>@<Pi-IP>` (use expect — no BatchMode; credentials in local memory only)
- **Service:** `sudo systemctl restart trading-journal`
- **Pi path:** `/home/<user>/trading-journal`
- **Dev path:** local clone of this repo
- **Port:** 8082

## Database
- **Mode:** SQLite WAL — safe for concurrent reads during sync
- **Migrations:** database.py::init_db() — ALL must be idempotent
- **Tables:** positions, orders, wallet_snapshots, analyzed_calls, pending_limits, trader_rulebook, trade_hindsight, settings, import_log, token_usage, schema_version

## Import Graph (safe edit order)
constants.py, prompt_fragments.py, trade_history.py, chart_sr.py, chart_indicators.py — no internal deps, edit freely
helpers.py, database.py — imported by everything, edit carefully
ai_client.py — imports constants + helpers
chart_context.py — orchestrates chart_indicators + chart_sr (1140 lines, being split)
prompt_builder.py — imports chart_context, ai_rulebook, ai_pattern_detector, nansen_client
ai_*.py — import ai_client + prompt_builder + trade_history
routes/*.py — import helpers + ai_* modules

## AI Pipeline
- Sonnet (claude-sonnet-4-6): call analyzer, advisor, scanner, rulebook, pattern detector, grader
- Haiku (claude-haiku-4-5-20251001): scanner quick-score, hindsight, live trade check, limit analysis
- Token logging: log_token_usage(module, model, in, out, cached) from helpers.py
- Prompt caching: build_cached_messages() — ephemeral cache on context blocks >= 4096 chars

## Testing
- Framework: pytest
- Tests in tests/ directory
- Run: python3 -m pytest tests/ -v
- Fixtures: tests/conftest.py — db (in-memory SQLite), sample_positions

## API Rules
- All routes return {"ok": true/false, "data": ...} via _ok() / _err()
- Never expose exception messages in API responses (CWE-209)
- Never change existing endpoint URLs or response shapes

## JS Frontend
- 17 modules static/js/01-utils.js through 16-settings.js
- Bump ?v=X.X in templates/index.html on every JS change
- notify(msg, type) toast function in 01-utils.js
