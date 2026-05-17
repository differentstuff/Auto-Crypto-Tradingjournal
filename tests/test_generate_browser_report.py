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
