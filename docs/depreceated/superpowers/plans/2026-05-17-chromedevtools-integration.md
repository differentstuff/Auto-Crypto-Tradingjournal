# Chrome DevTools MCP Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the chrome-devtools-mcp plugin into the trading journal so Claude automatically runs browser verification after major UI deploys — tab sweep, Lighthouse audits, interaction checks — reads the HTML report, fixes any failures inline, and commits a clean report.

**Architecture:** Five static artifacts (`.mcp.json`, `browser_test_sequence.json`, `generate_browser_report.py`, `browser_issues.md`, CLAUDE.md update) plus a `.gitignore` entry. No new pip deps. The MCP tools are called by Claude in-session; `generate_browser_report.py` is a pure Python stdlib report renderer that Claude calls after collecting results.

**Tech Stack:** Python 3.13 stdlib (`json`, `datetime`, `base64`, `string`, `sys`), chrome-devtools-mcp@0.22.0 (already installed), vanilla JS SPA navigation via `evaluate_script("showPage('name')")`.

**Critical SPA detail:** The trading journal uses `showPage('name')` for tab switching — there is no URL hash routing. All tab navigation in the test sequence uses `evaluate_script`, not URL navigation.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `.mcp.json` | Create | Register chrome-devtools-mcp MCP server |
| `scripts/browser_test_sequence.json` | Create | Machine-readable test spec with exact selectors |
| `scripts/generate_browser_report.py` | Create | JSON results → self-contained HTML report |
| `scripts/browser_issues.md` | Create | Warning backlog (starts empty) |
| `tests/test_generate_browser_report.py` | Create | Unit tests for report generator |
| `.gitignore` | Modify | Add `.claude/settings.local.json` |
| `CLAUDE.md` | Modify | Add browser verification section |

---

## Task 1: `.mcp.json` — Register MCP server

**Files:**
- Create: `.mcp.json`

- [ ] **Step 1: Create `.mcp.json`**

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

- [ ] **Step 2: Add `.claude/settings.local.json` to `.gitignore`**

Open `.gitignore`. Append at the end:

```
.claude/settings.local.json
```

This file is where local path overrides live (e.g. if `npx` is not on PATH, set `"command": "/absolute/path/to/npx"` there instead of in the committed `.mcp.json`).

- [ ] **Step 3: Verify `.mcp.json` is not accidentally ignored**

```bash
git check-ignore -v .mcp.json
```

Expected: no output (file is NOT ignored — it should be committed).

- [ ] **Step 4: Commit**

```bash
git add .mcp.json .gitignore
git commit -m "feat: register chrome-devtools-mcp MCP server for trading journal"
```

---

## Task 2: `scripts/browser_test_sequence.json` — Test spec

**Files:**
- Create: `scripts/browser_test_sequence.json`

The SPA uses `showPage('name')` for navigation. All tab switching uses `evaluate_script`, not URL anchors. Page section IDs follow the pattern `page-{name}`.

- [ ] **Step 1: Create `scripts/browser_test_sequence.json`**

