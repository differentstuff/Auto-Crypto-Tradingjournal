# Trading Signal Improvements — Design Spec
*Date: 2026-05-14 · Status: Approved*

---

## Overview

7 targeted improvements to the journal's scoring, confluence, and prompt systems based on VMC Cipher A+B and SMC/ICT methodology review. All changes are backward-compatible — no DB migrations, no API shape changes.

---

## Architecture Summary

```
prompt_fragments.py     — scoring scale text, new fragments (items 2, 5)
chart_context.py        — new signal weight functions (items 3, 4)
prompt_builder.py       — reversal rubric, zone/LOQ guidance injection (items 3, 7)
ai_scanner.py           — kill zone annotation, 1H finalist timeframe (items 1, 6)
```

No new files. No new dependencies.

---

## Item 1 — Kill Zone Urgency Annotation

**File:** `ai_scanner.py`

**Logic:** After Claude scores each setup, post-process the result dict. Compute current UTC hour. Kill zones:
- London: 07:00–10:00 UTC
- NY AM:  12:00–15:00 UTC

If neither window is active, append `" ⚠ Outside kill zone"` to the existing `urgency` field value.

**Implementation site:** `_batch_ai_score()` — in the result-parsing loop where each setup dict is built (after score threshold check). Also applied in `_ai_score()` single-symbol path.

**No prompt change. No score change.**

**Precondition:** Result dict must have an `urgency` key. If missing, set to `"⚠ Outside kill zone"`.

---

## Item 2 — R:R Threshold Step-Up (Option B)

**File:** `prompt_fragments.py`

**SCORING_SCALE updated to:**
```
5 — Moderate: mixed signals, borderline — not worth entering without improvement
6 — Acceptable: clear bias + valid level, SL structural, R:R ≥ 2:1
7 — Good: multiple aligned signals, structural entry + SL, R:R ≥ 2.5:1
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 3:1
9 — Excellent: near-ideal — all criteria met, multi-TF alignment, R:R ≥ 3.5:1
10 — Perfect: textbook chart pattern, volume confirmation, ideal entry timing, R:R ≥ 4:1
```

**LEVEL_PROXIMITY_RULES cap line updated:**
```
- R:R < 2:1 → score ≤ 6; R:R ≥ 2.5:1 for score 7+; R:R ≥ 3.5:1 for score 9+
```

**Also update in `ai_scanner.py`:** `_build_scanner_stable()` inline summary string:
```python
"5=Mod, 6=Accept(R:R≥2), 7=Good(R:R≥2.5), 8=Strong(R:R≥3), 9=Excellent(R:R≥3.5), 10=Perfect(R:R≥4)"
```

---

## Item 3 — Premium/Discount Zone Penalty

**File:** `prompt_fragments.py` (LEVEL_PROXIMITY_RULES) + `prompt_builder.py`

**Approach:** Prompt instruction only — Claude already receives S/R levels and current price; it can determine zone position without Python computation.

**Two lines added to LEVEL_PROXIMITY_RULES:**
```
- LONG setup in premium zone (price > midpoint of nearest S/R range) → reduce score by 1
- SHORT setup in discount zone (price < midpoint of nearest S/R range) → reduce score by 1
```

Midpoint = `(nearest_resistance + nearest_support) / 2`. If no S/R levels available, skip this rule.

---

## Item 4 — MFI as Standalone Confluence Signal

**File:** `chart_context.py`

**Data source:** MFI is already computed alongside WaveTrend (in `_compute_wavetrend_indicators()`). It is stored in `inds["wavetrend"]["mfi"]` as a float in range −100 to +100. Positive = bullish capital inflow, negative = bearish.

**New function:**
```python
def _mfi_weight(wt: dict) -> float:
    """MFI (Money Flow) contribution: +0.3 bullish, -0.3 bearish, 0 near-zero."""
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    if mfi > 10:   return  0.3
    if mfi < -10:  return -0.3
    return 0.0
```

Dead-band of ±10 avoids noise near zero. Max contribution 0.3 (smaller than CVD 0.4 — MFI is a slower signal).

**Changes to `confluence_score()`:**
1. Add `mfi_w = _mfi_weight(inds.get("wavetrend", {}))` alongside existing weights
2. Add to `base_score`
3. Update `max_val` comment: `5.9 + 0.3 = 6.2` per timeframe

**Changes to `_get_tf_weights()`:**
Add `_mfi_weight(inds.get("wavetrend", {}))` to the `base` list so `bull_total`/`bear_total` reflect MFI.

---

## Item 5 — Draw-on-Liquidity TP Guidance

**File:** `prompt_fragments.py` (new constant) + `prompt_builder.py` + `ai_scanner.py`

