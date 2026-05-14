# Trading Signal Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply 7 SMC/ICT and VMC Cipher improvements to the trading journal's scoring, confluence, and prompt systems.

**Architecture:** Four files changed: `prompt_fragments.py` (scoring text), `chart_context.py` (MFI signal weight), `prompt_builder.py` (reversal rubric + LOQ injection), `ai_scanner.py` (kill zone annotation + 1H timeframe). All changes tested with pytest; no DB migrations; no API shape changes.

**Tech Stack:** Python 3.13, pytest, existing project structure at `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/`

---

## File Map

| File | Items | What changes |
|------|-------|-------------|
| `prompt_fragments.py` | 2, 3, 5 | SCORING_SCALE R:R values; LEVEL_PROXIMITY_RULES premium/discount + R:R cap; new DRAW_ON_LIQUIDITY_RULES |
| `chart_context.py` | 4 | New `_mfi_weight()`; update `confluence_score()` base_score + max_val; update `_get_tf_weights()` |
| `prompt_builder.py` | 5, 7 | Inject DRAW_ON_LIQUIDITY_RULES into `build_stable_prefix()`; update reversal rubric |
| `ai_scanner.py` | 1, 2, 6 | Add `_is_in_kill_zone()` + `_annotate_kill_zone()`; update R:R cap string; update finalist timeframes |
| `tests/test_prompt_fragments.py` | 2, 3, 5 | 4 new assertions added to existing file |
| `tests/test_chart_context_scoring.py` | 4 | New file — 7 tests for `_mfi_weight` and confluence integration |
| `tests/test_scanner_killzone.py` | 1 | New file — 8 tests for kill zone detection and annotation |

---

## Task 1: Update prompt_fragments.py — R:R thresholds, premium/discount, LOQ rules

**Files:**
- Modify: `prompt_fragments.py`
- Modify: `tests/test_prompt_fragments.py`

- [ ] **Step 1: Write 4 failing tests — add to bottom of existing test file**

Open `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/tests/test_prompt_fragments.py` and append:

```python
def test_scoring_scale_rr_thresholds_updated():
    """Score 6 requires R:R ≥ 2:1 (raised from 1.5); score 9 requires R:R ≥ 3.5:1."""
    from prompt_fragments import SCORING_SCALE
    assert "R:R ≥ 2:1" in SCORING_SCALE, "Score 6 should require R:R ≥ 2:1"
    assert "R:R ≥ 3.5:1" in SCORING_SCALE, "Score 9 should require R:R ≥ 3.5:1"
    assert "R:R ≥ 1.5:1" not in SCORING_SCALE, "Old 1.5:1 threshold should be removed"


def test_level_proximity_rr_cap_updated():
    """LEVEL_PROXIMITY_RULES cap line must reflect new thresholds."""
    from prompt_fragments import LEVEL_PROXIMITY_RULES
    assert "R:R < 2:1" in LEVEL_PROXIMITY_RULES
    assert "R:R ≥ 2.5:1" in LEVEL_PROXIMITY_RULES
    assert "R:R < 1.5:1" not in LEVEL_PROXIMITY_RULES, "Old 1.5 cap should be gone"


def test_level_proximity_premium_discount_rules():
    """LEVEL_PROXIMITY_RULES must contain premium/discount zone penalty instructions."""
    from prompt_fragments import LEVEL_PROXIMITY_RULES
    assert "premium zone" in LEVEL_PROXIMITY_RULES.lower()
    assert "discount zone" in LEVEL_PROXIMITY_RULES.lower()
    assert "reduce score by 1" in LEVEL_PROXIMITY_RULES.lower()


def test_draw_on_liquidity_rules_exists():
    """DRAW_ON_LIQUIDITY_RULES constant must exist and contain key phrases."""
    from prompt_fragments import DRAW_ON_LIQUIDITY_RULES
    assert "liquidity pools" in DRAW_ON_LIQUIDITY_RULES.lower()
    assert "swing" in DRAW_ON_LIQUIDITY_RULES.lower()
    assert len(DRAW_ON_LIQUIDITY_RULES) > 100
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_prompt_fragments.py::test_scoring_scale_rr_thresholds_updated tests/test_prompt_fragments.py::test_level_proximity_rr_cap_updated tests/test_prompt_fragments.py::test_level_proximity_premium_discount_rules tests/test_prompt_fragments.py::test_draw_on_liquidity_rules_exists -v 2>&1 | tail -15
```
Expected: 4 FAILED (AssertionError)

