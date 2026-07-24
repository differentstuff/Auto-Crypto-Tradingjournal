#!/usr/bin/env python3
"""
run_all.py — Run all 8 live validation scripts in sequence.

Phase 1 (read-only):  test_connection
Phase 2 (write):      test_open_position, test_modify_tpsl, test_partial_tp1,
                      test_trailing_stop, test_full_close, test_cancel_orders
Phase 3 (reconcile):  test_reconcile

Exit code: 0 if ALL pass, 1 if ANY fail.

Usage:
    python scripts/live_validation/run_all.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

SCRIPTS = [
    # Phase 1: Read-only
    ("test_connection.py", "Connection & Balance"),
    # Phase 2: Write actions
    ("test_open_position.py", "Open Position"),
    ("test_modify_tpsl.py", "Modify TP/SL"),
    ("test_partial_tp1.py", "Partial TP1"),
    ("test_trailing_stop.py", "Trailing Stop"),
    ("test_full_close.py", "Full Close"),
    ("test_cancel_orders.py", "Cancel Orders"),
    # Phase 3: Reconciliation
    ("test_reconcile.py", "Reconciliation"),
]


def main() -> None:
    print(f"\n{'═' * 60}")
    print(f"  AUTO-TRADER LIVE VALIDATION — ALL 8 TESTS")
    print(f"  Symbol: DOGEUSDT | Leverage: 1x | Budget: ~$35 USDT")
    print(f"  Estimated total fees: ~$0.12 (worst case ~$3)")
    print(f"{'═' * 60}")

    results: list[tuple[str, str, bool, str]] = []
    start_time = time.time()

    for i, (script, description) in enumerate(SCRIPTS, 1):
        script_path = os.path.join(SCRIPT_DIR, script)

        print(f"\n{'─' * 60}")
        print(f"  [{i}/8] Running: {description} ({script})")
        print(f"{'─' * 60}")

        start = time.time()
        try:
            proc = subprocess.run(
                [sys.executable, script_path],
                cwd=PROJECT_ROOT,
                timeout=120,
                capture_output=False,  # Let output flow to terminal
            )
            passed = proc.returncode == 0
            detail = ""
        except subprocess.TimeoutExpired:
            passed = False
            detail = "TIMEOUT after 120s"
        except FileNotFoundError:
            passed = False
            detail = f"Script not found: {script_path}"
        except Exception as e:
            passed = False
            detail = str(e)[:80]

        elapsed = time.time() - start
        results.append((script, description, passed, detail))

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed_total = time.time() - start_time

    print(f"\n{'═' * 60}")
    print(f"  VALIDATION SUMMARY")
    print(f"{'═' * 60}")

    all_passed = True
    for script, description, passed, detail in results:
        icon = "✅" if passed else "❌"
        status = "PASS" if passed else "FAIL"
        line = f"  {icon} {description:25s} [{status}]"
        if detail:
            line += f"  — {detail}"
        print(line)
        if not passed:
            all_passed = False

    total = len(results)
    passed_count = sum(1 for _, _, p, _ in results if p)
    print(f"\n  Result: {passed_count}/{total} passed  ({elapsed_total:.0f}s total)")

    if all_passed:
        print(f"\n  🎉 ALL 8 VALIDATIONS PASSED!")
        print(f"  → Verify in Bitget GUI that positions/orders appeared and disappeared")
        print(f"  → You are cleared to run strategy_15 at 1x leverage 24/7")
    else:
        print(f"\n  ⚠️  Some validations failed — review errors above before going live.")
        failed_names = [desc for _, desc, p, _ in results if not p]
        print(f"  Failed: {', '.join(failed_names)}")

    print()
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