**New constant in `prompt_fragments.py`:**
```python
DRAW_ON_LIQUIDITY_RULES = """TAKE-PROFIT TARGETING:
Prefer TP targets that coincide with visible liquidity pools — equal highs/lows,
prior swing highs/lows, previous ATH/ATL, or untested fair value gaps — rather
than arbitrary R:R multiples. Name the specific level and why liquidity rests there.
A TP at a swing high where stop-losses cluster is higher quality than a round-number TP.""".strip()
```

**Injection points:**
- `prompt_builder.py` `build_stable_prefix()`: append `DRAW_ON_LIQUIDITY_RULES` after scoring fragments
- `ai_scanner.py` `_build_scanner_stable()`: append after `SCORING_SCALE`

---

## Item 6 — 1H Timeframe for Finalist Pipeline

**File:** `ai_scanner.py`

**Change:** In `_score_finalists_with_agents()`, update the data collector call:
```python
# Before:
"timeframes": ["4H", "1D"]
# After:
"timeframes": ["1H", "4H", "1D"]
```

**Scope:** Only affects top-N finalists going through the full 7-agent pipeline (max `SCANNER_FULL_DETAIL_TOP_N = 12` symbols). Stage 1 bulk confluence scan remains 4H+1D for performance.

**Why 1H first:** The interpreter processes timeframes in order; having 1H first in the list gives the AI fresher short-term momentum context.

---

## Item 7 — BOS/CHoCH Distinction in Reversal Rubric

**File:** `prompt_builder.py`

**Updated `_RUBRICS["reversal"]`:**
```python
"reversal": (
    "REVERSAL RUBRIC: 9-10 = extreme RSI divergence at major S/R, multi-TF confirmation, "
    "clear candle rejection pattern, R:R ≥ 3.5:1. 7-8 = strong level + indicator signal. "
    "6 = moderate confluence only. Penalise reversals against the weekly trend unless very strong. "
    "CRITICAL: Require CHoCH (Change of Character) confirmation before entry — a BOS (Break of Structure) "
    "alone confirms continuation, not reversal. Score ≤ 6 for any reversal setup lacking prior CHoCH "
    "on the entry timeframe."
),
```

---

## Testing Plan

### `tests/test_prompt_fragments.py` (update existing + add)
- Verify `SCORING_SCALE` contains `R:R ≥ 2:1` for score 6
- Verify `SCORING_SCALE` contains `R:R ≥ 3.5:1` for score 9
- Verify `LEVEL_PROXIMITY_RULES` contains premium/discount penalty text
- Verify `DRAW_ON_LIQUIDITY_RULES` constant exists and contains "liquidity pools"

### `tests/test_chart_context_scoring.py` (new)
- `test_mfi_weight_bullish`: `_mfi_weight({"mfi": 30})` → 0.3
- `test_mfi_weight_bearish`: `_mfi_weight({"mfi": -25})` → -0.3
- `test_mfi_weight_deadband`: `_mfi_weight({"mfi": 5})` → 0.0
- `test_mfi_weight_empty`: `_mfi_weight({})` → 0.0
- `test_mfi_weight_none`: `_mfi_weight(None)` → 0.0
- `test_confluence_score_includes_mfi`: `confluence_score()` with bullish MFI returns higher score than without
- `test_confluence_max_updated`: `max_val` = `len(tfs) * 6.2` with MFI included

### `tests/test_scanner_killzone.py` (new)
- `test_in_london_killzone`: UTC 08:30 → no annotation
- `test_in_ny_killzone`: UTC 13:00 → no annotation
- `test_outside_killzone_morning`: UTC 05:00 → urgency contains "⚠ Outside kill zone"
- `test_outside_killzone_evening`: UTC 20:00 → urgency contains "⚠ Outside kill zone"
- `test_boundary_london_start`: UTC 07:00 → no annotation (inclusive)
- `test_boundary_london_end`: UTC 10:00 → annotation (exclusive end, 10:00 is outside)
- `test_urgency_field_missing`: result dict without urgency key → sets urgency to annotation
- `test_urgency_field_preserved`: existing urgency text preserved, annotation appended

---

## Files Changed

| File | Lines changed (est.) |
|------|---------------------|
| `prompt_fragments.py` | ~15 (update SCORING_SCALE, LEVEL_PROXIMITY_RULES; add DRAW_ON_LIQUIDITY_RULES) |
| `chart_context.py` | ~20 (add `_mfi_weight()`; update `confluence_score()`, `_get_tf_weights()`) |
| `prompt_builder.py` | ~10 (inject DRAW_ON_LIQUIDITY_RULES; update reversal rubric) |
| `ai_scanner.py` | ~15 (kill zone annotation; update summary string; 1H timeframe) |
| `tests/test_prompt_fragments.py` | ~20 (4 new assertions) |
| `tests/test_chart_context_scoring.py` | ~70 (new file, 7 tests) |
| `tests/test_scanner_killzone.py` | ~80 (new file, 8 tests) |

**No DB migrations. No API shape changes. No new dependencies.**