```json
{
  "_comment": "Trading Journal browser test sequence. Navigation uses evaluate_script('showPage(name)') — this SPA has no URL hash routing.",
  "host": "http://192.168.1.21:8082",
  "wait_after_initial_navigate_ms": 3000,
  "wait_after_tab_switch_ms": 2000,
  "dom_node_blank_threshold": 20,

  "phase1_tabs": [
    {"name": "Dashboard",      "page_name": "dashboard",  "section_id": "page-dashboard"},
    {"name": "Trades",         "page_name": "trades",     "section_id": "page-trades"},
    {"name": "Call Analyzer",  "page_name": "calls",      "section_id": "page-calls"},
    {"name": "Pending Orders", "page_name": "pending",    "section_id": "page-pending"},
    {"name": "Live Sync",      "page_name": "live",       "section_id": "page-live"},
    {"name": "Journal",        "page_name": "journal",    "section_id": "page-journal"},
    {"name": "Hindsight",      "page_name": "hindsight",  "section_id": "page-hindsight"},
    {"name": "Chart Explorer", "page_name": "charts",     "section_id": "page-charts"},
    {"name": "Deep Dive",      "page_name": "deep",       "section_id": "page-deep"},
    {"name": "Edge Lab",       "page_name": "edge",       "section_id": "page-edge"},
    {"name": "Risk",           "page_name": "risk",       "section_id": "page-risk"},
    {"name": "AI Advisor",     "page_name": "ai",         "section_id": "page-ai"},
    {"name": "Setup Scanner",  "page_name": "scanner",    "section_id": "page-scanner"},
    {"name": "Import Data",    "page_name": "import",     "section_id": "page-import"},
    {"name": "Data Sources",   "page_name": "sources",    "section_id": "page-sources"},
    {"name": "Settings",       "page_name": "settings",   "section_id": "page-settings"}
  ],

  "phase2_lighthouse": [
    {"page": "Dashboard",     "page_name": "dashboard", "a11y_min": 80, "perf_min": 70},
    {"page": "Live Trades",   "page_name": "trades",    "a11y_min": 80, "perf_min": 70},
    {"page": "Call Analyzer", "page_name": "calls",     "a11y_min": 80, "perf_min": 60},
    {"page": "Scanner",       "page_name": "scanner",   "a11y_min": 80, "perf_min": 60}
  ],

  "phase3_interactions": [
    {
      "name": "Call Analyzer form",
      "page_name": "calls",
      "click_selector": "#call-analyze-btn",
      "wait_ms": 2000,
      "pass_selector": "#saved-calls-list,#analyst-stats-content,.call-result"
    },
    {
      "name": "Scanner start",
      "page_name": "scanner",
      "click_selector": "#btn-scan",
      "wait_ms": 3000,
      "pass_selector": "#scanner-meta,#scanner-feedback,#scanner-results"
    },
    {
      "name": "Chart Explorer canvas",
      "page_name": "charts",
      "click_selector": null,
      "wait_ms": 3000,
      "pass_selector": "canvas"
    }
  ],

  "phase4_full_scan": [
    {
      "name": "Call Analyzer E2E",
      "page_name": "calls",
      "steps": [
        {"action": "fill", "selector": "#sz-entry",    "value": "104000"},
        {"action": "fill", "selector": "#sz-sl",       "value": "100000"},
        {"action": "fill", "selector": "#sz-risk",     "value": "1"},
        {"action": "click","selector": "#call-analyze-btn"},
        {"action": "wait", "ms": 90000}
      ],
      "pass_selector": "#saved-calls-list .call-card,#analyst-stats-content"
    },
    {
      "name": "Scanner full run",
      "page_name": "scanner",
      "steps": [
        {"action": "click","selector": "#btn-scan"},
        {"action": "wait", "ms": 120000}
      ],
      "pass_selector": ".scanner-row,#scanner-results tr"
    },
    {
      "name": "Chart Explorer S&R",
      "page_name": "charts",
      "steps": [
        {"action": "fill", "selector": "#explorer-symbol", "value": "BTCUSDT"},
        {"action": "click","selector": "#btn-analyze"},
        {"action": "wait", "ms": 5000}
      ],
      "pass_selector": "canvas"
    },
    {
      "name": "Risk tab content",
      "page_name": "risk",
      "steps": [
        {"action": "wait", "ms": 3000}
      ],
      "pass_selector": ".risk-card,.kpi-card,table"
    }
  ],

  "thresholds": {
    "fail_on_console_error": true,
    "fail_on_dom_below_threshold": true,
    "fail_on_5xx": true,
    "fail_on_a11y_below_min": true,
    "warn_on_perf_below_min": true,
    "fail_on_perf_below_50": true
  }
}
```

- [ ] **Step 2: Validate JSON parses correctly**

```bash
python3 -c "import json; d=json.load(open('scripts/browser_test_sequence.json')); print(f\"Tabs: {len(d['phase1_tabs'])}, Lighthouse: {len(d['phase2_lighthouse'])}, Interactions: {len(d['phase3_interactions'])}, Full scan: {len(d['phase4_full_scan'])}\")"
```

Expected output:
```
Tabs: 16, Lighthouse: 4, Interactions: 3, Full scan: 4
```

