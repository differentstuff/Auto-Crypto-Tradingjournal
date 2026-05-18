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
import html
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


def _lighthouse_class(score, threshold: float) -> str:
    if score is None:
        return "warn"
    if score < threshold:
        return "fail"
    if score >= 70:
        return "pass"
    return "warn"


def generate_report(results: dict, out_path: str = _DEFAULT_OUT) -> str:
    tabs       = results.get("tabs", [])
    lighthouse = results.get("lighthouse", [])
    interactions = results.get("interactions", [])

    tabs_pass = sum(1 for t in tabs if t.get("status") == "pass")
    lh_pass   = sum(1 for l in lighthouse
                    if (l.get("accessibility") or 0) >= 80
                    and ((l.get("performance") or 100) >= 50))
    ix_pass   = sum(1 for i in interactions if i.get("status") == "pass")
    fail_count = sum(1 for t in tabs if t.get("status") == "fail")
    warn_count = sum(1 for t in tabs if t.get("status") == "warn") + \
                 sum(1 for l in lighthouse
                     if (50 <= (l.get("performance") or 100) < 70)
                     or (50 <= (l.get("accessibility") or 100) < 80))

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
        err_html = f'<div class="errors">' + "<br>".join(html.escape(e) for e in errs[:3]) + "</div>" if errs else ""
        ss   = t.get("screenshot_b64", "")
        img  = f'<img class="screenshot" src="data:image/png;base64,{ss}" alt="">' if ss else "—"
        status = t.get('status', 'unknown')
        status_cls = 'pass' if status == 'pass' else 'warn' if status == 'warn' else 'fail'
        tab_rows += (
            f"<tr><td>{html.escape(t.get('name','?'))}</td>"
            f"<td class=\"{status_cls}\">{icon}</td>"
            f"<td>{err_html or '—'}</td>"
            f"<td>{t.get('dom_node_count','?')}</td>"
            f"<td>{t.get('load_ms','?')}</td>"
            f"<td>{img}</td></tr>\n"
        )

    lh_rows = ""
    for l in lighthouse:
        a   = l.get("accessibility", 0)
        p   = l.get("performance")  # may be None (Playwright only, no Lighthouse)
        a_c = _lighthouse_class(a, 80)
        p_c = _lighthouse_class(p, 70)
        p_display = str(p) if p is not None else "N/A"
        lh_rows += (
            f"<tr><td>{html.escape(l.get('page','?'))}</td>"
            f"<td class=\"score-{a_c}\">{a} {'✅' if a_c=='pass' else '⚠️' if a_c=='warn' else '❌'}</td>"
            f"<td class=\"score-{p_c}\">{p_display} {'✅' if p_c=='pass' else '⚠️' if p_c=='warn' else '—'}</td></tr>\n"
        )

    ix_rows = ""
    for i in interactions:
        icon = _status_icon(i.get("status", "unknown"))
        ix_rows += (
            f"<tr><td>{html.escape(i.get('name','?'))}</td>"
            f"<td class=\"{'pass' if i.get('status')=='pass' else 'fail'}\">{icon}</td>"
            f"<td>{i.get('elapsed_ms','?')}</td></tr>\n"
        )

    errors_section = ""
    if all_errors:
        rows = "".join(
            f"<tr><td><b>{html.escape(name)}</b></td><td><div class='errors'>"
            + "<br>".join(html.escape(e) for e in errs[:10]) + "</div></td></tr>"
            for name, errs in all_errors
        )
        errors_section = (
            "<h2>Console Errors</h2>"
            "<table><tr><th>Tab</th><th>Errors</th></tr>"
            + rows + "</table>"
        )

    ts = results.get("timestamp", datetime.now().isoformat())
    html_obj = _HTML.format(
        css=_CSS,
        timestamp=html.escape(ts),
        host=html.escape(results.get("host", "")),
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
        f.write(html_obj)
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