- [ ] **Step 3: Implement — replace full contents of prompt_fragments.py**

Replace `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/prompt_fragments.py` with:

```python
"""
Shared Claude prompt text blocks. Import instead of copy-pasting.
Each token saved here saves it on every single AI call.
"""

SCORING_SCALE = """SCORING SCALE:
5 — Moderate: mixed signals, borderline — not worth entering without improvement
6 — Acceptable: clear bias + valid level, SL structural, R:R ≥ 2:1
7 — Good: multiple aligned signals, structural entry + SL, R:R ≥ 2.5:1
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 3:1
9 — Excellent: near-ideal — all criteria met, multi-TF alignment, R:R ≥ 3.5:1
10 — Perfect: textbook chart pattern, volume confirmation, ideal entry timing, R:R ≥ 4:1""".strip()

LEVEL_PROXIMITY_RULES = """LEVEL PROXIMITY DEFINITIONS (use when scoring):
- Entry ≤ 0.5× ATR from structural level → strong anchor, no penalty
- Entry 0.5–1.0× ATR from structural level → acceptable, note it
- Entry > 1.0× ATR from nearest level → structural anchor missing → score ≤ 6
- SL < 1.0× ATR from entry → inside noise → score ≤ 6
- R:R < 2:1 → score ≤ 6; R:R ≥ 2.5:1 for score 7+; R:R ≥ 3.5:1 for score 9+
- LONG setup in premium zone (price > midpoint of nearest S/R range) → reduce score by 1
- SHORT setup in discount zone (price < midpoint of nearest S/R range) → reduce score by 1
- Midpoint = (nearest resistance + nearest support) / 2; skip if no S/R levels available""".strip()

MARKET_CONTEXT_RULES = """MARKET CONTEXT WEIGHTING:
- Funding rate > 0.05% in trade direction → reduce score by 1 (crowd on-side, squeeze risk)
- Funding rate > 0.1% in trade direction → reduce score by 2 (extremely crowded)
- Funding rate opposite direction → slight tailwind, can note as positive factor
- Fear & Greed < 20 (Extreme Fear): long bias gets +0.5; short bias gets −0.5
- Fear & Greed > 80 (Extreme Greed): long bias gets −0.5; short bias gets +0.5""".strip()

DRAW_ON_LIQUIDITY_RULES = """TAKE-PROFIT TARGETING:
Prefer TP targets that coincide with visible liquidity pools — equal highs/lows,
prior swing highs/lows, previous ATH/ATL, or untested fair value gaps — rather
than arbitrary R:R multiples. Name the specific level and why liquidity rests there.
A TP at a swing high where stop-losses cluster is higher quality than a round-number TP.""".strip()
```

- [ ] **Step 4: Run all 4 new tests — must pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_prompt_fragments.py -v 2>&1 | tail -15
```
Expected: all tests in file PASS (4 new + 5 existing = 9 total)

- [ ] **Step 5: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add prompt_fragments.py tests/test_prompt_fragments.py
git commit -m "feat: update R:R thresholds, add premium/discount zone penalty, add LOQ rules"
```

---

## Task 2: Add MFI weight to confluence scoring

**Files:**
- Create: `tests/test_chart_context_scoring.py`
- Modify: `chart_context.py`

- [ ] **Step 1: Write the failing test file**

Create `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/tests/test_chart_context_scoring.py`:

