# Architecture Review & Optimisation Plan
**Date:** 2026-05-09  
**Version at review:** v2.7.2 + post-patch (commit 843c96b)  
**Reviewer:** Claude Sonnet 4.6 via Claude Code

---

## 1. Current State Summary

| Metric | Value |
|--------|-------|
| Python files | 38 (~10,000 lines) |
| JS modules | 17 (~4,400 lines) |
| API endpoints | 65 |
| DB tables | 10 |
| AI calls (distinct modules) | 11 |
| Largest file | chart_context.py (1,140 lines) |
| Test coverage | None (integration via UI only) |

---

## 2. Architecture Issues — Prioritised

### P0 — Blocking / High Risk

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| P0-1 | **max_tokens 2048 in ai_rulebook.py** still not bumped (ai_advisor + ai_call fixed, rulebook left) | ai_rulebook.py | Rulebook truncation → bad rules injected into every prompt |
| P0-2 | **Cache race condition** — concurrent fetch both miss cache, both call Bitget API, last write wins | chart_context.py, nansen_client.py | Wasted API credits + potential rate-limit; stale data risk |
| P0-3 | **Hardcoded $1000 equity fallback** — if Bitget API fails, sizing uses $1000; no user alert | routes/calls.py L27-38 | Wrong position sizing shown silently |
| P0-4 | **Generic `except Exception: pass`** swallows failures in 9 places | helpers.py, nansen_client.py, ai_scanner.py | Debugging impossible; silent data loss |
| P0-5 | **No input allowlist on timeframe/symbol** passed to Bitget API | chart_context.py L66 | Potential API abuse / unexpected 400s |

### P1 — Significant Technical Debt

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| P1-1 | **chart_context.py at 1,140 lines** — candle fetch + 13 indicators + S/R + trendlines + caching all mixed | chart_context.py | Untestable; hard to modify one indicator without risk to others |
| P1-2 | **ai_scanner.py at 822 lines** — 3 stages + state + batch scoring + Telegram in one file | ai_scanner.py | Same issue; hard to tune stage thresholds |
| P1-3 | **_symbol_history() duplicated** in ai_call.py + ai_hindsight.py | Both | Divergence risk if one is updated |
| P1-4 | **No pagination** on analytics/deep, token-usage — entire history loaded into memory | routes/analytics.py | Fails at scale; slow on large trade history |
| P1-5 | **trader_rulebook wiped on every regen** — no versioning or audit trail | database.py, ai_rulebook.py | Can't compare rule quality over time |
| P1-6 | **cot_reasoning stored but never used** in subsequent calls | ai_call.py, analyzed_calls table | Wasted storage; missed learning signal |
| P1-7 | **analyzer_calls.setup_type mismatch** — input to Claude is None, output is ignored | ai_call.py L245, save endpoint | setup_type column always empty |
| P1-8 | **prompt_builder char budget (5,600) is unenforced** — can grow silently | prompt_builder.py | Context overflow → worse answers |

### P2 — Quality / Maintainability

| # | Issue | File(s) | Impact |
|---|-------|---------|--------|
| P2-1 | No foreign key constraints | database.py | Orphaned orders after position delete |
| P2-2 | Datetime stored as ISO strings, not UTC epoch | All tables | Timezone bugs if DB locale changes |
| P2-3 | Scanner spawns 8 parallel Bitget fetches per 30-min cycle | ai_scanner.py | Rate-limit risk; no backoff |
| P2-4 | ThreadPoolExecutor `max_workers` hardcoded; not tuned to Pi CPU count | Multiple | Thrash on Raspberry Pi 5 (4 cores) |
| P2-5 | Dead imports in several modules | ai_rulebook.py, routes/ | Clutter |
| P2-6 | Token usage not logged for all modules (ai_limit, ai_trade_grader missing) | helpers.py callers | Incomplete cost visibility |
| P2-7 | No structured logging; all `print()` | All | No log level, no timestamps, hard to filter on Pi |

---

## 3. Token Efficiency Audit

Current token flow per user action:

| Action | Modules Called | Approximate Tokens |
|--------|---------------|--------------------|
| Call analysis | ai_call → prompt_builder → chart_context | ~3,500 in / ~800 out |
| AI Advisor | ai_advisor → prompt_builder | ~4,000 in / ~600 out |
| Scanner (30 min) | ai_scanner ×N haiku + 1 sonnet batch | ~500 in/sym + ~2,000 batch |
| Hindsight (10 trades) | ai_hindsight ×10 haiku | ~800 in × 10 |
| Rulebook regen | ai_rulebook | ~3,000 in / ~500 out |

**Waste identified:**
- cot_reasoning (~200 tokens/call) stored but not reused
- `_has_tech_levels()` heuristic occasionally fetches chart when not needed
- Scanner batch prompt includes full market context per symbol (could be shared header once)
- Rulebook sent verbatim every call even when unchanged (prompt cache helps but only if context ≥ 4KB)

