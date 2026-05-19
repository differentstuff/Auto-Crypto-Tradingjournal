#!/usr/bin/env python3
"""
compare_cascades.py — Score the latest scanner finalists through 8 different
AI providers + an Opus 4.7 baseline, then produce a side-by-side report.

Goal: determine the ranking of providers (by output quality vs Opus) so the
production cascade order is empirically grounded rather than guessed.

Usage (run on Pi from project root):
    python3 scripts/compare_cascades.py

Output:
    docs/cascade_comparison.md   — agreement table + per-setup diff + verdict
"""
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# Load .env so direct execution (outside systemd) gets all API keys
_env_file = os.path.join(_ROOT, ".env")
if os.path.exists(_env_file):
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from constants import MODEL
from database import db_conn
import agent_data_collector
import agent_data_interpreter
import agent_market_sentiment
import agent_data_reviewer
from agent_orchestrator import run_scanner_prep
from ai_client import force_provider

# ── Run definitions ───────────────────────────────────────────────────────────
# Each entry: (run_label, provider, model). provider="anthropic" + model="claude-opus-4-7"
# is the baseline. Everything else is a force-routed single-provider test.
RUNS = [
    ("Baseline (Opus 4.7)",        "anthropic",   "claude-opus-4-7"),
    ("Grok 3 (X.AI)",              "grok",        "grok-3"),
    ("Grok 3 Mini (X.AI)",         "grok",        "grok-3-mini"),
    ("Qwen 3 235B (Cerebras)",     "cerebras",    "qwen-3-235b-a22b-instruct-2507"),
    ("Llama 3.1 8B (Cerebras)",    "cerebras",    "llama3.1-8b"),
    ("Llama 3.3 70B (Groq)",       "groq",        "llama-3.3-70b-versatile"),
    ("Llama 4 Scout (Groq)",       "groq",        "meta-llama/llama-4-scout-17b-16e-instruct"),
    ("DeepSeek V4 (OR)",           "openrouter",  "deepseek/deepseek-v4-flash:free"),
    ("Nemotron 120B (OR)",         "openrouter",  "nvidia/nemotron-3-super-120b-a12b:free"),
]
N_SETUPS = 12     # how many scanner finalists to re-score
MAX_PARALLEL = 2  # provider concurrency — keep low to respect free-tier RPM


def get_latest_scan_setups(conn, limit: int = N_SETUPS) -> list[dict]:
    """Fetch the most recent scanner setups from analyzed_calls."""
    rows = conn.execute("""
        SELECT symbol, direction, setup_score, analysis_json, created_at
        FROM analyzed_calls
        WHERE analyst = 'scanner' AND analysis_json IS NOT NULL
        ORDER BY created_at DESC LIMIT ?
    """, (limit,)).fetchall()
    out = []
    for r in rows:
        try:
            aj = json.loads(r["analysis_json"])
        except Exception:
            continue
        out.append({
            "symbol":     r["symbol"],
            "direction":  r["direction"],
            "scan_score": r["setup_score"] or aj.get("setup_score", 0),
        })
    return out


def collect_once(setup: dict) -> dict:
    """Run agent_data_collector once per setup — external market data only,
    no AI calls. The result is reused across all provider runs so every
    provider scores on identical inputs."""
    return agent_data_collector.run({
        "symbol":     setup["symbol"],
        "direction":  setup["direction"],
        "timeframes": ["4H", "1D"],
    })


def run_pipeline_for_setup(setup: dict, collected: dict, provider: str, model: str) -> dict:
    """Execute the AI pipeline for one setup under a forced provider,
    reusing the pre-fetched `collected` market data for fairness."""
    symbol    = setup["symbol"]
    direction = setup["direction"]
    started   = time.time()
    try:
        with force_provider(provider, model), db_conn() as conn:
            interpreted = agent_data_interpreter.run({"collected": collected})
            sentiment   = agent_market_sentiment.run({
                "collected": collected, "interpreted": interpreted,
                "symbol": symbol, "direction": direction,
            })
            reviewed    = agent_data_reviewer.run({
                "interpreted": interpreted, "symbol": symbol,
                "direction": direction, "setup_type": "scanner",
            }, conn)
            result = run_scanner_prep(
                symbol=symbol, direction=direction,
                collected=collected, interpreted=interpreted,
                reviewed=reviewed, sentiment=sentiment,
                conn=conn, model=model,
            )
        elapsed = time.time() - started
        return {
            "score":      int(result.get("setup_score", 0) or 0),
            "entry":      float(result.get("entry_price", 0) or 0),
            "sl":         float(result.get("sl_price", 0) or 0),
            "tp1":        float(result.get("tp1_price", 0) or 0),
            "tp2":        float(result.get("tp2_price", 0) or 0),
            "rr":         float(result.get("rr_ratio", 0) or 0),
            "conditions": (result.get("key_conditions") or [])[:5],
            "reasoning":  (result.get("cot_reasoning") or "")[:600],
            "elapsed_s":  round(elapsed, 1),
            "error":      None,
        }
    except Exception as e:
        return {"score": 0, "entry": 0, "sl": 0, "tp1": 0, "tp2": 0, "rr": 0,
                "conditions": [], "reasoning": "", "elapsed_s": round(time.time()-started,1),
                "error": str(e)[:200]}