```python
"""Tests for _mfi_weight and its integration into confluence_score."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_mfi_weight_bullish():
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": 30.0}) == 0.3


def test_mfi_weight_bearish():
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": -25.0}) == -0.3


def test_mfi_weight_deadband_positive():
    """Values between -10 and +10 return 0."""
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": 5.0}) == 0.0


def test_mfi_weight_deadband_negative():
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": -8.0}) == 0.0


def test_mfi_weight_empty_dict():
    from chart_context import _mfi_weight
    assert _mfi_weight({}) == 0.0


def test_mfi_weight_none():
    from chart_context import _mfi_weight
    assert _mfi_weight(None) == 0.0


def test_mfi_weight_boundary_positive_10():
    """Exactly 10 is inside the dead-band → 0."""
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": 10.0}) == 0.0


def test_mfi_weight_boundary_above_10():
    """11 is outside dead-band → bullish."""
    from chart_context import _mfi_weight
    assert _mfi_weight({"mfi": 11.0}) == 0.3


def test_confluence_score_max_val_updated():
    """max_val in confluence_score must equal len(tfs) * 6.2 when MFI included."""
    from chart_context import confluence_score
    import unittest.mock as mock

    mock_ctx = {
        "4H": {"indicators": {"ok": True, "rsi": {"value": 50}, "macd": {}, "ema": {},
                               "adx": {}, "wavetrend": {"mfi": 0.0}, "cvd": {}, "volume": {}}},
        "1D": {"indicators": {"ok": True, "rsi": {"value": 50}, "macd": {}, "ema": {},
                               "adx": {}, "wavetrend": {"mfi": 0.0}, "cvd": {}, "volume": {}}},
    }
    with mock.patch("chart_context.get_chart_context", return_value=mock_ctx):
        result = confluence_score("BTCUSDT", ["4H", "1D"], ctx=mock_ctx)
    assert result["max"] == pytest.approx(2 * 6.2, rel=1e-3), \
        f"Expected max=12.4, got {result['max']}"


def test_confluence_score_mfi_raises_bullish_score():
    """Strong bullish MFI increases the score compared to neutral MFI."""
    from chart_context import confluence_score
    import unittest.mock as mock

    def _make_ctx(mfi_val):
        return {
            "4H": {"indicators": {"ok": True,
                "rsi": {"value": 65}, "macd": {"trend": "bullish", "histogram_trend": "growing"},
                "ema": {"alignment": "fully bullish", "stack": "bullish"},
                "adx": {"direction": "bullish", "value": 30},
                "wavetrend": {"signal": "buy", "wt1": 20.0, "mfi": mfi_val},
                "cvd": {"trend": "rising"}, "volume": {"ratio": 1.8}}},
        }

    with mock.patch("chart_context.get_chart_context", return_value=_make_ctx(50.0)):
        score_with_mfi = confluence_score("BTCUSDT", ["4H"], ctx=_make_ctx(50.0))
    with mock.patch("chart_context.get_chart_context", return_value=_make_ctx(0.0)):
        score_neutral = confluence_score("BTCUSDT", ["4H"], ctx=_make_ctx(0.0))

    assert score_with_mfi["score"] > score_neutral["score"], \
        "Bullish MFI should increase confluence score"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_chart_context_scoring.py -v 2>&1 | tail -20
```
Expected: ImportError `cannot import name '_mfi_weight'`

- [ ] **Step 3: Add `_mfi_weight` function to chart_context.py**

In `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/chart_context.py`, find the `_cvd_weight` function:
```python
def _cvd_weight(cvd: dict) -> float:
    """CVD rising = bullish signal (+0.4), falling = bearish (-0.4), flat = 0."""
    trend = cvd.get("trend", "flat")
    return 0.4 if trend == "rising" else (-0.4 if trend == "falling" else 0.0)
```

Add immediately after it (before `_get_tf_weights`):

```python
def _mfi_weight(wt: dict) -> float:
    """
    MFI (Money Flow) contribution from WaveTrend data.
    MFI > 10 = capital inflow (bullish +0.3), MFI < -10 = outflow (bearish -0.3).
    Dead-band ±10 avoids noise near zero.
    """
    mfi = wt.get("mfi", 0.0) if wt else 0.0
    if mfi > 10:   return  0.3
    if mfi < -10:  return -0.3
    return 0.0
```

- [ ] **Step 4: Update `confluence_score` to include MFI in base_score**

In `confluence_score()`, find this block:
```python
        rsi_w  = _rsi_weight(inds.get("rsi",  {}).get("value", 50))
        macd_w = _macd_weight(inds.get("macd", {}))
        ema_w  = _ema_weight(inds.get("ema",   {}))
        adx_w  = _adx_weight(inds.get("adx",   {}))
        wt_w   = _wt_weight(inds.get("wavetrend", {}))
        cvd_w  = _cvd_weight(inds.get("cvd", {}))
        base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + cvd_w
        vol_w  = _volume_weight(inds, base_score)

        tf_score = base_score + vol_w
        total_score += tf_score

        pos = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, cvd_w, vol_w) if w > 0), 1)
        neg = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, cvd_w, vol_w) if w < 0), 1)
```

