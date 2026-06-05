# Optimization Roadmap — Trading Journal v1.5.0

> Master document. Each section links to a focused implementation plan.
> Sources: architecture review, code quality review, security review (2026-05-14).
> Baseline: **213 tests passing** on Pi and Mac.

---

## Priority order

| Plan | Effort | Value | Risk | Do first when… |
|------|--------|-------|------|----------------|
| **A — Quick Wins** | S (1-2h) | High | Low | Always — unblocks clean CI |
| **B — Async Optimizer + Performance** | M (3-4h) | High | Low | Pi reliability matters |
| **C — Agent Pipeline Cleanup** | M (3-4h) | Medium | Low | Adding new agents or signals |
| **D — Architecture Refactoring** | L (1-2 days) | High | Medium | Before adding 3rd exchange |

---

## Plan A — Quick Wins
`docs/superpowers/plans/2026-05-15-opt-a-quick-wins.md`

Fix 3 pre-existing test failures, patch the confluence denominator mismatch, escape one remaining XSS vector, fix migration numbering comment.

**Delivers:** 216/216 tests green on both Mac and Pi.

---

## Plan B — Async Optimizer + Performance
`docs/superpowers/plans/2026-05-15-opt-b-async-performance.md`

Make `/api/backtest/optimize` non-blocking (currently freezes Flask for 5-15 min). Add performance regression tests. Profile and tune `_compute_signals()` vectorization.

**Delivers:** Optimizer runs in background; journal stays responsive; performance baselines locked.

---

## Plan C — Agent Pipeline Cleanup
`docs/superpowers/plans/2026-05-15-opt-c-agent-pipeline.md`

Extract `compute_consensus` + `add_gemini_consensus` into `consensus.py`. Remove `agent_orchestrator ↔ agent_trade_prep` circular import. Add `ScannerSetup` TypedDict. Add integration test for `run_call_analysis()`.

**Delivers:** Import cycle broken; consensus logic independently testable; scanner protocol typed.

---

## Plan D — Architecture Refactoring
`docs/superpowers/plans/2026-05-15-opt-d-architecture.md`

Split `chart_context.py` (774 lines, 7 concerns) into 4 focused modules. Extract `sync_base.py` + `SyncDriver` protocol to collapse 200 lines of duplication between `bitget_sync` and `blofin_sync` and remove the cross-module private import.

**Delivers:** chart_context changes are locally contained; adding a 3rd exchange (Hyperliquid) is a 1-file addition.

---

## What is NOT in scope here

- LLM provider unification (Anthropic/Gemini/Grok → single interface) — L effort, deferred to v1.6
- `helpers.py` decomposition — S effort but low urgency; do as part of D
- FreqAI (F7) — deferred per earlier decision
- Hyblock Capital — deferred pending credentials
