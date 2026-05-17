# Chrome DevTools MCP — Deploy Verification Integration Design

## Overview

**Goal:** Add browser-layer verification to the post-deploy quality loop. After every Pi deploy, Claude automatically navigates the trading journal UI using Chrome DevTools MCP tools, runs Lighthouse audits, checks interactions, reads the generated report, fixes all failures inline, and commits a clean report.

**Plugin:** `chrome-devtools-mcp@0.22.0` (already installed at `~/.claude/plugins/cache/claude-plugins-official/chrome-devtools-mcp/`).

**Target:** `http://192.168.1.21:8082` (Pi, always-on, live data).

---

## Architecture

```
systemctl restart trading-journal
        │
        ▼  (PostToolUse hook)
Launch Chrome --remote-debugging-port=9222 → http://192.168.1.21:8082
        │
        ▼  (Claude reads browser_test_sequence.json, executes with MCP tools)
Phase 1: Tab sweep (16 tabs) → screenshots + console errors
Phase 2: Lighthouse audits (4 pages) → accessibility + performance scores
Phase 3: Interaction checks (3 flows) → form appears, scanner starts, chart renders
        │
        ▼  (generate_browser_report.py)
scripts/browser_test_report.html   ← timestamped, embedded screenshots, pass/fail per item
        │
        ▼  (Claude reads report)
FAIL?  → trace error → patch JS/Flask → re-run affected test → verify clean
WARN obvious? → one-liner fix applied immediately
WARN complex? → append to scripts/browser_issues.md
        │
        ▼
git add scripts/browser_test_report.html && git commit -m "test: browser check clean"
```

---

## File Structure

| File | Purpose |
|------|---------|
| `.mcp.json` | Registers chrome-devtools MCP server for this project |
| `scripts/browser_test_sequence.json` | Machine-readable test spec: tabs, interactions, audit targets, thresholds |
| `scripts/generate_browser_report.py` | Converts JSON results → HTML report with embedded screenshots |
| `scripts/browser_test_report.html` | Generated report (committed each run — latest report is review evidence) |
| `scripts/browser_issues.md` | Warning-tier backlog (committed, accumulates over time) |
| `.claude/settings.json` | PostToolUse hook that launches Chrome after systemctl restart |
| `CLAUDE.md` | Updated: browser test workflow + triage rules |

---

## `.mcp.json` (project root)

```json
{
  "mcpServers": {
    "chrome-devtools": {
      "command": "npx",
      "args": ["chrome-devtools-mcp@latest"]
    }
  }
}
```

> `npx` resolves via PATH — no hardcoded local paths. If `npx` is not on PATH in the Claude Code session, set `command` to the absolute path of `npx` via `which npx` locally and store it in `.claude/settings.local.json` (gitignored), not in `.mcp.json`.

---

## Test Sequence (`browser_test_sequence.json`)

### Phase 1 — Tab sweep

All 16 tabs navigated in order. For each:
- `navigate_page` to the tab anchor (`#dashboard`, `#live`, `#calls`, etc.)
- `take_screenshot` → stored as base64 in results
- `list_console_messages` → filter severity `error`
- `list_network_requests` → flag any 5xx responses

**FAIL conditions per tab:**
- Any `console.error` or uncaught exception
- DOM node count < 20 after 3s load wait (evaluated via `evaluate_script("document.querySelectorAll('*').length")` — catches blank/failed renders without image analysis)
- Network request returning 500/503

### Phase 2 — Lighthouse audits

Four pages audited via `lighthouse_audit`:

| Page | Accessibility target | Performance target |
|------|---------------------|-------------------|
| Dashboard | ≥ 80 | ≥ 70 |
| Live Trades | ≥ 80 | ≥ 70 |
| Call Analyzer | ≥ 80 | ≥ 60 (AI latency) |
| Setup Scanner | ≥ 80 | ≥ 60 (scan latency) |

**Severity mapping:**
- Accessibility < 80 → FAIL
- Performance < 50 → FAIL
- Performance 50–69 → WARN
- Both thresholds met → PASS

### Phase 3 — Interaction checks

| Check | Action | Pass condition |
|-------|--------|---------------|
| Call Analyzer form | `click` "Add Call" / "Analyze" button | Form or result card appears within 5s |
| Setup Scanner | `click` "Run Scanner" | Progress bar or stage indicator visible |
| Chart Explorer | Navigate to `#explorer`, enter BTCUSDT | Canvas element non-zero dimensions, no console error |

### Phase 4 — Full scan only (C baseline)

Run once after standard flow is confirmed clean. Not part of every deploy.

