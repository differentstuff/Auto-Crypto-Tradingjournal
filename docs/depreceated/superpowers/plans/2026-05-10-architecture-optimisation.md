# Architecture Optimisation Implementation Plan

> **Status: COMPLETE** — All phases implemented (2026-05-10). Latest commit: 5dcdc7d.

**Goal:** Eliminate duplication, silence 8 swallowed exceptions, cut ~300 tokens per AI call, and make every module independently testable.

**Result:** 20 tests passing, Pi live, all 65 API endpoints unchanged.

---

## Completed Work Summary

| Phase | Items | Status |
|-------|-------|--------|
| **0 — Foundation** | constants.py, prompt_fragments.py, 8 bare-except fixes, log_token_usage additions, pytest scaffold, CLAUDE.md | ✅ |
| **1 — AI Layer** | ai_client.py (7→1 Anthropic clients), trade_history.py (4→1 _symbol_history) | ✅ |
| **2 — Chart Split** | chart_indicators.py (pure RSI/EMA/MACD/ADX), chart_sr.py (pure S/R pivots) | ✅ |
| **3 — DB Migrations** | schema_version table + numbered _apply() runner | ✅ |
| **4+5 — Quick Wins** | normalize_symbol/direction in trade_utils, remove ai_call_analyzer stub, .env.example | ✅ |

**Token savings:** ~240 tokens/call from prompt_fragments deduplication.
**Test coverage:** tests/ directory, 20 tests, conftest.py with db+sample_positions fixtures.

---

## Remaining Work (from original spec)

- [ ] **P0-2** Cache race condition in chart_context.py + nansen_client.py (double-checked locking)
- [ ] **P0-3** Surface equity API failure with user-visible warning banner
- [ ] **P0-5** Add VALID_TIMEFRAMES allowlist in chart_context.py
- [ ] **P1-7** Save setup_type from Claude JSON response to analyzed_calls
- [ ] **P1-8** Enforce prompt_builder char budget (currently unenforced at 5,600 chars)
- [ ] **P1-5** Rulebook versioning (keep last 3 versions in trader_rulebook_history)
- [ ] **Wire chart_context.py** to use chart_indicators.py + chart_sr.py (currently parallel, not integrated)
- [ ] **Phase 3** Write self_test.py covering all 65 endpoints
- [ ] **Phase 4** UI/UX: stale-data badge, symbol autocomplete, scanner ETA, mobile overflow fixes

---

## Architecture Issues Reference (from initial review)

For full details see `docs/superpowers/specs/2026-05-09-architecture-review-and-optimisation.md`.

**Key files created this phase:**

| File | Lines | Purpose |
|------|-------|---------|
| `constants.py` | ~30 | All shared constants |
| `prompt_fragments.py` | ~30 | Reusable prompt text blocks |
| `ai_client.py` | ~40 | Singleton Anthropic wrapper |
| `trade_history.py` | ~80 | Unified trade history queries |
| `chart_indicators.py` | ~200 | Pure indicator computation |
| `chart_sr.py` | ~80 | Pure S/R level detection |
| `tests/` | ~300 | pytest suite, 20 tests |
| `CLAUDE.md` | ~50 | Subagent context file |

---

*Full implementation details were in the original 1,319-line version of this file.*
*Compressed after completion on 2026-05-10.*