# ── Structural soundness checks (criterion b) ─────────────────────────────────

def structural_soundness(result: dict, direction: str) -> tuple[int, list[str]]:
    """Return (issues_count, list_of_issue_descriptions). 0 = perfectly sound."""
    issues = []
    e, sl, t1, t2 = result["entry"], result["sl"], result["tp1"], result["tp2"]
    if not (e and sl and t1 and t2):
        issues.append("missing-levels")
        return len(issues), issues
    is_long = direction.lower().startswith("l")
    if is_long:
        if sl >= e: issues.append("SL≥entry")
        if t1 <= e: issues.append("TP1≤entry")
        if t2 <= t1: issues.append("TP2≤TP1")
    else:
        if sl <= e: issues.append("SL≤entry")
        if t1 >= e: issues.append("TP1≥entry")
        if t2 >= t1: issues.append("TP2≥TP1")
    if result["rr"] and result["rr"] < 1.5:
        issues.append(f"R:R<1.5 ({result['rr']:.2f})")
    return len(issues), issues


# ── Report builder ────────────────────────────────────────────────────────────

def generate_report(setups: list[dict], all_results: dict) -> str:
    """all_results: {run_label: {setup_idx: result_dict}}"""
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    baseline_label = RUNS[0][0]
    lines += [
        "# Cascade Comparison — 8 Providers vs Opus 4.7",
        "",
        f"**Generated:** {ts}  ",
        f"**Baseline:** `{RUNS[0][2]}`  ",
        f"**Setups scored:** {len(setups)}  ",
        f"**Runs:** {len(RUNS)} ({len(RUNS)-1} provider + 1 baseline)",
        "",
        "## Summary — score agreement with baseline",
        "",
        "Agreement = |Δ| ≤ 1 from Opus baseline score. Higher = closer to Opus.",
        "",
        "| Run | Provider | Avg \\|Δ\\| | Agree (Δ≤1) | Strong diverge (Δ>3) | Sound trades | Errors | Avg latency |",
        "|---|---|---|---|---|---|---|---|",
    ]

    summary_rows = []
    for label, prov, model in RUNS:
        if label == baseline_label:
            continue
        deltas, sound, errors, latencies = [], 0, 0, []
        for i, _setup in enumerate(setups):
            r  = all_results[label][i]
            br = all_results[baseline_label][i]
            if r["error"]:
                errors += 1; continue
            if br["error"]:
                continue
            d = abs((r["score"] or 0) - (br["score"] or 0))
            deltas.append(d)
            if structural_soundness(r, _setup["direction"])[0] == 0:
                sound += 1
            latencies.append(r["elapsed_s"])
        n = len(deltas)
        if n == 0:
            avg_d = "—"; agree = "—"; diverge = "—"
        else:
            avg_d = f"{sum(deltas)/n:.2f}"
            agree = f"{sum(1 for d in deltas if d <= 1)}/{n}"
            diverge = sum(1 for d in deltas if d > 3)
        avg_lat = f"{sum(latencies)/len(latencies):.1f}s" if latencies else "—"
        summary_rows.append((label, prov, avg_d, agree, diverge, sound, errors, avg_lat))
        lines.append(
            f"| {label} | {prov} | {avg_d} | {agree} | {diverge} | "
            f"{sound}/{n} | {errors}/{len(setups)} | {avg_lat} |"
        )

    # Verdict: sort runs by (avg_d asc, sound desc, errors asc) — lowest delta = best
    lines += ["", "## Ranking (closest to Opus baseline)", ""]
    ranked = sorted(
        summary_rows,
        key=lambda r: (
            float(r[2]) if r[2] != "—" else 99,    # avg |Δ| (lower = better)
            -r[5],                                  # sound count (higher = better)
            r[6],                                   # errors (lower = better)
        ),
    )
    for i, (label, prov, avg_d, agree, diverge, sound, errors, lat) in enumerate(ranked, 1):
        lines.append(f"{i}. **{label}** ({prov}) — avg Δ {avg_d}, sound {sound}, errors {errors}, {lat}")

    lines += ["", "## Per-setup detail", ""]

    for i, setup in enumerate(setups):
        sym, dir_ = setup["symbol"], setup["direction"]
        b = all_results[baseline_label][i]
        lines += [f"---", f"### {sym} — {dir_}", ""]
        if b["error"]:
            lines += [f"⚠ Baseline errored: `{b['error']}` — skipping setup", ""]
            continue
        lines.append(f"**Baseline (Opus):** score {b['score']} · entry {b['entry']:.6g} · "
                     f"SL {b['sl']:.6g} · TP1 {b['tp1']:.6g} · TP2 {b['tp2']:.6g} · R:R {b['rr']:.2f}")
        lines += ["", "| Run | Score | Δ | Entry | SL | TP1 | TP2 | R:R | Sound | Latency |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for label, _prov, _model in RUNS:
            r = all_results[label][i]
            if r["error"]:
                lines.append(f"| {label} | — | — | — | — | — | — | — | — | ERROR |")
                continue
            d = (r["score"] or 0) - (b["score"] or 0)
            d_str = f"{d:+d}" if label != baseline_label else "—"
            n_issues, _ = structural_soundness(r, dir_)
            sound_str = "✓" if n_issues == 0 else f"⚠×{n_issues}"
            lines.append(f"| {label} | {r['score']} | {d_str} | {r['entry']:.6g} | "
                         f"{r['sl']:.6g} | {r['tp1']:.6g} | {r['tp2']:.6g} | "
                         f"{r['rr']:.2f} | {sound_str} | {r['elapsed_s']}s |")
        lines += ["", "**Baseline reasoning (Opus):**", f"> {b['reasoning']}", ""]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"Cascade comparison — {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"  Loading {N_SETUPS} latest scanner setups from DB…")
    with db_conn() as conn:
        setups = get_latest_scan_setups(conn, limit=N_SETUPS)
    if not setups:
        print("  ❌ No scanner setups found. Run a scan first.")
        sys.exit(1)
    print(f"  Found {len(setups)} setups.")
    print(f"  Will execute {len(RUNS)} runs × {len(setups)} setups = {len(RUNS)*len(setups)} pipelines.")
    print()

    # Phase 1: collect market data once per setup (no AI, but external API hits)
    print("━━ Phase 1: collecting market data (once per setup) ━━")
    setup_data: dict[int, dict] = {}
    for i, s in enumerate(setups):
        try:
            setup_data[i] = collect_once(s)
            print(f"    ✓ {s['symbol']:14s} data collected")
        except Exception as e:
            print(f"    ✗ {s['symbol']:14s} collect failed: {e}")
            setup_data[i] = None
    print()

    # Phase 2: score each (setup, provider) combo using the cached collected data
    all_results: dict[str, dict[int, dict]] = {label: {} for label, _, _ in RUNS}

    for label, prov, model in RUNS:
        print(f"━━ {label} ({prov} / {model}) ━━")
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
            futures = {}
            for i, s in enumerate(setups):
                if setup_data[i] is None:
                    all_results[label][i] = {"score": 0, "entry": 0, "sl": 0, "tp1": 0,
                                              "tp2": 0, "rr": 0, "conditions": [],
                                              "reasoning": "", "elapsed_s": 0,
                                              "error": "no market data"}
                    continue
                futures[ex.submit(run_pipeline_for_setup, s, setup_data[i], prov, model)] = i
            for fut in as_completed(futures):
                i = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"score": 0, "entry": 0, "sl": 0, "tp1": 0, "tp2": 0, "rr": 0,
                           "conditions": [], "reasoning": "", "elapsed_s": 0,
                           "error": str(e)[:200]}
                all_results[label][i] = res
                err_mark = "✗" if res["error"] else "✓"
                err_suffix = ' err=' + res['error'][:50] if res['error'] else ''
                print(f"    {err_mark} {setups[i]['symbol']:14s} score={res['score']:>2} "
                      f"{res['elapsed_s']}s{err_suffix}")
        print()

    # Save report
    out_path = os.path.join(_ROOT, "docs", "cascade_comparison.md")
    report = generate_report(setups, all_results)
    with open(out_path, "w") as f:
        f.write(report)
    print(f"✓ Report saved: {out_path}")

    # Print top-3 ranking
    print()
    print("─── TOP 3 (closest to Opus baseline) ───")
    baseline_label = RUNS[0][0]
    ranked = []
    for label, prov, _ in RUNS:
        if label == baseline_label: continue
        deltas = []
        for i in range(len(setups)):
            r  = all_results[label][i]
            br = all_results[baseline_label][i]
            if r["error"] or br["error"]: continue
            deltas.append(abs(r["score"] - br["score"]))
        if deltas:
            ranked.append((sum(deltas)/len(deltas), label, prov))
    for i, (avg_d, label, prov) in enumerate(sorted(ranked)[:3], 1):
        print(f"  {i}. {label} ({prov}) — avg |Δ| {avg_d:.2f}")


if __name__ == "__main__":
    main()