| Check | Action | Pass condition |
|-------|--------|---------------|
| Call Analyzer E2E | Fill BTCUSDT Long entry/SL/TP, submit | AI score card renders within 90s |
| Scanner E2E | Run scanner, wait for completion | ≥ 1 finalist row in results table |
| Chart S&R overlay | Open BTCUSDT chart | S&R level lines visible on chart canvas |
| Risk tab | Navigate to Risk | Correlation matrix table renders, no blank cards |

---

## Report Format (`browser_test_report.html`)

```
Browser Test Report — 2026-05-17 23:48 | Host: 192.168.1.21:8082

Summary: Tabs 15/16 ✅  Lighthouse 3/4 ✅  Interactions 3/3 ✅  FAIL 1  WARN 2

── Tab Results ─────────────────────────────────────────
✅ Dashboard        no errors                        [screenshot]
✅ Live Trades      no errors                        [screenshot]
❌ Call Analyzer    TypeError: Cannot read 'score'   [screenshot]
⚠️  Risk             Perf 48 (target ≥ 70)           [screenshot]
✅ Scanner          no errors                        [screenshot]
... (all 16 tabs)

── Lighthouse ──────────────────────────────────────────
Dashboard      A11y 89 ✅  Perf 74 ✅
Live Trades    A11y 76 ⚠️  Perf 71 ✅
Call Analyzer  A11y 82 ✅  Perf 61 ✅
Scanner        A11y 84 ✅  Perf 58 ✅

── Interactions ────────────────────────────────────────
Add Call form    ✅  (appeared in 0.4s)
Run Scanner      ✅  (progress bar visible)
Chart Explorer   ✅  (canvas 1200×600px)

── Console Errors ──────────────────────────────────────
[Call Analyzer] TypeError: Cannot read properties of undefined (reading 'score')
  at 07-calls.js:241
```

Report embeds screenshots inline (base64), styled with a minimal CSS table. One file, self-contained.

---

## Triage Rules (for Claude)

### FAIL — block, fix immediately

1. Read console error message + file:line from report
2. Open the JS file at that line
3. Identify root cause (undefined check missing, API response shape changed, etc.)
4. Patch the file
5. Re-run `navigate_page` + `list_console_messages` for the failing tab only
6. Verify clean, continue

### WARN — fix if obvious, backlog if not

**Obvious** (one-liner): missing `aria-label`, button without `type`, image without `alt`. Fix inline, no re-test needed.

**Non-obvious** (needs investigation): performance regression >20 points vs last report, layout broken on mobile viewport, Lighthouse flags a missing heading hierarchy. Append to `scripts/browser_issues.md`:

```markdown
## 2026-05-17 — Live Trades A11y 76 (target ≥80)
Lighthouse: "Buttons do not have an accessible name" — likely the direction badges in 08-live.js
Priority: Medium
```

### INFO — log only

Lighthouse "best practices" suggestions, non-critical deprecation warnings. Appear in report, no action.

---

## PostToolUse Hook

In `.claude/settings.json`, add a hook that fires after any Bash command matching `systemctl restart trading-journal`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "systemctl restart trading-journal",
        "hooks": [
          {
            "type": "command",
            "command": "open -a 'Google Chrome' --args --remote-debugging-port=9222 --user-data-dir=/tmp/claude-chrome-debug 'http://192.168.1.21:8082' 2>/dev/null; sleep 3 && echo 'Chrome launched for browser testing'"
          }
        ]
      }
    ]
  }
}
```

Chrome launches with a clean profile (`/tmp/claude-chrome-debug`) on port 9222. The 3s sleep gives it time to load before MCP tools connect.

---

## CLAUDE.md Addition

Add to the deployment section:

```markdown
## Post-Deploy Browser Verification

After every `systemctl restart trading-journal`:
1. Chrome launches automatically (PostToolUse hook, port 9222)
2. Run browser tests: read `scripts/browser_test_sequence.json` and execute each phase
   using chrome-devtools-mcp tools
3. Call `python3 scripts/generate_browser_report.py <results.json>` to produce
   `scripts/browser_test_report.html`
4. Read the report and triage:
   - FAIL → fix inline, re-test the failing tab/interaction, verify clean
   - WARN (obvious) → fix inline
   - WARN (complex) → append to `scripts/browser_issues.md`
5. Commit: `git add scripts/browser_test_report.html && git commit -m "test: browser check clean — vX.Y.Z"`