Replace with:
```python
        rsi_w  = _rsi_weight(inds.get("rsi",  {}).get("value", 50))
        macd_w = _macd_weight(inds.get("macd", {}))
        ema_w  = _ema_weight(inds.get("ema",   {}))
        adx_w  = _adx_weight(inds.get("adx",   {}))
        wt_w   = _wt_weight(inds.get("wavetrend", {}))
        mfi_w  = _mfi_weight(inds.get("wavetrend", {}))
        cvd_w  = _cvd_weight(inds.get("cvd", {}))
        base_score = rsi_w + macd_w + ema_w + adx_w + wt_w + mfi_w + cvd_w
        vol_w  = _volume_weight(inds, base_score)

        tf_score = base_score + vol_w
        total_score += tf_score

        pos = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, mfi_w, cvd_w, vol_w) if w > 0), 1)
        neg = round(sum(w for w in (rsi_w, macd_w, ema_w, adx_w, wt_w, mfi_w, cvd_w, vol_w) if w < 0), 1)
```

- [ ] **Step 5: Update max_val comment and value**

In `confluence_score()`, find:
```python
    max_val = float(len(tfs) * 5.9)  # 6 directional signals (max 1.0 each, CVD 0.4) + vol (0.5)
```

Replace with:
```python
    max_val = float(len(tfs) * 6.2)  # 7 directional signals: RSI+MACD+EMA+ADX+WT(1.0 each) + MFI(0.3) + CVD(0.4) + vol(0.5)
```

- [ ] **Step 6: Update `_get_tf_weights` to include MFI**

In `_get_tf_weights()`, find:
```python
    base = [
        _rsi_weight(inds.get("rsi",  {}).get("value", 50)),
        _macd_weight(inds.get("macd", {})),
        _ema_weight(inds.get("ema",   {})),
        _adx_weight(inds.get("adx",   {})),
        _wt_weight(inds.get("wavetrend", {})),
        _cvd_weight(inds.get("cvd", {})),
    ]
```

Replace with:
```python
    base = [
        _rsi_weight(inds.get("rsi",  {}).get("value", 50)),
        _macd_weight(inds.get("macd", {})),
        _ema_weight(inds.get("ema",   {})),
        _adx_weight(inds.get("adx",   {})),
        _wt_weight(inds.get("wavetrend", {})),
        _mfi_weight(inds.get("wavetrend", {})),
        _cvd_weight(inds.get("cvd", {})),
    ]
```

- [ ] **Step 7: Run all new tests — must pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_chart_context_scoring.py -v 2>&1 | tail -20
```
Expected: 11 PASSED

- [ ] **Step 8: Run existing chart tests to confirm no regressions**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_chart_indicators.py tests/test_chart_sr.py -v 2>&1 | tail -10
```
Expected: same pass/fail count as before (pre-existing pandas_ta failures unchanged)

- [ ] **Step 9: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add chart_context.py tests/test_chart_context_scoring.py
git commit -m "feat: add MFI as standalone confluence signal (weight ±0.3, dead-band ±10)"
```

---

## Task 3: Update prompt_builder.py — LOQ injection + reversal rubric

**Files:**
- Modify: `prompt_builder.py`

- [ ] **Step 1: Add DRAW_ON_LIQUIDITY_RULES import**

In `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/prompt_builder.py`, find the existing imports at the top. Add `DRAW_ON_LIQUIDITY_RULES` to the import from `prompt_fragments`:

The file currently doesn't import from `prompt_fragments`. Add this import after the existing imports (after `from analytics import get_backtest_context`):

```python
from prompt_fragments import DRAW_ON_LIQUIDITY_RULES
```

- [ ] **Step 2: Inject DRAW_ON_LIQUIDITY_RULES into build_stable_prefix**

In `build_stable_prefix()`, find:
```python
def build_stable_prefix(conn, exchange_filter: str = None) -> str:
    ...
    sections   = []
    remaining  = MAX_CONTEXT_CHARS
```

Replace with:
```python
def build_stable_prefix(conn, exchange_filter: str = None) -> str:
    ...
    sections   = [DRAW_ON_LIQUIDITY_RULES]
    remaining  = MAX_CONTEXT_CHARS - len(DRAW_ON_LIQUIDITY_RULES)
