#!/usr/bin/env python3
"""
scripts/self_test.py — End-to-end smoke test of all 76 API endpoints.

Runs against a live server and checks that every endpoint:
  - Returns HTTP 200 (or expected code)
  - Returns valid JSON with an "ok" key
  - (for HTML routes) Returns a 200 with HTML content

Modes:
  Default       — reads only; safe against a production database
  --write       — also tests POST/PATCH/DELETE by creating and cleaning up test fixtures
  --ai          — also tests AI-calling endpoints (uses Anthropic API credits); implies --write

Usage:
  python3 scripts/self_test.py                          # against localhost:8082
  python3 scripts/self_test.py --host 192.168.1.21:8082 # against Pi
  python3 scripts/self_test.py --write                  # include write tests
  python3 scripts/self_test.py --ai                     # include AI tests

Exit code: 0 if all run tests pass, 1 if any fail.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

# ── CLI args ───────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="Trading Journal API self-test")
parser.add_argument("--host",    default="localhost:8082", help="host:port (default: localhost:8082)")
parser.add_argument("--write",   action="store_true",      help="run write/mutation tests")
parser.add_argument("--ai",      action="store_true",      help="run AI-calling tests (implies --write, costs API credits)")
parser.add_argument("--timeout", type=int, default=20,     help="per-request timeout in seconds")
args = parser.parse_args()

if args.ai:
    args.write = True

HOST    = args.host
TIMEOUT = args.timeout

# ── Result tracking ────────────────────────────────────────────────────────────

_results = []  # (name, status, detail)


def _record(name: str, status: str, detail: str = ""):
    _results.append((name, status, detail))
    icons = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭ "}
    icon  = icons.get(status, "❓")
    suffix = f"  — {detail}" if detail else ""
    print(f"  {icon} {name}{suffix}")


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _raw(method: str, path: str, body=None, params: dict = None) -> tuple[int, str]:
    """Return (status_code, response_body). Raises on network/timeout error."""
    url = f"http://{HOST}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")


def _check_json(body: str) -> tuple[bool, dict | None]:
    """Return (is_valid, parsed). parsed is None on parse failure."""
    try:
        return True, json.loads(body)
    except json.JSONDecodeError:
        return False, None


def _ok_key(d: dict | None) -> bool:
    return d is not None and "ok" in d


# ── High-level request wrappers ────────────────────────────────────────────────

def GET(path: str, *, name: str = None, params: dict = None,
        expect_html: bool = False, expect_ok_false: bool = False) -> dict | None:
    label = name or f"GET {path}"
    try:
        status, body = _raw("GET", path, params=params)
    except Exception as exc:
        _record(label, "FAIL", str(exc)[:80])
        return None

    if expect_html:
        if status == 200 and ("<html" in body.lower() or "<!doctype" in body.lower()):
            _record(label, "PASS", "HTML ok")
            return {"ok": True, "_html": True}
        _record(label, "FAIL", f"HTTP {status}, expected HTML")
        return None

    valid, d = _check_json(body)
    if not valid:
        _record(label, "FAIL", f"HTTP {status}, non-JSON body")
        return None

    if expect_ok_false:
        # Any status code is fine as long as we get valid JSON with ok=false
        if d and d.get("ok") is False:
            _record(label, "PASS", f"HTTP {status} ok=false (expected)")
            return d
        _record(label, "FAIL", f"HTTP {status}: expected ok=false, got {body[:60]}")
        return None

    if status != 200:
        detail = d.get("error", body[:60]) if d else body[:60]
        _record(label, "FAIL", f"HTTP {status}: {detail}")
        return None
    if not _ok_key(d):
        _record(label, "FAIL", "no 'ok' key in response")
        return None
    _record(label, "PASS")
    return d


def POST(path: str, body=None, *, name: str = None,
         expect_status: int = None) -> dict | None:
    label  = name or f"POST {path}"
    expect = expect_status or 200
    try:
        status, resp = _raw("POST", path, body or {})
    except Exception as exc:
        _record(label, "FAIL", str(exc)[:80])
        return None

    valid, d = _check_json(resp)
    if not valid:
        _record(label, "FAIL", f"HTTP {status}, non-JSON body")
        return None
    if status not in (200, 201) and status != expect:
        detail = d.get("error", resp[:60]) if d else resp[:60]
        _record(label, "FAIL", f"HTTP {status}: {detail}")
        return None
    if not _ok_key(d):
        _record(label, "FAIL", "no 'ok' key")
        return None
    _record(label, "PASS")
    return d


def PATCH(path: str, body: dict, *, name: str = None) -> dict | None:
    label = name or f"PATCH {path}"
    try:
        status, resp = _raw("PATCH", path, body)
    except Exception as exc:
        _record(label, "FAIL", str(exc)[:80])
        return None
    valid, d = _check_json(resp)
    if not valid or status != 200 or not _ok_key(d):
        detail = (d.get("error", "") if d else resp[:60])
        _record(label, "FAIL", f"HTTP {status}: {detail}")
        return None
    _record(label, "PASS")
    return d


def PUT(path: str, body: dict, *, name: str = None) -> dict | None:
    label = name or f"PUT {path}"
    try:
        status, resp = _raw("PUT", path, body)
    except Exception as exc:
        _record(label, "FAIL", str(exc)[:80])
        return None
    valid, d = _check_json(resp)
    if not valid or status != 200 or not _ok_key(d):
        detail = (d.get("error", "") if d else resp[:60])
        _record(label, "FAIL", f"HTTP {status}: {detail}")
        return None
    _record(label, "PASS")
    return d


def DELETE(path: str, *, name: str = None) -> dict | None:
    label = name or f"DELETE {path}"
    try:
        status, resp = _raw("DELETE", path)
    except Exception as exc:
        _record(label, "FAIL", str(exc)[:80])
        return None
    valid, d = _check_json(resp)
    if not valid or status != 200 or not _ok_key(d):
        detail = (d.get("error", "") if d else resp[:60])
        _record(label, "FAIL", f"HTTP {status}: {detail}")
        return None
    _record(label, "PASS")
    return d


def SKIP(name: str, reason: str = ""):
    _record(name, "SKIP", reason)


# ── Validation helpers ─────────────────────────────────────────────────────────

def _assert_list(resp: dict | None, name: str) -> list:
    """Check that resp['data'] is a list. Returns the list (may be empty)."""
    if resp is None:
        return []
    data = resp.get("data")
    if not isinstance(data, list):
        _record(f"{name} — data is list", "FAIL", f"got {type(data).__name__}")
        return []
    _record(f"{name} — data is list", "PASS")
    return data


def _assert_key(resp: dict | None, key: str, name: str):
    """Check that resp['data'][key] exists."""
    if resp is None:
        return
    data = resp.get("data", {})
    if not isinstance(data, dict) or key not in data:
        _record(f"{name} — has '{key}'", "FAIL")
    else:
        _record(f"{name} — has '{key}'", "PASS")


# ── Test fixtures ──────────────────────────────────────────────────────────────

# IDs discovered from existing data (Phase 0)
_existing_pos_id  = None
_existing_call_id = None

# IDs created by write tests (Phase 2) — cleaned up at end
_test_pos_id   = None
_test_call_id  = None
_test_limit_id = None


_MINIMAL_CALL = {
    "symbol":        "TESTUSDT",
    "direction":     "Long",
    "trade_type":    "Breakout",
    "has_dca":       False,
    "has_candle_close_sl": False,
    "thinking":      "Self-test fixture — not a real analysis.",
    "setup_quality": {"score": 5, "label": "Moderate"},
    "chart_analysis": "Test fixture.",
    "risk_reward":   {"ratio": "1:2", "entry": 1.0, "sl": 0.9, "tp1": 1.2, "tp2": 1.4},
    "bitget_settings": {"symbol": "TESTUSDT", "direction": "Long",
                        "stop_loss": {"price": "0.9"},
                        "take_profit_1": {"price": "1.2"},
                        "take_profit_2": {"price": "1.4"}},
    "entry_timing":  "Market",
    "optimizations": ["Test 1"],
    "risks":         ["Test risk"],
    "pattern_flags": [],
    "historical_context": "No history.",
    "sl_warning":    "",
    "summary":       "Self-test fixture call.",
    "_call_text":    "LONG TESTUSDT at $1.00, SL $0.90, TP1 $1.20, TP2 $1.40",
    "_sizing": {
        "entry_price": 1.0, "sl_price": 0.9,
        "total_notional_usdt": 100, "margin_needed_usdt": 10,
        "risk_pct": 1.0, "risk_amount_usdt": 10,
        "avg_entry": 1.0, "leverage": 10,
    },
    "_analyst": "self_test",
}

_MINIMAL_POSITION = {
    "symbol":       "TESTUSDT",
    "direction":    "Long",
    "open_time":    "2024-01-01 00:00:00",
    "close_time":   "2024-01-01 01:00:00",
    "entry_price":  1.00,
    "close_price":  1.10,
    "size_usdt":    100,
    "realized_pnl": 10.0,
    "notes":        "self_test fixture",
}

_MINIMAL_LIMIT = {
    "symbol":      "TESTUSDT",
    "direction":   "Long",
    "limit_price": 1.0,
    "size_usdt":   100,
    "sl_price":    0.9,
    "tp1_price":   1.2,
}


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    global _existing_pos_id, _existing_call_id
    global _test_pos_id, _test_call_id, _test_limit_id

    print(f"\nTrading Journal — self_test.py")
    print(f"Host   : {HOST}")
    print(f"Write  : {'yes' if args.write else 'no'}")
    print(f"AI     : {'yes' if args.ai else 'no'}")
    print(f"Timeout: {TIMEOUT}s\n")

    # ── Phase 0: Connectivity check ────────────────────────────────────────────
    print("── Phase 0: Connectivity ──────────────────────────────────────")
    try:
        status, _ = _raw("GET", "/")
        if status != 200:
            print(f"FATAL: server at {HOST} returned HTTP {status} for GET /")
            sys.exit(1)
        print(f"  Server is up at http://{HOST}/\n")
    except Exception as exc:
        print(f"FATAL: cannot reach http://{HOST}/ — {exc}")
        sys.exit(1)

    # ── Phase 1a: Index & static ────────────────────────────────────────────────
    print("── Phase 1a: Index & chart pages ──────────────────────────────")
    GET("/",                  name="GET /  (index page)",  expect_html=True)
    GET("/chart",             name="GET /chart",           expect_html=True)

    # ── Phase 1b: Status / health endpoints ────────────────────────────────────
    print("\n── Phase 1b: Status / health ───────────────────────────────────")
    GET("/api/sync/status",                name="GET /api/sync/status")
    GET("/api/scanner/status",             name="GET /api/scanner/status")
    GET("/api/scanner/criteria-defaults",  name="GET /api/scanner/criteria-defaults")
    GET("/api/scanner/watchlist",          name="GET /api/scanner/watchlist")
    GET("/api/hindsight/status",           name="GET /api/hindsight/status")
    GET("/api/import/status",              name="GET /api/import/status")
    GET("/api/settings/exchanges",         name="GET /api/settings/exchanges")
    GET("/api/settings/blofin/status",     name="GET /api/settings/blofin/status")
    GET("/api/rulebook",                   name="GET /api/rulebook")
    GET("/api/telegram/status",            name="GET /api/telegram/status")

    # ── Phase 1c: Analytics endpoints ──────────────────────────────────────────
    print("\n── Phase 1c: Analytics ─────────────────────────────────────────")
    r = GET("/api/dashboard/kpis", name="GET /api/dashboard/kpis")
    _assert_key(r, "total_pnl", "dashboard/kpis")

    GET("/api/analytics/deep",           name="GET /api/analytics/deep")
    GET("/api/analytics/heatmap",        name="GET /api/analytics/heatmap")
    GET("/api/analytics/rr",             name="GET /api/analytics/rr")
    GET("/api/analytics/mfe-mae",        name="GET /api/analytics/mfe-mae")
    GET("/api/analytics/ev-by-setup",    name="GET /api/analytics/ev-by-setup")
    GET("/api/analytics/rolling",        name="GET /api/analytics/rolling")
    GET("/api/analytics/accuracy-trend", name="GET /api/analytics/accuracy-trend")
    GET("/api/analytics/sharpe-calmar",  name="GET /api/analytics/sharpe-calmar")
    GET("/api/token-usage",              name="GET /api/token-usage")

    # ── Phase 1d: Journal / positions ──────────────────────────────────────────
    print("\n── Phase 1d: Journal / positions ───────────────────────────────")
    r = GET("/api/positions", name="GET /api/positions (all)")
    # /api/positions returns {ok:true, data:{positions:[...], total:N, page:N, ...}}
    if r:
        data = r.get("data", {})
        rows = data.get("positions", []) if isinstance(data, dict) else []
        if isinstance(rows, list):
            _record("positions — data.positions is list", "PASS")
        else:
            _record("positions — data.positions is list", "FAIL",
                    f"got {type(rows).__name__}")
        if rows:
            _existing_pos_id = rows[0]["id"]
            print(f"    Using existing position id={_existing_pos_id}")
    else:
        rows = []

    GET("/api/symbols",       name="GET /api/symbols")
    GET("/api/wallet/history",name="GET /api/wallet/history")
    GET("/api/hindsight/results", name="GET /api/hindsight/results")

    # ── Phase 1e: Call analyzer endpoints ──────────────────────────────────────
    print("\n── Phase 1e: Call analyzer ─────────────────────────────────────")
    r = GET("/api/calls/saved",            name="GET /api/calls/saved")
    saved_calls = _assert_list(r, "calls/saved list")
    if saved_calls:
        _existing_call_id = saved_calls[0]["id"]
        print(f"    Using existing call id={_existing_call_id}")

    GET("/api/calls/check-matches",        name="GET /api/calls/check-matches")
    GET("/api/calls/linkable",             name="GET /api/calls/linkable")
    GET("/api/calls/prediction-accuracy",  name="GET /api/calls/prediction-accuracy")
    GET("/api/calls/analyst-stats",        name="GET /api/calls/analyst-stats")

    # ── Phase 1f: Limits ───────────────────────────────────────────────────────
    print("\n── Phase 1f: Limits ────────────────────────────────────────────")
    r = GET("/api/limits",              name="GET /api/limits")
    _assert_list(r, "limits list")
    GET("/api/limits/risk-summary",     name="GET /api/limits/risk-summary")

    # ── Phase 1g: Live / exchange ──────────────────────────────────────────────
    print("\n── Phase 1g: Live / exchange ───────────────────────────────────")
    GET("/api/live/positions",           name="GET /api/live/positions")
    GET("/api/live/pending-orders",      name="GET /api/live/pending-orders")
    GET("/api/exchange/symbols",         name="GET /api/exchange/symbols")
    GET("/api/market/context",           name="GET /api/market/context")
    GET("/api/market/calendar",          name="GET /api/market/calendar")
    GET("/api/market/prices",            name="GET /api/market/prices",
        params={"symbols": "BTCUSDT,ETHUSDT"})
    GET("/api/nansen/movers",            name="GET /api/nansen/movers")

    # ── Phase 1h: Parameterised GETs ──────────────────────────────────────────
    print("\n── Phase 1h: Parameterised GETs ────────────────────────────────")
    GET("/api/nansen/signal/BTCUSDT",    name="GET /api/nansen/signal/BTCUSDT")
    GET("/api/chart/candles",            name="GET /api/chart/candles?symbol=BTCUSDT",
        params={"symbol": "BTCUSDT", "timeframe": "1D", "limit": "50"})
    GET("/api/chart/indicators",         name="GET /api/chart/indicators?symbol=BTCUSDT",
        params={"symbol": "BTCUSDT", "timeframes": "4H,1D"})

    # ── Phase 1i: Endpoints with IDs (use discovered or 404 test) ─────────────
    print("\n── Phase 1i: ID-parameterised GETs ─────────────────────────────")
    if _existing_pos_id:
        GET(f"/api/positions/{_existing_pos_id}",
            name=f"GET /api/positions/<id> (id={_existing_pos_id})")
    else:
        GET("/api/positions/99999",
            name="GET /api/positions/99999 (expect 404 ok=false)",
            expect_ok_false=True)

    if _existing_call_id:
        GET(f"/api/calls/{_existing_call_id}/postmortem",
            name=f"GET /api/calls/<id>/postmortem (id={_existing_call_id})")
    else:
        SKIP("GET /api/calls/<id>/postmortem", "no saved calls in DB")

    # ── Phase 1j: POST error-path tests (no write) ────────────────────────────
    print("\n── Phase 1j: POST error paths (validation, no DB write) ────────")
    # These POST requests send intentionally bad data to test 400 responses.
    POST("/api/limits",               body={},
         name="POST /api/limits (missing fields → 400)", expect_status=400)
    POST("/api/positions",            body={},
         name="POST /api/positions (missing fields → 400)", expect_status=400)
    POST("/api/settings/credentials", body={"exchange": "bad"},
         name="POST /api/settings/credentials (bad exchange → 400)", expect_status=400)
    POST("/api/limits/bulk-update",   body={"ids": []},
         name="POST /api/limits/bulk-update (empty ids → 400)", expect_status=400)
    POST("/api/calls/analyze",        body={"call_text": ""},
         name="POST /api/calls/analyze (empty text → 400)", expect_status=400)

    # ── Phase 2: Write tests (--write) ─────────────────────────────────────────
    if not args.write:
        print("\n── Phase 2: Write tests ────────────────────────────────────────")
        SKIP("All write tests", "pass --write to enable")
    else:
        print("\n── Phase 2a: Position CRUD ─────────────────────────────────────")

        r = POST("/api/positions", body=_MINIMAL_POSITION,
                 name="POST /api/positions (create test fixture)", expect_status=201)
        if r and r.get("data", {}).get("id"):
            _test_pos_id = r["data"]["id"]
            print(f"    Created test position id={_test_pos_id}")

            GET(f"/api/positions/{_test_pos_id}",
                name=f"GET /api/positions/{_test_pos_id} (just created)")

            PUT(f"/api/positions/{_test_pos_id}",
                body={"notes": "self_test updated", "tags": "test"},
                name=f"PUT /api/positions/{_test_pos_id}")

        print("\n── Phase 2b: Analyzed call CRUD ────────────────────────────────")

        r = POST("/api/calls/save", body=_MINIMAL_CALL,
                 name="POST /api/calls/save (create test fixture)", expect_status=201)
        if r and r.get("data", {}).get("id"):
            _test_call_id = r["data"]["id"]
            print(f"    Created test call id={_test_call_id}")

            PATCH(f"/api/calls/{_test_call_id}",
                  body={"notes": "self_test patched"},
                  name=f"PATCH /api/calls/{_test_call_id}")

            POST(f"/api/calls/{_test_call_id}/record-outcome",
                 body={"outcome": "tp1", "outcome_pnl": 5.0},
                 name=f"POST /api/calls/<id>/record-outcome")

            POST(f"/api/calls/{_test_call_id}/close",
                 name=f"POST /api/calls/<id>/close")

        print("\n── Phase 2c: Limit CRUD ────────────────────────────────────────")

        r = POST("/api/limits", body=_MINIMAL_LIMIT,
                 name="POST /api/limits (create test fixture)", expect_status=201)
        if r and r.get("data", {}).get("id"):
            _test_limit_id = r["data"]["id"]
            print(f"    Created test limit id={_test_limit_id}")

            PATCH(f"/api/limits/{_test_limit_id}",
                  body={"notes": "self_test"},
                  name=f"PATCH /api/limits/{_test_limit_id}")

            POST("/api/limits/bulk-update",
                 body={"ids": [_test_limit_id], "status": "triggered"},
                 name="POST /api/limits/bulk-update")

        print("\n── Phase 2d: Misc writes ────────────────────────────────────────")
        POST("/api/calls/analyze",
             body={"call_text": "Long BTCUSDT at $50000, SL $49000, TP $52000"},
             name="POST /api/calls/analyze (no AI key needed for parse path)"
             ) if not args.ai else None

    # ── Phase 3: AI tests (--ai) ───────────────────────────────────────────────
    if not args.ai:
        print("\n── Phase 3: AI tests ───────────────────────────────────────────")
        SKIP("All AI tests", "pass --ai to enable (uses API credits)")
    else:
        print("\n── Phase 3: AI tests ───────────────────────────────────────────")

        POST("/api/calls/analyze",
             body={"call_text": "Long BTCUSDT at $50000, SL $49000, TP $52000"},
             name="POST /api/calls/analyze (Sonnet)")

        POST("/api/ai/analyze", body={},
             name="POST /api/ai/analyze (portfolio advisor)")

        POST("/api/rulebook/update", body={"force": False},
             name="POST /api/rulebook/update (regen guard)")

        POST("/api/scanner/calibrate", body={},
             name="POST /api/scanner/calibrate")

        if _test_limit_id:
            POST(f"/api/limits/{_test_limit_id}/analyze",
                 name=f"POST /api/limits/<id>/analyze (Haiku)")

        if _test_pos_id:
            POST(f"/api/positions/{_test_pos_id}/grade", body={},
                 name=f"POST /api/positions/<id>/grade (Haiku)")

        POST("/api/live/analyze",
             body={"position_ids": []},
             name="POST /api/live/analyze (live check, no open positions needed)")

        # Scanner run is slow (~30s) — included but warned
        print("    ⚠  POST /api/scanner/run may take 30+ seconds …")
        POST("/api/scanner/run", body={},
             name="POST /api/scanner/run")

    # ── Phase 4: Cleanup ───────────────────────────────────────────────────────
    if args.write:
        print("\n── Phase 4: Cleanup ────────────────────────────────────────────")
        if _test_call_id:
            DELETE(f"/api/calls/{_test_call_id}",
                   name=f"DELETE /api/calls/{_test_call_id} (fixture cleanup)")
        if _test_limit_id:
            DELETE(f"/api/limits/{_test_limit_id}",
                   name=f"DELETE /api/limits/{_test_limit_id} (fixture cleanup)")
        if _test_pos_id:
            DELETE(f"/api/positions/{_test_pos_id}",
                   name=f"DELETE /api/positions/{_test_pos_id} (fixture cleanup)")

    # ── Remaining endpoints that are complex / side-effecting (always skipped) ─
    print("\n── Skipped endpoints ───────────────────────────────────────────")
    SKIP("POST /api/sync",                   "triggers live Bitget sync — run manually")
    SKIP("POST /api/settings/test-connection","tests live exchange creds — run manually")
    SKIP("POST /api/settings/blofin/sync",   "triggers live Blofin sync — run manually")
    SKIP("POST /api/telegram/test",          "sends real Telegram message — run manually")
    SKIP("POST /api/hindsight/run",          "batch AI job — run manually or with --ai")
    SKIP("DELETE /api/hindsight/results",    "destructive — run manually")
    SKIP("POST /api/import",                 "requires CSV upload — run manually")
    SKIP("POST /api/calls/<id>/confirm-match","needs matching position — run manually")
    SKIP("POST /api/calls/<id>/dismiss",     "needs existing call — run manually")
    SKIP("POST /api/settings/credentials",  "would overwrite real .env — run manually")

    # ── Summary ────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    skipped = sum(1 for _, s, _ in _results if s == "SKIP")
    total  = passed + failed
    pct    = round(passed / total * 100) if total else 0

    print(f"Results: {passed}/{total} passed  ({pct}%)  |  {skipped} skipped")

    if failed:
        print(f"\nFailed tests:")
        for name, status, detail in _results:
            if status == "FAIL":
                print(f"  ❌ {name}" + (f"  — {detail}" if detail else ""))

    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
