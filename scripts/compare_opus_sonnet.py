#!/usr/bin/env python3
"""
compare_opus_sonnet.py — Re-score the latest scanner setups with Opus and compare to Sonnet.

Usage (run on Pi from project root):
    python3 scripts/compare_opus_sonnet.py

Output:
    docs/opus_sonnet_comparison.md   — full comparison table + reasoning diff
"""

import json
import sys
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from constants import MODEL
OPUS_MODEL = "claude-opus-4-7"

from database import db_conn
import agent_data_collector
import agent_data_interpreter
import agent_market_sentiment
import agent_data_reviewer
from agent_orchestrator import run_scanner_prep


def get_latest_scan_setups(conn, limit: int = 15) -> list[dict]:
    """Fetch the most recent scanner setups from analyzed_calls."""
    cur = conn.execute("""
        SELECT symbol, direction, setup_score, analysis_json, created_at
        FROM analyzed_calls
        WHERE analyst = 'scanner'
          AND analysis_json IS NOT NULL
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    results = []
    for r in rows:
        try:
            aj = json.loads(r["analysis_json"])
        except Exception:
            continue
        results.append({
            "symbol":      r["symbol"],
            "direction":   r["direction"],
            "sonnet_score": r["setup_score"] or aj.get("setup_score", 0),
            "sonnet_cot":  aj.get("cot_reasoning", ""),
            "sonnet_conditions": aj.get("key_conditions", []),
            "sonnet_entry": aj.get("entry_price", 0),
            "sonnet_sl":    aj.get("sl_price", 0),
            "sonnet_tp1":   aj.get("tp1", 0),
            "sonnet_tp2":   aj.get("tp2", 0),
            "sonnet_rr":    aj.get("rr_ratio", 0),
            "created_at":  r["created_at"],
        })
    return results


def rescore_with_opus(setup: dict, conn) -> dict:
    """Re-run the full data pipeline + trade prep with Opus for one setup."""
    symbol    = setup["symbol"]
    direction = setup["direction"]
    print(f"  Opus scoring: {symbol} {direction}...", flush=True)
    try:
        collected   = agent_data_collector.run({
            "symbol":     symbol,
            "direction":  direction,
            "timeframes": ["4H", "1D"],
        })
        interpreted = agent_data_interpreter.run({"collected": collected})
        sentiment   = agent_market_sentiment.run({
            "collected": collected, "interpreted": interpreted,
        })
        reviewed    = agent_data_reviewer.run({
            "collected": collected, "interpreted": interpreted,
            "sentiment": sentiment,
        }, conn)
        result = run_scanner_prep(
            symbol=symbol, direction=direction,
            collected=collected, interpreted=interpreted,
            reviewed=reviewed, sentiment=sentiment,
            conn=conn, model=OPUS_MODEL,
        )
        return {
            "opus_score":      result.get("setup_score", 0),
            "opus_cot":        result.get("cot_reasoning", ""),
            "opus_conditions": result.get("key_conditions", []),
            "opus_entry":      result.get("entry_price", 0),
            "opus_sl":         result.get("sl_price", 0),
            "opus_tp1":        result.get("tp1", 0),
            "opus_tp2":        result.get("tp2", 0),
            "opus_rr":         result.get("rr_ratio", 0),
            "error":           None,
        }
    except Exception as e:
        return {"opus_score": 0, "opus_cot": "", "opus_conditions": [], "error": str(e)}


def _score_label(s: float) -> str:
    if s >= 9:  return "⭐ Excellent"
    if s >= 7:  return "✅ Good"
    if s >= 5:  return "🟡 Monitor"
    return "❌ Avoid"


def _delta_str(opus: float, sonnet: float) -> str:
    d = opus - sonnet
    if d > 1:   return f"▲ +{d:.0f} Opus higher"
    if d < -1:  return f"▼ {d:.0f} Sonnet higher"
    return "≈ Agree"


def generate_report(setups: list[dict]) -> str:
    lines = []
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        f"# Opus vs Sonnet Scanner Comparison",
        f"",
        f"**Generated:** {ts}  ",
        f"**Sonnet model:** `{MODEL}`  ",
        f"**Opus model:** `{OPUS_MODEL}`  ",
        f"**Setups compared:** {len(setups)}",
        f"",
    ]

    # Summary table
    lines += ["## Score Summary", ""]
    lines += ["| Symbol | Dir | Sonnet | Opus | Delta | Verdict |"]
    lines += ["|--------|-----|--------|------|-------|---------|"]
    for s in setups:
        ss = s.get("sonnet_score", 0)
        os_ = s.get("opus_score", 0)
        err = s.get("error")
        opus_cell  = f"**{os_}** {_score_label(os_)}" if not err else f"❌ Error"
        delta_cell = _delta_str(os_, ss) if not err else "—"
        lines.append(
            f"| {s['symbol']} | {s['direction']} | **{ss}** {_score_label(ss)} "
            f"| {opus_cell} | {delta_cell} | "
            f"{'⚠ Diverge' if abs(os_ - ss) > 2 else 'OK'} |"
        )
    lines += [""]

    # Agreements / disagreements summary
    agreements   = [s for s in setups if not s.get("error") and abs(s.get("opus_score",0) - s.get("sonnet_score",0)) <= 1]
    divergences  = [s for s in setups if not s.get("error") and abs(s.get("opus_score",0) - s.get("sonnet_score",0)) > 2]
    opus_higher  = [s for s in setups if not s.get("error") and s.get("opus_score",0) > s.get("sonnet_score",0) + 1]
    opus_lower   = [s for s in setups if not s.get("error") and s.get("opus_score",0) < s.get("sonnet_score",0) - 1]

    lines += [
        "## Agreement Analysis",
        "",
        f"- **Agree (Δ ≤ 1):** {len(agreements)}/{len(setups)} setups",
        f"- **Strongly diverge (Δ > 2):** {len(divergences)} setups",
        f"- **Opus scores higher:** {len(opus_higher)} setups",
        f"- **Opus scores lower (more conservative):** {len(opus_lower)} setups",
        "",
    ]

    # Per-setup detail
    lines += ["## Per-Setup Detail", ""]
    for s in setups:
        ss   = s.get("sonnet_score", 0)
        os_  = s.get("opus_score", 0)
        err  = s.get("error")
        lines += [
            f"---",
            f"### {s['symbol']} — {s['direction']}  *(Sonnet: {ss} | Opus: {os_ if not err else 'ERR'})*",
            "",
        ]
        if err:
            lines += [f"**Opus error:** `{err}`", ""]
            continue

        # Price levels comparison
        lines += [
            "**Levels comparison:**",
            "",
            f"| | Sonnet | Opus |",
            f"|---|--------|------|",
            f"| Entry | {s.get('sonnet_entry',0):.6g} | {s.get('opus_entry',0):.6g} |",
            f"| SL | {s.get('sonnet_sl',0):.6g} | {s.get('opus_sl',0):.6g} |",
            f"| TP1 | {s.get('sonnet_tp1',0):.6g} | {s.get('opus_tp1',0):.6g} |",
            f"| TP2 | {s.get('sonnet_tp2',0):.6g} | {s.get('opus_tp2',0):.6g} |",
            f"| R:R | {s.get('sonnet_rr',0):.2f} | {s.get('opus_rr',0):.2f} |",
            "",
        ]

        # Key conditions comparison
        s_conds = s.get("sonnet_conditions", [])
        o_conds = s.get("opus_conditions", [])
        s_set   = set(c.lower()[:60] for c in s_conds)
        o_set   = set(c.lower()[:60] for c in o_conds)
        only_opus   = [c for c in o_conds if c.lower()[:60] not in s_set]
        only_sonnet = [c for c in s_conds if c.lower()[:60] not in o_set]

        lines += ["**Key conditions:**", ""]
        for c in s_conds:
            marker = "✓" if c.lower()[:60] in o_set else "◻"
            lines.append(f"- Sonnet {marker} {c}")
        for c in only_opus:
            lines.append(f"- Opus only ★ {c}")
        lines += [""]

        # Reasoning
        lines += [
            "**Sonnet reasoning:**",
            f"> {s.get('sonnet_cot','—')}",
            "",
            "**Opus reasoning:**",
            f"> {s.get('opus_cot','—')}",
            "",
        ]

    return "\n".join(lines)


def main():
    print(f"Opus vs Sonnet comparison — {datetime.utcnow().strftime('%H:%M UTC')}")
    print(f"Loading latest scanner setups from DB…")

    with db_conn() as conn:
        setups = get_latest_scan_setups(conn, limit=15)

    if not setups:
        print("No scanner setups found. Run a scan first.")
        sys.exit(1)

    print(f"Found {len(setups)} setups to re-score with Opus.")
    print(f"This will make {len(setups)} Opus API calls — estimated cost: ~${len(setups) * 0.05:.2f}\n")

    # Re-score with Opus (parallel, max 3 at once to avoid rate limits)
    with db_conn() as conn:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {ex.submit(rescore_with_opus, s, conn): s for s in setups}
            for fut in as_completed(futures):
                s = futures[fut]
                try:
                    opus_result = fut.result()
                    s.update(opus_result)
                except Exception as e:
                    s["error"] = str(e)
                    s["opus_score"] = 0

    report = generate_report(setups)

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "opus_sonnet_comparison.md")
    with open(out_path, "w") as f:
        f.write(report)

    print(f"\nReport saved: {out_path}")
    print("\n--- SCORE SUMMARY ---")
    for s in setups:
        ss  = s.get("sonnet_score", 0)
        os_ = s.get("opus_score", 0)
        err = "ERR" if s.get("error") else ""
        delta = f"Δ{os_-ss:+.0f}" if not s.get("error") else ""
        print(f"  {s['symbol']:14s} {s['direction']:5s}  Sonnet={ss}  Opus={os_ if not s.get('error') else err}  {delta}")


if __name__ == "__main__":
    main()