```

- [ ] **Step 3: Update reversal rubric with BOS/CHoCH requirement**

In `_RUBRICS`, replace the `"reversal"` entry:

```python
    "reversal": (
        "REVERSAL RUBRIC: 9-10 = extreme RSI divergence at major S/R, multi-TF confirmation, "
        "clear candle rejection pattern, R:R ≥ 3.5:1. 7-8 = strong level + indicator signal. "
        "6 = moderate confluence only. Penalise reversals against the weekly trend unless very strong. "
        "CRITICAL: Require CHoCH (Change of Character) confirmation before entry — a BOS (Break of "
        "Structure) alone confirms continuation, not reversal. Score ≤ 6 for any reversal setup "
        "lacking prior CHoCH on the entry timeframe."
    ),
```

- [ ] **Step 4: Verify import works**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -c "from prompt_builder import build_stable_prefix, get_setup_rubric; r = get_setup_rubric('reversal'); assert 'CHoCH' in r; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add prompt_builder.py
git commit -m "feat: inject LOQ rules into stable prefix; add BOS/CHoCH requirement to reversal rubric"
```

---

## Task 4: Update ai_scanner.py — kill zone annotation, R:R cap string, 1H timeframe

**Files:**
- Create: `tests/test_scanner_killzone.py`
- Modify: `ai_scanner.py`

- [ ] **Step 1: Write failing tests for kill zone**

Create `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/tests/test_scanner_killzone.py`:

```python
"""Tests for kill zone detection and urgency annotation in ai_scanner."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_london_killzone_start_is_active():
    """07:00 UTC is inside London kill zone (inclusive start)."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=7) is True


def test_london_killzone_middle_is_active():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=8) is True


def test_london_killzone_end_is_inactive():
    """10:00 UTC is exclusive end of London window → outside."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=10) is False


def test_ny_am_killzone_is_active():
    """13:00 UTC is inside NY AM kill zone."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=13) is True


def test_ny_am_killzone_end_is_inactive():
    """15:00 UTC is exclusive end of NY AM window → outside."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=15) is False


def test_outside_both_killzones_morning():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=5) is False


def test_outside_both_killzones_evening():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=20) is False


def test_annotate_outside_killzone_appends_warning():
    """Result outside kill zone gets warning appended to urgency field."""
    from ai_scanner import _annotate_kill_zone
    result = {"urgency": "Now", "setup_score": 8}
    annotated = _annotate_kill_zone(result, utc_hour=5)
    assert "⚠ Outside kill zone" in annotated["urgency"]
    assert "Now" in annotated["urgency"]


def test_annotate_inside_killzone_no_change():
    """Result inside kill zone must not be modified."""
    from ai_scanner import _annotate_kill_zone
    result = {"urgency": "Now", "setup_score": 8}
    annotated = _annotate_kill_zone(result, utc_hour=8)
    assert annotated["urgency"] == "Now"


def test_annotate_missing_urgency_field():
    """Result with no urgency key gets urgency set to warning string."""
    from ai_scanner import _annotate_kill_zone
    result = {"setup_score": 7}
    annotated = _annotate_kill_zone(result, utc_hour=20)
    assert annotated["urgency"] == "⚠ Outside kill zone"


def test_annotate_inside_killzone_missing_urgency_no_change():
    """Inside kill zone: missing urgency field stays missing (no spurious key added)."""
    from ai_scanner import _annotate_kill_zone
    result = {"setup_score": 7}
    annotated = _annotate_kill_zone(result, utc_hour=9)
    assert "urgency" not in annotated
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_scanner_killzone.py -v 2>&1 | tail -20
```
Expected: ImportError — `_is_in_kill_zone` and `_annotate_kill_zone` not defined

- [ ] **Step 3: Add `_is_in_kill_zone` and `_annotate_kill_zone` to ai_scanner.py**

In `/Users/fbauer/Documents/ClaudeAIData/Trading-Journal/ai_scanner.py`, find the imports section at the top. The file already imports `time` and `threading`. Add `import datetime` after `import time`.

Then, after the `_disabled_criteria_block` function (around line 131) and before the `_state` dict, insert:

```python
# ── Kill zone helpers ──────────────────────────────────────────────────────────

def _is_in_kill_zone(utc_hour: int = None) -> bool:
    """
    Return True if the given UTC hour falls within an institutional kill zone.
    London: 07:00–09:59 UTC  |  NY AM: 12:00–14:59 UTC
    Pass utc_hour explicitly for testing; defaults to current UTC time.
    """
    h = utc_hour if utc_hour is not None else datetime.datetime.utcnow().hour
    return (7 <= h < 10) or (12 <= h < 15)


def _annotate_kill_zone(result: dict, utc_hour: int = None) -> dict:
    """
    Append '⚠ Outside kill zone' to the urgency field when outside institutional windows.
    No-op when inside a kill zone. Returns the result dict (mutated in place).
    """
    if _is_in_kill_zone(utc_hour):
        return result
    warning = "⚠ Outside kill zone"
    if "urgency" in result:
        existing = result["urgency"]
        result["urgency"] = (existing + " " + warning).strip() if existing else warning
    return result
```

- [ ] **Step 4: Apply `_annotate_kill_zone` in `_batch_ai_score`**

In `_batch_ai_score()`, find the inner loop that builds the output list:
```python
        out = []
        for i, (symbol, ctx, conf, direction, _score, _reason) in enumerate(finalists):
            r = results[i] if i < len(results) else {}
            if r.get("setup_score", 0) < min_score:
                continue
            r["_symbol"] = symbol
            out.append(r)
```

Replace with:
```python
        out = []
        for i, (symbol, ctx, conf, direction, _score, _reason) in enumerate(finalists):
            r = results[i] if i < len(results) else {}
            if r.get("setup_score", 0) < min_score:
                continue
            r["_symbol"] = symbol
            _annotate_kill_zone(r)
            out.append(r)
```

- [ ] **Step 5: Apply `_annotate_kill_zone` in `_ai_score`**

In `_ai_score()`, find:
```python
        result["_symbol"] = symbol
        return result
```

Replace with:
```python
        result["_symbol"] = symbol
        _annotate_kill_zone(result)
        return result
```

- [ ] **Step 6: Update R:R cap string in `_build_scanner_stable`**

In `_build_scanner_stable()`, find:
```python
if cr.get("rr_minimum",True): caps.append("R:R below 1.5:1")
```

Replace with:
```python
if cr.get("rr_minimum",True): caps.append("R:R below 2:1")
```

Also find the inline summary string:
```python
        + "5=Mod(borderline), 6=Accept(R:R≥1.5), 7=Good(R:R≥2:1), "
        + "8=Strong(R:R≥2.5:1), 9=Excellent(multi-TF,R:R≥3:1), 10=Perfect(R:R≥4:1)\n"
```

Replace with:
```python
        + "5=Mod(borderline), 6=Accept(R:R≥2), 7=Good(R:R≥2.5), "
        + "8=Strong(R:R≥3), 9=Excellent(multi-TF,R:R≥3.5), 10=Perfect(R:R≥4)\n"
```

Also append DRAW_ON_LIQUIDITY_RULES to the stable scanner prefix. In `_build_scanner_stable()`, find:
```python
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES
```
(this import is at the top of the file — line 26)

Update it to:
```python
from prompt_fragments import SCORING_SCALE, LEVEL_PROXIMITY_RULES, MARKET_CONTEXT_RULES, DRAW_ON_LIQUIDITY_RULES
```

Then in `_build_scanner_stable()`, find the return statement:
```python
    return (
        f"{rb_block}"
        + SCORING_SCALE + "\n"
        + "5=Mod(borderline), 6=Accept(R:R≥2), 7=Good(R:R≥2.5), "
        + "8=Strong(R:R≥3), 9=Excellent(multi-TF,R:R≥3.5), 10=Perfect(R:R≥4)\n"
        + f"Score <{min_score} if: {cap_str}.{dis_part}"
    )
```

Replace with:
```python
    return (
        f"{rb_block}"
        + SCORING_SCALE + "\n"
        + "5=Mod(borderline), 6=Accept(R:R≥2), 7=Good(R:R≥2.5), "
        + "8=Strong(R:R≥3), 9=Excellent(multi-TF,R:R≥3.5), 10=Perfect(R:R≥4)\n"
        + f"Score <{min_score} if: {cap_str}.{dis_part}\n\n"
        + DRAW_ON_LIQUIDITY_RULES
    )
```

- [ ] **Step 7: Update `_score_finalists_with_agents` to use 1H timeframe**