---

## 4. Implementation Plan — Ordered by Dependency

### Phase 0: Foundation Fixes (do before anything else)
*Nothing else should be built on broken foundations.*

- [ ] **P0-1** Verify ai_rulebook.py max_tokens is 4096 (was 2048 in audit)
- [ ] **P0-2** Fix cache race: use `threading.Lock` double-checked locking in chart_context.py and nansen_client.py
- [ ] **P0-3** Surface equity API failure to user (show warning banner, don't silently use $1000)
- [ ] **P0-4** Replace bare `except Exception: pass` with `except Exception as e: logger.warning(...)` — minimum 9 sites
- [ ] **P0-5** Add `VALID_TIMEFRAMES = {"1m","5m","15m","1H","4H","1D","1W"}` allowlist in chart_context.py
- [ ] **P1-7** Save setup_type from Claude's JSON response in analyzed_calls
- [ ] **P2-6** Add `log_token_usage()` to ai_limit.py and ai_trade_grader.py

### Phase 1: AI Quality (depends on Phase 0 stability)
*Improve the quality of what Claude produces before optimising structure.*

- [ ] Review call analyzer prompt — add explicit instruction: "return compact JSON, no prose outside JSON"
- [ ] Inject cot_reasoning from last call for same symbol into subsequent call prompts (learning loop)
- [ ] Add `setup_type` to the save flow so analyzed_calls.setup_type is populated
- [ ] Enforce prompt_builder char budget: truncate with ellipsis and log when over 5,600
- [ ] Add rulebook versioning: keep last 3 versions in trader_rulebook_history table
- [ ] Review scanner scoring rubric: calibrate Haiku quick-score vs Sonnet final-score alignment
- [ ] Scanner: share market context block as a single prefix, not repeated per-symbol in batch

### Phase 2: Structural Improvements (depends on Phase 1 — we now know what to keep)
*Split the large files only after understanding what each piece does.*

- [ ] Split chart_context.py:
  - `chart_fetch.py` — candle download + caching (cache race fix moves here)
  - `chart_indicators.py` — all pandas-ta + VMC Cipher computations
  - `chart_sr.py` — S/R detection, trendlines, confluence scoring
  - Keep chart_context.py as thin orchestrator
- [ ] Split ai_scanner.py:
  - `scanner_pipeline.py` — stage 1/2/3 logic
  - `scanner_batch.py` — batch Sonnet scoring
  - Keep ai_scanner.py as public interface + state
- [ ] Centralise `_symbol_history()` in helpers.py → remove from ai_call.py + ai_hindsight.py
- [ ] Add pagination to `/api/analytics/deep` and `/api/token-usage` (page/limit params)
- [ ] Replace all `print()` with Python `logging` module (configurable level via .env `LOG_LEVEL`)
- [ ] Tune `max_workers` to `min(8, os.cpu_count() or 4)` everywhere

### Phase 3: Feature Testing (depends on Phase 2 — stable structure)
*Systematic end-to-end test of all 65 endpoints.*

- [ ] Write self-test script: `scripts/self_test.py` — hits all GET endpoints, checks shape of response
- [ ] Test call analyzer with: minimal call, full call with image, parse-error-prone call
- [ ] Test scanner: full run, stage 1 only, calibration endpoint
- [ ] Test hindsight: 5-trade run, verify TP/FP/TN/FN verdicts are computed correctly
- [ ] Test sync: trigger, verify new positions appear, verify dedup works
- [ ] Test limits: create, analyze, trigger, close flow
- [ ] Verify auto-close of analyzed_calls when position closes
- [ ] Verify outcome recording (hit_tp1, hit_tp2, hit_sl) populates correctly
- [ ] Test Telegram: send test alert, verify channel + Updates topic both receive

### Phase 4: UI/UX Polish (depends on Phase 3 — tested features)
*Polish only what is confirmed to work correctly.*

- [ ] Dashboard: add total trade count + avg hold time to KPI strip
- [ ] Live trades: add "last refreshed" badge that turns red if >2 min stale
- [ ] Call analyzer: add symbol auto-complete from known traded symbols
- [ ] Scanner: add "last scan" timestamp + next scan ETA
- [ ] Journal: add bulk "mark all as reviewed" action
- [ ] Settings: show token cost breakdown per day/week (from token_usage table)
- [ ] Mobile: fix pos-header overflow on small screens (wraps awkwardly at <480px)
- [ ] Error states: all API error responses should surface a toast (currently some are silent)

---

## 5. Dependency Graph

```
Phase 0 (Foundation)
    ↓
Phase 1 (AI Quality)    ← requires stable error handling + correct token limits
    ↓
Phase 2 (Structure)     ← requires knowing which AI logic to keep/improve
    ↓
Phase 3 (Feature Test)  ← requires stable, well-structured code
    ↓
Phase 4 (UI/UX)         ← requires confirmed working features
```

Do NOT start Phase 1 before Phase 0 is complete.  
Do NOT start Phase 3 before Phase 2 is complete.  
Phase 1 and Phase 2 can partially overlap if structural changes are isolated.

---

## 6. Quick Wins (can be done any time, low risk)

- [ ] Remove `ai_call_analyzer.py` stub (11 lines, just a re-export — confusing)
- [ ] Add `CHANGELOG.md` entries for v2.7.x
- [ ] `.env.example` — add all new keys (NANSEN_API_KEY, FRED_API_KEY, TELEGRAM_BOT_TOKEN)
- [ ] Fix `ai_rulebook.py` dead import (`import traceback` unused)
- [ ] Add `log_token_usage()` to ai_limit.py (2-line addition)

---

## 7. Not In Scope (this review)

- Exchange integrations beyond Bitget/Blofin
- Multi-user / authentication
- Cloud deployment (stays on Pi)
- Historical backtesting engine
- Order execution API (read-only stays read-only)

---

## 8. Success Criteria

Phase 0 done when:
- No silent exception swallowing in hot paths
- Cache race condition eliminated
- All AI modules have correct max_tokens

Phase 1 done when:
- Call analyzer outputs properly structured JSON 100% of the time (no parse errors on standard calls)
- Token cost per call analyzer run ≤ 4,000 in + 1,200 out (with caching)

Phase 2 done when:
- No Python file over 500 lines
- `_symbol_history()` exists in exactly one place

Phase 3 done when:
- All 65 endpoints respond correctly with test data
- Self-test script passes 65/65

Phase 4 done when:
- No silent errors in any user-facing flow
- Works acceptably on 390px mobile width

---

## 9. Future Integration: Hyblock Capital (Liquidation Levels)

**Decision: evaluate after Phase 1. Free plan only.**

### What Hyblock offers
Hyblock Capital is a crypto derivatives analytics platform with an API providing liquidation level estimates — price zones where clusters of highly-leveraged positions (25×/50×/100×) would get wiped if price reaches them. These zones act as price magnets and are not available from any free public source.

### Key endpoint: `GET /cumulative-liq-level`
```
Base: https://api.hyblockcapital.com/v2
Params: coin (e.g. "BTC"), exchange, leverage ("low"|"medium"|"high"), timestamp
Returns:
  totalLongLiquidationSize   — total USD value of long liquidations predicted at this level
  totalShortLiquidationSize  — same for shorts
  totalSizeLiquidationDelta  — long minus short (positive = more long exposure = bearish if broken)
  totalLongLiquidationCount  — number of positions
  totalShortLiquidationCount
Auth: OAuth2 client credentials (bearer token) + x-api-key header
```

### Free plan scope (confirmed)
- Authentication required (OAuth2 + API key) — register at hyblockcapital.com
- Free plan exists; exact rate limits not published but "429 Too Many Requests" implies they exist
- Paid tiers (Professional/Advanced) unlock higher limits and more exchanges
- **Strategy:** use free plan, cache aggressively (30 min), only fetch for scanner finalists + call analyzer symbols

### Value to the journal
| Where | How it helps |
|-------|-------------|
| **Call analyzer** | Inject nearest long/short liquidation cluster into prompt — Claude can flag if TP2 sits just below a $200M short liquidation wall (strong magnet) or if SL is above a long cluster (extra stop-hunt risk) |
| **Scanner confluence** | Add as 8th confluence signal: +0.4 if short liquidation cluster within 2% above entry (fuel for breakout), −0.3 if long cluster below entry (stops hunt risk) |
| **Chart context** | Show liquidation delta as a horizontal band overlay on the chart popup |

### Implementation plan (when ready)
1. Create `hyblock_client.py` — OAuth2 token fetch + cache + `get_liq_levels(symbol, exchange="binance")` returning the delta
2. Add to `market_context.py` `format_for_prompt()` — one line: "Liq delta at nearest level: +$45M short exposure (bullish fuel if broken)"
3. Add to `chart_context.py` confluence scoring as signal 8
4. Store `HYBLOCK_API_KEY` + `HYBLOCK_CLIENT_ID` + `HYBLOCK_CLIENT_SECRET` in `.env`
5. Cache TTL: 30 min (same as Nansen). Only fetch for symbols actively in scanner or being analyzed.

### Risk / caveat
- Free plan rate limits unknown — may need to limit to top-5 scanner finalists only
- OAuth2 token expiry requires refresh logic (same pattern as any OAuth2 client credentials flow)
- Data is an *estimate* — Hyblock's liquidation levels are modelled, not sourced from exchange data directly

---

*Next session: start with Phase 0 items in order. Reference this doc at session start.*
