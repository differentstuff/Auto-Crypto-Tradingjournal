"""
Shared Claude prompt text blocks. Import instead of copy-pasting.
Each token saved here saves it on every single AI call.
"""

SCORING_SCALE = """SCORING SCALE:
5 — Moderate: mixed signals, borderline — not worth entering without improvement
6 — Acceptable: clear bias + valid level, SL structural, R:R ≥ 1.5:1 — tradeable
7 — Good: multiple aligned signals, structural entry + SL, R:R ≥ 2:1
8 — Strong: ≥3 signals aligned, clean S/R entry, structural SL, R:R ≥ 2.5:1
9 — Excellent: near-ideal — all criteria met, multi-TF alignment, R:R ≥ 3:1
10 — Perfect: textbook chart pattern, volume confirmation, ideal entry timing, R:R ≥ 4:1""".strip()

LEVEL_PROXIMITY_RULES = """LEVEL PROXIMITY DEFINITIONS (use when scoring):
- Entry ≤ 0.5× ATR from structural level → strong anchor, no penalty
- Entry 0.5–1.0× ATR from structural level → acceptable, note it
- Entry > 1.0× ATR from nearest level → structural anchor missing → score ≤ 6
- SL < 1.0× ATR from entry → inside noise → score ≤ 6
- R:R < 1.5:1 → score ≤ 6; R:R ≥ 2:1 for score 7+; R:R ≥ 3:1 for score 9+""".strip()

MARKET_CONTEXT_RULES = """MARKET CONTEXT WEIGHTING:
- Funding rate > 0.05% in trade direction → reduce score by 1 (crowd on-side, squeeze risk)
- Funding rate > 0.1% in trade direction → reduce score by 2 (extremely crowded)
- Funding rate opposite direction → slight tailwind, can note as positive factor
- Fear & Greed < 20 (Extreme Fear): long bias gets +0.5; short bias gets −0.5
- Fear & Greed > 80 (Extreme Greed): long bias gets −0.5; short bias gets +0.5""".strip()