In `_score_finalists_with_agents()`, find:
```python
            collected = agent_data_collector.run({
                "symbol": sym, "direction": direction, "timeframes": ["4H", "1D"],
            })
```

Replace with:
```python
            collected = agent_data_collector.run({
                "symbol": sym, "direction": direction, "timeframes": ["1H", "4H", "1D"],
            })
```

- [ ] **Step 8: Run all kill zone tests — must pass**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/test_scanner_killzone.py -v 2>&1 | tail -20
```
Expected: 11 PASSED

- [ ] **Step 9: Verify import works**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -c "import ai_scanner; print('ok')"
```
Expected: `ok`

- [ ] **Step 10: Commit**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add ai_scanner.py tests/test_scanner_killzone.py
git commit -m "feat: kill zone urgency annotation, updated R:R cap string, 1H timeframe for finalists"
```

---

## Task 5: Full test suite + version bump + deploy

**Files:**
- Modify: `constants.py` (VERSION)

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: All new tests pass (11 kill zone + 11 chart scoring + 9 prompt fragments = 31 new). Pre-existing 15 failures in test_chart_indicators/test_chart_sr unchanged. Zero new failures.

- [ ] **Step 2: Bump version to 1.3.0**

In `constants.py`, change:
```python
VERSION = "1.2.0"
```
to:
```python
VERSION = "1.3.0"
```

- [ ] **Step 3: Commit version bump**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git add constants.py
git commit -m "chore: bump version to 1.3.0 — trading signal improvements"
```

- [ ] **Step 4: Push to GitHub**

```bash
cd /Users/fbauer/Documents/ClaudeAIData/Trading-Journal
git push origin main
```

- [ ] **Step 5: Deploy to Pi via systemctl**

```bash
expect -c "
  set timeout 60
  spawn ssh -o StrictHostKeyChecking=no fbauer@192.168.1.21
  expect \"password:\"
  send \"laZHn0rd\r\"
  expect \"\\\$\"
  send \"cd /home/fbauer/trading-journal && git fetch origin && git reset --hard origin/main && sudo systemctl restart trading-journal && sleep 3 && sudo systemctl status trading-journal --no-pager | head -6\r\"
  expect \"\\\$\"
  send \"exit\r\"
  expect eof
"
```
Expected: `Active: active (running)`

- [ ] **Step 6: Smoke-test on Pi**

```bash
curl -s http://192.168.1.21:8082/api/calls/accuracy-progress | python3 -m json.tool
```
Expected: `"ok": true` (confirms app is up and DB accessible)

- [ ] **Step 7: Update project memory**

Update `/Users/fbauer/.claude/projects/-Users-fbauer/memory/project_trading_journal.md` to reflect v1.3.0 and the 7 improvements.

---

## Self-Review

**Spec coverage:**
- [x] Item 1 — kill zone urgency annotation → Task 4 steps 3–5
- [x] Item 2 — R:R thresholds (Option B) → Task 1 + Task 4 step 6
- [x] Item 3 — premium/discount zone penalty → Task 1 (LEVEL_PROXIMITY_RULES)
- [x] Item 4 — MFI standalone signal → Task 2
- [x] Item 5 — DRAW_ON_LIQUIDITY_RULES → Task 1 (constant) + Task 3 (injection) + Task 4 (scanner injection)
- [x] Item 6 — 1H finalist timeframe → Task 4 step 7
- [x] Item 7 — BOS/CHoCH reversal rubric → Task 3 step 3
- [x] Tests → test_prompt_fragments.py (4 new), test_chart_context_scoring.py (11 new), test_scanner_killzone.py (11 new)
- [x] Deploy → Task 5

**Type/name consistency:**
- `_is_in_kill_zone(utc_hour)` — defined Task 4 step 3, tested Task 4 step 1 ✓
- `_annotate_kill_zone(result, utc_hour)` — defined Task 4 step 3, applied steps 4+5, tested step 1 ✓
- `_mfi_weight(wt)` — defined Task 2 step 3, used in confluence_score step 4, in _get_tf_weights step 6, tested step 1 ✓
- `DRAW_ON_LIQUIDITY_RULES` — defined Task 1, imported Task 3+4 ✓
- `max_val = float(len(tfs) * 6.2)` — updated Task 2 step 5, tested step 7 ✓

**Placeholder scan:** No TBDs, no vague steps. All code blocks are complete.