### Full scan (one-time or on-demand)
After standard check passes, run Phase 4 (E2E workflows). Use this to re-baseline after
major UI changes.
```

---

## `generate_browser_report.py` — Report Generator

Accepts a JSON file with this structure:

```json
{
  "timestamp": "2026-05-17T23:48:00",
  "host": "192.168.1.21:8082",
  "tabs": [
    {
      "name": "Dashboard",
      "anchor": "#dashboard",
      "status": "pass",
      "console_errors": [],
      "screenshot_b64": "...",
      "load_ms": 420
    }
  ],
  "lighthouse": [
    {
      "page": "Dashboard",
      "accessibility": 89,
      "performance": 74
    }
  ],
  "interactions": [
    {
      "name": "Add Call form",
      "status": "pass",
      "elapsed_ms": 380
    }
  ]
}
```

Produces a self-contained HTML file with inline CSS, embedded screenshots, and summary counts. No external dependencies — pure stdlib `string.Template` + base64.

---

## `browser_test_sequence.json` — Test Spec

```json
{
  "host": "http://192.168.1.21:8082",
  "wait_after_navigate_ms": 2000,
  "phase1_tabs": [
    {"name": "Dashboard",      "anchor": "#dashboard"},
    {"name": "Live Trades",    "anchor": "#live"},
    {"name": "Call Analyzer",  "anchor": "#calls"},
    {"name": "Pending Orders", "anchor": "#pending"},
    {"name": "Live Sync",      "anchor": "#sync"},
    {"name": "Journal",        "anchor": "#journal"},
    {"name": "Hindsight",      "anchor": "#hindsight"},
    {"name": "Chart Explorer", "anchor": "#explorer"},
    {"name": "Deep Dive",      "anchor": "#deep"},
    {"name": "Edge Lab",       "anchor": "#edge"},
    {"name": "AI Advisor",     "anchor": "#advisor"},
    {"name": "Setup Scanner",  "anchor": "#scanner"},
    {"name": "Import Data",    "anchor": "#import"},
    {"name": "Data Sources",   "anchor": "#sources"},
    {"name": "Settings",       "anchor": "#settings"},
    {"name": "Risk",           "anchor": "#risk"}
  ],
  "phase2_lighthouse": [
    {"page": "Dashboard",     "anchor": "#dashboard", "a11y_min": 80, "perf_min": 70},
    {"page": "Live Trades",   "anchor": "#live",      "a11y_min": 80, "perf_min": 70},
    {"page": "Call Analyzer", "anchor": "#calls",     "a11y_min": 80, "perf_min": 60},
    {"page": "Scanner",       "anchor": "#scanner",   "a11y_min": 80, "perf_min": 60}
  ],
  "phase3_interactions": [
    {
      "name": "Add Call form",
      "steps": ["navigate:#calls", "click:.btn-analyze,.btn-add-call", "wait:2000"],
      "pass_selector": ".call-result,.call-form-modal"
    },
    {
      "name": "Run Scanner",
      "steps": ["navigate:#scanner", "click:#btn-run-scanner,.scanner-run-btn", "wait:3000"],
      "pass_selector": ".scanner-progress,.stage-progress,.scanner-stage"
    },
    {
      "name": "Chart Explorer",
      "steps": ["navigate:#explorer", "wait:3000"],
      "pass_selector": "canvas"
    }
  ],
  "phase4_full_scan": [
    {
      "name": "Call Analyzer E2E",
      "steps": [
        "navigate:#calls",
        "fill:#symbol-input:BTCUSDT",
        "fill:#direction-select:long",
        "fill:#entry-input:104000",
        "fill:#sl-input:100000",
        "fill:#tp1-input:110000",
        "click:#btn-analyze",
        "wait:90000"
      ],
      "pass_selector": ".setup-score,.ai-verdict,.verdict-card"
    },
    {
      "name": "Scanner E2E",
      "steps": ["navigate:#scanner", "click:#btn-run-scanner,.scanner-run-btn", "wait:120000"],
      "pass_selector": ".finalist-row,.setup-card"
    },
    {
      "name": "Chart S&R",
      "steps": ["navigate:#explorer", "fill:#symbol-input:BTCUSDT", "wait:5000"],
      "pass_selector": ".sr-level,canvas"
    },
    {
      "name": "Risk tab",
      "steps": ["navigate:#risk", "wait:3000"],
      "pass_selector": ".correlation-matrix,.risk-card"
    }
  ],
  "thresholds": {
    "fail_on_console_error": true,
    "fail_on_blank_body": true,
    "fail_on_5xx": true
  }
}
```

---

## Scope Boundaries

**In scope:**
- Frontend JS errors at runtime
- Blank / broken tabs
- Lighthouse accessibility regressions
- Lighthouse performance regressions
- Primary user-facing interactions

**Out of scope:**
- Backend logic correctness (covered by `self_test.py`)
- Mobile device emulation (desktop Chrome only)
- Cross-browser testing (Chrome only — matches Pi user base)
- Visual regression diffing (screenshots for human review, not pixel-diff)
- CI/CD pipeline (runs in Claude Code session, not GitHub Actions)