- [ ] **Step 3: Commit**

```bash
git add scripts/browser_test_sequence.json
git commit -m "feat: browser test sequence — 16-tab sweep, 4 Lighthouse audits, 3 interactions, 4 E2E full scan"
```

---

## Task 3: `scripts/generate_browser_report.py` + tests

**Files:**
- Create: `scripts/generate_browser_report.py`
- Create: `tests/test_generate_browser_report.py`

The generator accepts a JSON results file, produces a self-contained HTML file. Pure stdlib — no dependencies.

**Input JSON shape** (what Claude assembles and passes to this script):

```json
{
  "timestamp": "2026-05-17T23:48:00",
  "host": "192.168.1.21:8082",
  "tabs": [
    {
      "name": "Dashboard",
      "page_name": "dashboard",
      "status": "pass",
      "console_errors": [],
      "screenshot_b64": "iVBORw0KGgo...",
      "dom_node_count": 342,
      "load_ms": 420
    }
  ],
  "lighthouse": [
    {"page": "Dashboard", "accessibility": 89, "performance": 74}
  ],
  "interactions": [
    {"name": "Add Call form", "status": "pass", "elapsed_ms": 380}
  ]
}
```

- [ ] **Step 1: Write failing tests**

```python
# tests/test_generate_browser_report.py
import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from generate_browser_report import generate_report, _status_icon, _lighthouse_class


def _minimal_results(tab_status="pass", console_errors=None, a11y=85, perf=72):
    return {
        "timestamp": "2026-05-17T23:48:00",
        "host": "192.168.1.21:8082",
        "tabs": [
            {
                "name": "Dashboard",
                "page_name": "dashboard",
                "status": tab_status,
                "console_errors": console_errors or [],
                "screenshot_b64": "",
                "dom_node_count": 342,
                "load_ms": 420,
            }
        ],
        "lighthouse": [
            {"page": "Dashboard", "accessibility": a11y, "performance": perf}
        ],
        "interactions": [
            {"name": "Add Call form", "status": "pass", "elapsed_ms": 380}
        ],
    }


def test_generate_report_produces_html_file():
    results = _minimal_results()
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = f.name
    try:
        generate_report(results, out_path)
        assert os.path.exists(out_path)
        content = open(out_path).read()
        assert content.startswith("<!DOCTYPE html>")
        assert "192.168.1.21:8082" in content
        assert "Dashboard" in content
    finally:
        os.unlink(out_path)


def test_fail_tab_appears_in_report():
    results = _minimal_results(
        tab_status="fail",
        console_errors=["TypeError: Cannot read 'score' at 07-calls.js:241"]
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = f.name
    try:
        generate_report(results, out_path)
        content = open(out_path).read()
        assert "07-calls.js:241" in content
        assert "❌" in content
    finally:
        os.unlink(out_path)


def test_summary_counts_are_correct():
    results = {
        "timestamp": "2026-05-17T23:48:00",
        "host": "192.168.1.21:8082",
        "tabs": [
            {"name": "Dashboard", "page_name": "dashboard", "status": "pass",
             "console_errors": [], "screenshot_b64": "", "dom_node_count": 300, "load_ms": 400},
            {"name": "Calls",     "page_name": "calls",     "status": "fail",
             "console_errors": ["TypeError"], "screenshot_b64": "", "dom_node_count": 5, "load_ms": 200},
        ],
        "lighthouse": [{"page": "Dashboard", "accessibility": 89, "performance": 74}],
        "interactions": [{"name": "Add Call", "status": "pass", "elapsed_ms": 300}],
    }
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        out_path = f.name
    try:
        generate_report(results, out_path)
        content = open(out_path).read()
        assert "1/2" in content   # 1 pass out of 2 tabs
        assert "FAIL" in content
    finally:
        os.unlink(out_path)


def test_status_icon():
    assert _status_icon("pass") == "✅"
    assert _status_icon("fail") == "❌"
    assert _status_icon("warn") == "⚠️"
    assert _status_icon("unknown") == "❓"


def test_lighthouse_class_thresholds():
    assert _lighthouse_class(85, 80) == "pass"
    assert _lighthouse_class(79, 80) == "fail"
    assert _lighthouse_class(55, 50) == "warn"
    assert _lighthouse_class(49, 50) == "fail"
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python3 -m pytest tests/test_generate_browser_report.py -v
```

