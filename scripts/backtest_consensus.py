#!/usr/bin/env python3
"""
scripts/backtest_consensus.py — Accuracy validation for the multi-agent consensus.

Tests three hypotheses:
  H1: Claude-only accuracy (score ≥ 6 → should be profitable)
  H2: Consensus accuracy (both models agree ≥ 6 → higher conviction → better WR)
  H3: Divergence signal (|claude - gemini| > 2 → lower win rate → avoid these)

Accuracy = (profitable_outcomes / total_outcomes_with_result) × 100
Target: consensus accuracy ≥ 85%

Usage:
  python3 scripts/backtest_consensus.py                    # against localhost:8082
  python3 scripts/backtest_consensus.py --host <pi-ip>:8082
  python3 scripts/backtest_consensus.py --live             # re-score with Gemini live
"""

import argparse
import json
import sys
import os
import urllib.request
from pathlib import Path

# Allow importing project modules
sys.path.insert(0, str(Path(__file__).parent.parent))

parser = argparse.ArgumentParser(description="Consensus accuracy backtest")
parser.add_argument("--host",  default="localhost:8082")
parser.add_argument("--live",  action="store_true", help="Re-score calls with Gemini live (uses API credits)")
parser.add_argument("--min-score", type=int, default=5, help="Min setup_score to include (default 5)")
args = parser.parse_args()

HOST = args.host


def api_get(path: str) -> dict:
    resp = urllib.request.urlopen(f"http://{HOST}{path}", timeout=15)
    return json.loads(resp.read())


def outcome_is_win(call: dict) -> bool | None:
    """Return True=win, False=loss, None=unknown."""
    outcome = call.get("outcome")
    pnl     = call.get("outcome_pnl")
    if outcome in ("won",):
        return True
    if outcome in ("lost",):
        return False
    if outcome == "manual" and pnl is not None:
        return float(pnl) > 0
    return None


def accuracy_stats(calls: list, label: str):
    wins = losses = unknown = 0
    for c in calls:
        r = outcome_is_win(c)
        if r is True:   wins   += 1
        elif r is False: losses += 1
        else:            unknown += 1
    total = wins + losses
    pct = round(wins / total * 100, 1) if total else 0
    status = "✅" if pct >= 85 else ("⚠ " if pct >= 70 else "❌")
    print(f"  {status} {label:45} {wins}/{total} = {pct}% WR  ({unknown} unknown outcome)")
    return pct, total


print(f"\nConsensus Accuracy Backtest")
print(f"Host: {HOST} | Min score: {args.min_score}\n")

# ── Fetch data ─────────────────────────────────────────────────────────────────
try:
    calls = api_get("/api/calls/saved").get("data", [])
except Exception as exc:
    print(f"❌ Cannot reach {HOST}: {exc}")
    sys.exit(1)

scored_calls = [c for c in calls if (c.get("setup_score") or 0) >= args.min_score]
with_outcome = [c for c in scored_calls if outcome_is_win(c) is not None]

print(f"Total calls in DB        : {len(calls)}")
print(f"With score ≥ {args.min_score}           : {len(scored_calls)}")
print(f"With known outcome       : {len(with_outcome)}")
print()

if not with_outcome:
    print("⚠  No calls with known outcomes yet — record more call outcomes to enable backtesting.")
    print("   Tip: open the Calls tab, mark outcomes (TP1/TP2/SL/manual) after trades close.\n")
    sys.exit(0)

# ── H1: Claude-only accuracy ───────────────────────────────────────────────────
print("── H1: Claude-only scoring ─────────────────────────────────────────────")
for threshold in (5, 6, 7, 8):
    subset = [c for c in with_outcome if (c.get("setup_score") or 0) >= threshold]
    accuracy_stats(subset, f"Claude score ≥ {threshold}")

# ── H2: Consensus accuracy (calls that already have gemini_score) ──────────────
print("\n── H2: Consensus scoring (already stored) ─────────────────────────────")
con_calls = [c for c in with_outcome if c.get("gemini_score") and c.get("consensus_score")]
if con_calls:
    for threshold in (5, 6, 7):
        subset = [c for c in con_calls if (c.get("consensus_score") or 0) >= threshold]
        accuracy_stats(subset, f"Consensus score ≥ {threshold}")

    divergent = [c for c in con_calls
                 if abs((c.get("setup_score") or 0) - (c.get("gemini_score") or 0)) > 2]
    aligned   = [c for c in con_calls
                 if abs((c.get("setup_score") or 0) - (c.get("gemini_score") or 0)) <= 1]
    print()
    accuracy_stats(aligned,   "Aligned calls (|Δ| ≤ 1) — high confidence")
    accuracy_stats(divergent, "Divergent calls (|Δ| > 2) — REVIEW signals")
else:
    print("  No consensus data yet — consensus columns are populated when calls are saved")
    print("  after the Gemini integration is live. Re-run after recording new call outcomes.")

# ── H3: Live rescoring (optional, uses API credits) ────────────────────────────
if args.live:
    print("\n── H3: Live Gemini rescoring ────────────────────────────────────────────")
    try:
        import gemini_client
        if not gemini_client.is_configured():
            print("  ⚠  GEMINI_API_KEY not set — skipping live rescoring")
        else:
            import agent_orchestrator
            rescored = []
            for c in with_outcome[:20]:   # cap at 20 to control cost
                sym  = c.get("symbol", "")
                dir_ = c.get("direction", "Long")
                text = ""
                # Try to extract call_text from analysis_json
                try:
                    aj = json.loads(c.get("analysis_json") or "{}")
                    text = aj.get("_call_text", "")
                except Exception:
                    pass
                if not text or not sym:
                    continue
                gem = gemini_client.score_call(text, sym, dir_)
                if gem:
                    con = agent_orchestrator.compute_consensus(
                        c.get("setup_score", 0), gem["score"]
                    )
                    rescored.append({**c, "_live_gemini": gem, "_live_consensus": con})

            print(f"  Rescored {len(rescored)} calls live")
            if rescored:
                for threshold in (6, 7):
                    subset = [r for r in rescored
                              if (r["_live_consensus"]["consensus_score"]) >= threshold]
                    accuracy_stats(subset, f"Live consensus ≥ {threshold}")
    except ImportError as e:
        print(f"  ⚠  Could not import gemini_client: {e}")

# ── Summary ────────────────────────────────────────────────────────────────────
print("\n── Summary ──────────────────────────────────────────────────────────────")
total_with_outcome = len(with_outcome)
if total_with_outcome < 20:
    needed = 20 - total_with_outcome
    print(f"⚠  Only {total_with_outcome} calls with outcomes — need ~{needed} more for reliable 85% target.")
    print("   As you record more outcomes the consensus accuracy will become measurable.")
else:
    print(f"✅ {total_with_outcome} outcomes available — sufficient for statistical confidence.")

print(f"\n   To accumulate consensus data: use Call Analyzer (Gemini runs in parallel),")
print(f"   save the call, then mark the outcome once the trade closes.")
print()