Expected: `ModuleNotFoundError: No module named 'generate_browser_report'`

- [ ] **Step 3: Implement `scripts/generate_browser_report.py`**

```python
#!/usr/bin/env python3
"""
Generate a self-contained HTML browser test report from a JSON results file.

Usage:
    python3 scripts/generate_browser_report.py results.json
    python3 scripts/generate_browser_report.py results.json --out scripts/browser_test_report.html

Output: scripts/browser_test_report.html (default) or path from --out.
No external dependencies — pure stdlib.
"""
import json
import sys
import os
from datetime import datetime

_DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "browser_test_report.html")

_CSS = """
body{font-family:system-ui,sans-serif;background:#0f1117;color:#e2e8f0;margin:0;padding:24px}
h1{font-size:1.3rem;color:#a78bfa;margin:0 0 4px}
.meta{font-size:.8rem;color:#64748b;margin-bottom:24px}
.summary{display:flex;gap:20px;margin-bottom:28px;flex-wrap:wrap}
.pill{padding:6px 14px;border-radius:20px;font-size:.85rem;font-weight:600}
.pill.ok{background:#14532d;color:#4ade80}
.pill.warn{background:#713f12;color:#fbbf24}
.pill.fail{background:#450a0a;color:#f87171}
h2{font-size:1rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.08em;margin:24px 0 10px}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;padding:8px 10px;border-bottom:1px solid #1e293b;color:#64748b}
td{padding:8px 10px;border-bottom:1px solid #1e293b;vertical-align:top}
tr:hover td{background:#1e293b}
.pass{color:#4ade80} .fail{color:#f87171} .warn{color:#fbbf24}
.errors{font-size:.78rem;color:#f87171;margin-top:4px;font-family:monospace}
.screenshot{max-width:240px;border:1px solid #334155;border-radius:4px;cursor:pointer}
.screenshot:hover{max-width:100%;position:relative;z-index:10}
.score-ok{color:#4ade80} .score-warn{color:#fbbf24} .score-fail{color:#f87171}
"""

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Browser Test Report</title>
<style>{css}</style></head>
<body>
<h1>Browser Test Report</h1>
<div class="meta">{timestamp} &nbsp;|&nbsp; {host}</div>
<div class="summary">
  <span class="pill {tabs_cls}">Tabs {tabs_pass}/{tabs_total}</span>
  <span class="pill {lh_cls}">Lighthouse {lh_pass}/{lh_total}</span>
  <span class="pill {ix_cls}">Interactions {ix_pass}/{ix_total}</span>
  <span class="pill fail" style="{fail_display}">FAIL {fail_count}</span>
  <span class="pill warn" style="{warn_display}">WARN {warn_count}</span>
</div>

<h2>Tab Results</h2>
<table>
<tr><th>Tab</th><th>Status</th><th>Console errors</th><th>DOM nodes</th><th>Load ms</th><th>Screenshot</th></tr>
{tab_rows}
</table>

<h2>Lighthouse Audits</h2>
<table>
<tr><th>Page</th><th>Accessibility</th><th>Performance</th></tr>
{lh_rows}
</table>

<h2>Interactions</h2>
<table>
<tr><th>Check</th><th>Status</th><th>Elapsed ms</th></tr>
{ix_rows}
</table>

{errors_section}
</body></html>
"""


def _status_icon(status: str) -> str:
    return {"pass": "✅", "fail": "❌", "warn": "⚠️"}.get(status, "❓")


def _lighthouse_class(score: float, threshold: float) -> str:
    if score >= threshold:
        return "pass"
    if score >= 50:
        return "warn"
    return "fail"


def generate_report(results: dict, out_path: str = _DEFAULT_OUT) -> str:
    tabs       = results.get("tabs", [])
    lighthouse = results.get("lighthouse", [])
    interactions = results.get("interactions", [])

    tabs_pass = sum(1 for t in tabs if t.get("status") == "pass")
    lh_pass   = sum(1 for l in lighthouse
                    if l.get("accessibility", 0) >= 80 and l.get("performance", 0) >= 50)
    ix_pass   = sum(1 for i in interactions if i.get("status") == "pass")
    fail_count = sum(1 for t in tabs if t.get("status") == "fail")
    warn_count = sum(1 for t in tabs if t.get("status") == "warn") + \
                 sum(1 for l in lighthouse
                     if 50 <= l.get("performance", 100) < 70 or 50 <= l.get("accessibility", 100) < 80)

    def _pill_cls(n_pass, n_total):
        if n_pass == n_total:
            return "ok"
        if n_pass < n_total * 0.8:
            return "fail"
        return "warn"

    tab_rows = ""
    all_errors = []
    for t in tabs:
        icon  = _status_icon(t.get("status", "unknown"))
        errs  = t.get("console_errors", [])
        if errs:
            all_errors.append((t["name"], errs))
        err_html = f'<div class="errors">' + "<br>".join(errs[:3]) + "</div>" if errs else ""
        ss   = t.get("screenshot_b64", "")
        img  = f'<img class="screenshot" src="data:image/png;base64,{ss}" alt="">' if ss else "—"
        tab_rows += (
            f"<tr><td>{t.get('name','?')}</td>"
            f"<td class=\"{'pass' if t.get('status')=='pass' else 'fail'}\">{icon}</td>"
            f"<td>{err_html or '—'}</td>"
            f"<td>{t.get('dom_node_count','?')}</td>"
            f"<td>{t.get('load_ms','?')}</td>"
            f"<td>{img}</td></tr>\n"
        )

    lh_rows = ""
    for l in lighthouse:
        a   = l.get("accessibility", 0)
        p   = l.get("performance", 0)
        a_c = _lighthouse_class(a, 80)
        p_c = _lighthouse_class(p, 70)
        lh_rows += (
            f"<tr><td>{l.get('page','?')}</td>"
            f"<td class=\"score-{a_c}\">{a} {'✅' if a_c=='pass' else '⚠️' if a_c=='warn' else '❌'}</td>"
            f"<td class=\"score-{p_c}\">{p} {'✅' if p_c=='pass' else '⚠️' if p_c=='warn' else '❌'}</td></tr>\n"
        )

    ix_rows = ""
    for i in interactions:
        icon = _status_icon(i.get("status", "unknown"))
        ix_rows += (
            f"<tr><td>{i.get('name','?')}</td>"
            f"<td class=\"{'pass' if i.get('status')=='pass' else 'fail'}\">{icon}</td>"
            f"<td>{i.get('elapsed_ms','?')}</td></tr>\n"
        )

    errors_section = ""
    if all_errors:
        rows = "".join(
            f"<tr><td><b>{name}</b></td><td><div class='errors'>"
            + "<br>".join(errs) + "</div></td></tr>"
            for name, errs in all_errors
        )
        errors_section = (
            "<h2>Console Errors</h2>"
            "<table><tr><th>Tab</th><th>Errors</th></tr>"
            + rows + "</table>"
        )

    ts = results.get("timestamp", datetime.now().isoformat())
    html = _HTML.format(
        css=_CSS,
        timestamp=ts,
        host=results.get("host", ""),
        tabs_pass=tabs_pass,
        tabs_total=len(tabs),
        tabs_cls=_pill_cls(tabs_pass, len(tabs)) if tabs else "ok",
        lh_pass=lh_pass,
        lh_total=len(lighthouse),
        lh_cls=_pill_cls(lh_pass, len(lighthouse)) if lighthouse else "ok",
        ix_pass=ix_pass,
        ix_total=len(interactions),
        ix_cls=_pill_cls(ix_pass, len(interactions)) if interactions else "ok",
        fail_count=fail_count,
        fail_display="" if fail_count else "display:none",
        warn_count=warn_count,
        warn_display="" if warn_count else "display:none",
        tab_rows=tab_rows,
        lh_rows=lh_rows,
        ix_rows=ix_rows,
        errors_section=errors_section,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate browser test HTML report")
    parser.add_argument("results_json", help="Path to JSON results file")
    parser.add_argument("--out", default=_DEFAULT_OUT, help="Output HTML path")
    args = parser.parse_args()

    with open(args.results_json, encoding="utf-8") as f:
        results = json.load(f)
    out = generate_report(results, args.out)
    print(f"Report written to {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python3 -m pytest tests/test_generate_browser_report.py -v
```

Expected:
```
PASSED tests/test_generate_browser_report.py::test_generate_report_produces_html_file
PASSED tests/test_generate_browser_report.py::test_fail_tab_appears_in_report
PASSED tests/test_generate_browser_report.py::test_summary_counts_are_correct
PASSED tests/test_generate_browser_report.py::test_status_icon
PASSED tests/test_generate_browser_report.py::test_lighthouse_class_thresholds
5 passed
```

- [ ] **Step 5: Run full test suite — no regressions**

```bash
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

Expected: same passing count as before (437+), 9 pre-existing failures only.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_browser_report.py tests/test_generate_browser_report.py
git commit -m "feat: generate_browser_report.py — JSON results to self-contained HTML with embedded screenshots"
```

---

## Task 4: `scripts/browser_issues.md` — Warning backlog

**Files:**
- Create: `scripts/browser_issues.md`

- [ ] **Step 1: Create `scripts/browser_issues.md`**

```markdown
# Browser Issues Backlog

Open items from browser test runs that need investigation (non-critical / complex WARNs).
Critical FAILs are fixed inline during the test run and do not appear here.

Format:
## YYYY-MM-DD — [Tab] [metric] ([value] vs target [target])
[Lighthouse finding or console message]
Priority: High / Medium / Low

---

<!-- Issues are appended here by Claude during browser test runs -->
```

- [ ] **Step 2: Commit**

```bash
git add scripts/browser_issues.md
git commit -m "feat: browser_issues.md — warning backlog for non-critical browser test findings"
```

---

## Task 5: `CLAUDE.md` — Browser verification section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current CLAUDE.md deployment section**

```bash
grep -n "Deployment\|Deploy\|rsync\|systemctl" CLAUDE.md | head -20
```

- [ ] **Step 2: Add browser verification section after the Deployment section**

Find the `## Deployment (IMPORTANT)` section in `CLAUDE.md`. After the last line of that section (and before the next `##` heading), insert:

```markdown
## Browser Verification (major UI changes only)

Run when a deploy touches `static/js/*.js`, `templates/*.html`, or adds new UI components.
**Skip** for backend-only deploys, migrations, config changes, and bug fixes.

### Starting a browser test session
```bash
open -a 'Google Chrome' --args \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/claude-chrome-debug \
  'http://192.168.1.21:8082'
sleep 3
```

### Running the test sequence
Read `scripts/browser_test_sequence.json` and execute each phase using chrome-devtools-mcp tools:

**Tab navigation** — this SPA has no URL hash routing, use `evaluate_script`:
```js
showPage('dashboard')   // switches to Dashboard tab
```
Then `wait_for("#page-dashboard.active")` before screenshot.

**Per tab:** `evaluate_script` → `wait_for` section active → `take_screenshot` → `list_console_messages` → `evaluate_script("document.querySelectorAll('*').length")` (FAIL if < 20)

**Lighthouse:** `lighthouse_audit` on the 4 pages in `phase2_lighthouse`. Targets in the JSON.

**Interactions:** navigate to tab → `click` selector → `wait_for` pass selector → record elapsed.

### Generating the report
Collect all results into a JSON dict matching the shape in `scripts/generate_browser_report.py` docstring, save as `scripts/browser_test_results_tmp.json`, then:
```bash
python3 scripts/generate_browser_report.py scripts/browser_test_results_tmp.json
```
Report saved to `scripts/browser_test_report.html`.

### Triage
- **FAIL** (console error, DOM < 20 nodes, 5xx, a11y < 80, perf < 50): fix inline, re-test tab, verify clean.
- **WARN obvious** (missing aria-label, button missing type): one-liner fix, no re-test.
- **WARN complex** (perf regression, heading hierarchy): append to `scripts/browser_issues.md`.

### Commit when clean
```bash
git add scripts/browser_test_report.html
git commit -m "test: browser check clean — vX.Y.Z"
```

### Full scan (Phase 4 — one-time baseline or after major UI overhaul)
After standard Phases 1–3 pass, run `phase4_full_scan` steps from the JSON.
Use after: new tab added, major component redesign, v2.0 milestone.
```

- [ ] **Step 3: Verify CLAUDE.md structure is intact**

```bash
grep -n "^## " CLAUDE.md
```

Expected: list of section headings including the new `## Browser Verification` section, no broken formatting.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add browser verification workflow to CLAUDE.md — trigger criteria, navigation pattern, triage rules"
```

---

## Task 6: Smoke-test the full integration

**No new files — verification only.**

This task verifies the MCP server starts, the test sequence JSON is valid, and the report generator produces correct output end-to-end.

- [ ] **Step 1: Verify MCP server config is readable**

```bash
python3 -c "import json; d=json.load(open('.mcp.json')); print('MCP server:', list(d['mcpServers'].keys()))"
```

Expected:
```
MCP server: ['chrome-devtools']
```

- [ ] **Step 2: Verify test sequence has all 16 tabs**

```bash
python3 -c "
import json
d = json.load(open('scripts/browser_test_sequence.json'))
tabs = [t['page_name'] for t in d['phase1_tabs']]
expected = ['dashboard','trades','calls','pending','live','journal','hindsight',
            'charts','deep','edge','risk','ai','scanner','import','sources','settings']
missing = set(expected) - set(tabs)
extra   = set(tabs) - set(expected)
print('OK' if not missing and not extra else f'MISSING: {missing}  EXTRA: {extra}')
print('Tab count:', len(tabs))
"
```

Expected:
```
OK
Tab count: 16
```

- [ ] **Step 3: Run report generator with synthetic data**

```bash
python3 -c "
import json, tempfile, os, sys
sys.path.insert(0, 'scripts')
from generate_browser_report import generate_report

results = {
    'timestamp': '2026-05-17T23:48:00',
    'host': '192.168.1.21:8082',
    'tabs': [
        {'name': 'Dashboard', 'page_name': 'dashboard', 'status': 'pass',
         'console_errors': [], 'screenshot_b64': '', 'dom_node_count': 342, 'load_ms': 420},
        {'name': 'Calls', 'page_name': 'calls', 'status': 'fail',
         'console_errors': ['TypeError: Cannot read score at 07-calls.js:241'],
         'screenshot_b64': '', 'dom_node_count': 5, 'load_ms': 200},
    ],
    'lighthouse': [{'page': 'Dashboard', 'accessibility': 89, 'performance': 74}],
    'interactions': [{'name': 'Add Call form', 'status': 'pass', 'elapsed_ms': 380}],
}
out = generate_report(results, '/tmp/test_report.html')
size = os.path.getsize(out)
content = open(out).read()
assert '07-calls.js:241' in content
assert 'FAIL' in content
assert '1/2' in content
print(f'Report OK: {size} bytes, contains expected error, FAIL badge, tab count')
"
```

Expected:
```
Report OK: XXXX bytes, contains expected error, FAIL badge, tab count
```

- [ ] **Step 4: Run full test suite — confirm no regressions**

```bash
python3 -m pytest tests/ -q --tb=no 2>&1 | tail -3
```

Expected: 442+ passing (437 existing + 5 new), 9 pre-existing failures.

- [ ] **Step 5: Final commit and push**

```bash
git push
echo "Chrome DevTools MCP integration complete"
```

---

## Summary

| Task | Files created/modified | Tests |
|------|----------------------|-------|
| 1 | `.mcp.json`, `.gitignore` | manual verify |
| 2 | `scripts/browser_test_sequence.json` | python3 json parse |
| 3 | `scripts/generate_browser_report.py`, `tests/test_generate_browser_report.py` | 5 pytest |
| 4 | `scripts/browser_issues.md` | — |
| 5 | `CLAUDE.md` | grep verify |
| 6 | — | end-to-end smoke |

**New tests added: 5** | **New pip deps: 0** | **MCP tools used: in-session by Claude, not by scripts**
