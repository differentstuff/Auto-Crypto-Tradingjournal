# Setup Quality Scoring Guide — 1 to 10

This document defines exactly what each score level means for a trade setup. Used by the Setup Scanner, Call Analyzer, and Hindsight Analysis modules. Every score is based on the same underlying rubric regardless of which module produces it.

---

## Quick Reference

| Score | Label | One-line definition |
|-------|-------|---------------------|
| 1 | Terrible | No rationale, no levels, no plan — pure gamble |
| 2 | Very Poor | Vague idea, wrong entry zone, SL missing or inside noise |
| 3 | Poor | Some directional view but bad structure — chasing or arbitrary SL |
| 4 | Weak | Entry is plausible but R:R poor, SL too tight, or signals conflict |
| 5 | Moderate | Decent idea, acceptable structure, but nothing exceptional — borderline |
| 6 | Acceptable | Clear bias + valid level, SL structural, R:R ≥ 1.5:1 — tradeable |
| 7 | Good | Multiple aligned signals, structural entry + SL, R:R ≥ 2:1 |
| 8 | Strong | ≥ 3 signals, clean structure, R:R ≥ 2.5:1, no rulebook conflict |
| 9 | Excellent | Near-ideal setup — all criteria met, multi-TF alignment, R:R ≥ 3:1 |
| 10 | Perfect | Textbook execution opportunity — everything aligned at the same time |

---

## Scoring Factors

Every setup is evaluated across eight factors. Each factor contributes to the final score.

| Factor | Weight in score |
|--------|----------------|
| Technical confluence (signals aligned) | High |
| Entry quality (is the level structural?) | High |
| Stop loss quality (structural + ATR distance) | High |
| Risk:Reward ratio (to TP1 and TP2) | High |
| Multi-timeframe alignment | Medium |
| Market conditions (funding, F&G, L/S) | Medium |
| Chart pattern / setup type clarity | Medium |
| Rulebook compliance (known weaknesses) | Medium |

---

## Detailed Per-Score Breakdown

### Score 1 — Terrible

> "This is a coin flip with extra steps."

| Factor | Description |
|--------|-------------|
| Technical confluence | No aligned signals — RSI, MACD, EMA all mixed or against the direction |
| Entry | No logical entry level — entering at a random price in the middle of a range |
| Stop loss | Missing entirely, or placed so close it will be hit by normal candle noise |
| R:R | < 1:1 or not defined at all |
| Multi-TF | All timeframes against the direction or no analysis done |
| Market | Extreme adverse conditions (funding > 0.1% against, F&G extreme, crowded retail) |
| Pattern | No recognizable setup — no structure, no context |
| Rulebook | Violates multiple known weaknesses |

**Example:** FOMO long into a parabolic pump with no SL, funding rate at +0.1%, RSI 85, no defined target.

---

### Score 2 — Very Poor

> "There is a vague directional view but no tradeable plan."

| Factor | Description |
|--------|-------------|
| Technical confluence | At most 1 signal in the direction — most indicators mixed or opposing |
| Entry | Entry is at an arbitrary price or chasing a move > 3% from the structural level |
| Stop loss | Missing, or inside 1H ATR noise (< 0.5× ATR from entry) |
| R:R | < 1:1 even if a target is mentioned |
| Multi-TF | Primary TF barely aligned, higher TF is against the trade |
| Market | At least two adverse market conditions present |
| Pattern | Pattern named but setup doesn't match the described structure |
| Rulebook | Violates at least one known significant weakness |

**Example:** Long with a mental note "it looks oversold" — no SL price, target is "moon," RSI is 38 and falling, 1D trend is down.

---

### Score 3 — Poor

> "Directional view exists but execution structure is broken."

| Factor | Description |
|--------|-------------|
| Technical confluence | 1–2 signals aligned but primary indicator (trend/EMA) opposes the trade |
| Entry | Entry zone identifiable but price is chasing (1–3% past the level) |
| Stop loss | Below/above a level but inside 1× ATR — high noise risk |
| R:R | 1:1 to 1.5:1 — risk and reward nearly equal |
| Multi-TF | 4H aligned but 1D clearly against the direction |
| Market | One significant adverse condition (e.g. high funding or extreme sentiment) |
| Pattern | Partial pattern — breakout without retest, or reversal without confirmation |
| Rulebook | One known weakness flagged |

**Example:** Short after a candle closes red, but 1D is in an uptrend, SL is 0.8× ATR above entry, target at previous support with only 1.2:1 R:R.

---

### Score 4 — Weak

> "The idea has merit but the plan is not worth the risk."

| Factor | Description |
|--------|-------------|
| Technical confluence | 2 signals aligned, but ADX < 15 or EMA stack mixed |
| Entry | At or near a level, but it's a weak level (only 1–2 touches, not major) |
| Stop loss | Structural level but tight — distance is 1.0–1.2× ATR |
| R:R | 1.5:1 to 2:1 — acceptable but entry quality doesn't justify it |
| Multi-TF | 4H aligned, 1D neutral (not helping, not hurting) |
| Market | Neutral to slightly adverse conditions |
| Pattern | Pattern present but incomplete or in a low-conviction zone |
| Rulebook | No violations but no positive factors either |

**Example:** Long at a support level with 2 touches, RSI reset to 48, MACD flat, SL just under the level at 1.1× ATR, TP at next resistance for 1.8:1 R:R.

---

### Score 5 — Moderate

> "Tradeable if you have high risk tolerance, but nothing special."

| Factor | Description |
|--------|-------------|
| Technical confluence | 2–3 signals aligned including at least one trend indicator (EMA or ADX) |
| Entry | At a meaningful level (3+ touches or confluence of two levels) |
| Stop loss | Beyond a structural level, 1.2–1.5× ATR from entry |
| R:R | 2:1 — sufficient but not compelling |
| Multi-TF | 4H aligned, 1D neutral or partially aligned |
| Market | Neutral conditions — no strong tailwind or headwind |
| Pattern | A recognizable setup (e.g. pullback to EMA, range support) without textbook structure |
| Rulebook | Clean — no known weaknesses triggered |

**Example:** Long at a tested support zone, RSI 50 (neutral), MACD slightly bullish, EMA stack mixed (above 20 and 50 but below 200), SL at 1.3× ATR below support, TP for 2:1 R:R.

---

### Score 6 — Acceptable

> "A legitimate trade worth taking with standard position sizing."

| Factor | Description |
|--------|-------------|
| Technical confluence | 3 signals aligned, including EMA stack direction OR ADX confirming trend |
| Entry | At a well-defined level — support/resistance with 3+ historical touches, or EMA confluence |
| Stop loss | Beyond a structural level AND ≥ 1.5× ATR — clearly outside noise |
| R:R | ≥ 2:1 to TP1, with a defined TP2 ≥ 3:1 |
| Multi-TF | 4H aligned, 1D at least neutral (not opposing) |
| Market | Neutral to mildly favorable (funding near zero, F&G 30–70) |
| Pattern | Clear pattern type identifiable (pullback, breakout, base-and-rally) |
| Rulebook | No violations, respects trader's known weak spots |

**Example:** Long at a 4H support zone (5 touches) with RSI reset to 45 from 70, MACD bullish cross, EMA 20/50 stack bullish, SL at 1.5× ATR below support, TP1 at previous 4H high for 2.2:1, TP2 at 1D resistance for 3.5:1.

---

### Score 7 — Good

> "High-quality setup — clear plan, solid structure, conviction justified."

| Factor | Description |
|--------|-------------|
| Technical confluence | 3–4 signals aligned including EMA stack AND ADX > 20 confirming trend |
| Entry | At a high-quality structural zone (well-defined S/R + EMA confluence, or trendline tap) |
| Stop loss | Beyond a structural level AND ≥ 1.8× ATR — minimal noise risk |
| R:R | ≥ 2.5:1 to TP1, ≥ 4:1 to TP2 |
| Multi-TF | 4H aligned + 1D aligned in same direction |
| Market | Favorable — low funding, neutral-to-greed F&G, no crowded positioning |
| Pattern | Clear, named pattern with defined entry trigger and measured target |
| Rulebook | Fully clean — actively avoids the trader's documented weaknesses |

**Example:** Long at 4H ascending trendline support (6 touches) with 1D EMA stack fully bullish, RSI 48 (reset from 72), MACD bullish crossover on 4H, ADX 28 and rising, funding near zero, SL 1.8× ATR below trendline at swing low, TP1 for 2.8:1, TP2 for 4.5:1.

---

### Score 8 — Strong

> "Everything working together — take this trade with full sizing."

| Factor | Description |
|--------|-------------|
| Technical confluence | ≥ 4 aligned signals across multiple indicators — RSI/MACD/EMA/ADX all in agreement |
| Entry | At a high-conviction zone — confluence of 2+ independent levels (e.g. 4H S/R + 1D EMA50 + trendline) |
| Stop loss | Beyond a well-tested structural level AND ≥ 2× ATR — highly protected |
| R:R | ≥ 3:1 to TP1, ≥ 5:1 to TP2 |
| Multi-TF | 4H + 1D both aligned; 1W neutral or supportive |
| Market | Favorable — low/negative funding (longs), F&G 40–65, L/S ratio not crowded |
| Pattern | Textbook or near-textbook pattern — clear trigger, measured target, defined invalidation |
| Rulebook | Not only clean but actively matches the trader's documented strengths |

**Example:** Long at a major 1D support level (8 touches over 6 months) + 4H EMA50 confluence. RSI 46 after a full reset from 76. MACD bullish crossover 4H. EMA 20/50/200 fully bullish stack on 1D. ADX 31 trending up. Funding rate -0.01% (favorable for longs). F&G 55 (neutral). SL 2.1× ATR below, TP1 3.2:1, TP2 5.8:1.

---

### Score 9 — Excellent

> "Near-perfect setup — rare, high-conviction, patient execution justified."

| Factor | Description |
|--------|-------------|
| Technical confluence | All 4–5 signals aligned AND confirming each other across 4H and 1D |
| Entry | Price is exactly at the high-conviction zone — touching the level right now, not "near" it |
| Stop loss | Beyond a tested swing high/low, ≥ 2× ATR — structurally clean, tight relative to the move |
| R:R | ≥ 3.5:1 to TP1, ≥ 6:1 to TP2 |
| Multi-TF | 4H + 1D + 1W all aligned — the weekly trend supports the trade direction |
| Market | All favorable: low/negative funding, F&G 45–60 (not extreme), L/S ratio uncrowded |
| Pattern | Textbook pattern with volume confirmation at the trigger point |
| Rulebook | Matches documented strengths AND avoids documented weaknesses — alignment is total |

**Requires all of:** structural entry at a well-tested zone, multiple TF alignment, RSI not overextended (40–58 for Long, 42–60 for Short at entry), clean SL at minimum 2× ATR, pattern confirmed with volume, market conditions not working against it, and no rulebook conflicts.

**Example:** Long at the weekly EMA50 (first test since rally started), coinciding with 1D major support (12 touches), RSI reset to 44 on both 4H and 1D after being at 71, MACD bullish crossover on 4H while 1D MACD holds bullish, ADX 35 and rising on 1D, EMA 20/50/200 fully bullish on 1D and 4H, funding rate -0.015%, F&G 52, low retail L/S crowding, volume spike on the support touch. SL 2.3× ATR below weekly EMA. TP1 at previous swing high for 3.8:1, TP2 at 1.618 Fibonacci extension for 7.2:1.

---

### Score 10 — Perfect

> "Everything aligns at the same moment — maximum conviction, maximum sizing."

| Factor | Description |
|--------|-------------|
| Technical confluence | Every applicable indicator aligned — no conflicting signal anywhere |
| Entry | Exact touch of a major structural zone in real-time — price is at the level NOW with confirmation |
| Stop loss | Below/above the most recent swing structure AND ≥ 2.5× ATR — virtually zero premature stop risk |
| R:R | ≥ 4:1 to TP1, ≥ 7:1 to TP2 |
| Multi-TF | 4H + 1D + 1W + monthly (if visible) all in the same direction |
| Market | All three market signals favorable (funding, F&G, L/S) AND no macro event risk |
| Pattern | Perfect textbook pattern — breakout with retest, W-bottom with volume, flag with measured target |
| Rulebook | Directly matches the trader's best-performing setup type AND holds period |

**Requires additionally:** a clear and immediate entry trigger (not "wait and see"), a chart pattern where the invalidation level is obvious and the measured target is clear, and a market environment where no known risks are elevated.

**These setups are rare by definition.** If a 10/10 appears more than 2–3 times per month, the scoring is too generous.

---

## Factor Breakdown by Score

A concise summary of how each factor maps to each score level:

### Technical Confluence (signals aligned out of possible 8 across 4H+1D)

| Score | Bullish/Bearish signals | Trend confirmed? |
|-------|------------------------|-----------------|
| 1–2 | 0–1 | No — opposing signals dominate |
| 3–4 | 1–2 | No — primary trend against the trade |
| 5–6 | 2–3 | Partially — EMA or ADX neutral |
| 7–8 | 3–4 | Yes — EMA stack + ADX confirming |
| 9–10 | 5–8 | Yes — all TFs confirming |

### Entry Quality

| Score | Entry zone description |
|-------|----------------------|
| 1–2 | No level — random price or deep into a move |
| 3–4 | Weak level (1–2 historical touches, or chasing > 2%) |
| 5–6 | Valid level (3+ touches, or EMA confluence) |
| 7–8 | High-quality level (multiple confluences at same price) |
| 9–10 | Precise touch of a major level with confirmation signal |

### Stop Loss Quality

| Score | SL description | ATR distance |
|-------|---------------|-------------|
| 1–2 | Missing or mental | < 0.5× ATR |
| 3–4 | Price-based but too tight | 0.5–1.0× ATR |
| 5–6 | Beyond a structural level | 1.0–1.5× ATR |
| 7–8 | Beyond a strong structural level | 1.5–2.0× ATR |
| 9–10 | Beyond a major tested level | ≥ 2.0× ATR |

### Risk:Reward (to TP1)

| Score | R:R to TP1 | R:R to TP2 |
|-------|-----------|-----------|
| 1–2 | < 1:1 or undefined | Undefined |
| 3–4 | 1:1 to 1.5:1 | < 2:1 |
| 5–6 | 1.5:1 to 2:1 | 2:1 to 3:1 |
| 7–8 | 2.5:1 to 3.5:1 | 4:1 to 6:1 |
| 9–10 | ≥ 3.5:1 | ≥ 6:1 |

### RSI at Entry (Long example — reverse for Short)

| Score | RSI at entry | Interpretation |
|-------|-------------|---------------|
| 1–2 | > 82 | Severely overbought — chasing |
| 3–4 | 70–82 | Overbought — late entry |
| 5–6 | 55–70 | Extended but not extreme |
| 7–8 | 42–55 | Healthy — reset without breaking trend |
| 9–10 | 40–50 | Full reset — maximum room to run |

### Multi-Timeframe Alignment

| Score | Alignment |
|-------|-----------|
| 1–2 | All TFs conflicting |
| 3–4 | 4H opposed by 1D |
| 5–6 | 4H aligned, 1D neutral |
| 7–8 | 4H + 1D both aligned |
| 9–10 | 4H + 1D + 1W all aligned |

---

## Common Mistakes That Lower Scores

| Mistake | Typical score penalty |
|---------|----------------------|
| SL inside ATR noise (< 1× ATR) | −2 to −3 points |
| Entering after a 5%+ move without a retest | −2 to −3 points |
| R:R below 2:1 regardless of setup quality | −1 to −2 points |
| 1D trend opposing 4H trade direction | −2 points |
| RSI above 72 at Long entry (overbought) | −1 to −2 points |
| No defined TP2 or TP in thin air | −1 point |
| High funding rate against trade direction | −1 point |
| Known rulebook weakness triggered | −1 to −2 points |
| Pattern named incorrectly for structure shown | −1 point |
| Entering on Friday before weekend (thin liquidity) | −0.5 points |

---

## What Separates a 7 from a 9

The jump from 7 to 9 requires **three specific upgrades**:

1. **Multi-timeframe confirmation** — a 7 has 4H + 1D aligned; a 9 has 4H + 1D + 1W all pointing the same way
2. **Precision entry** — a 7 is "near a level"; a 9 is "price is touching the exact level right now with a confirmation candle"
3. **Volume** — a 7 has strong indicators; a 9 has those indicators AND a volume spike or surge at the exact entry point confirming institutional interest

A 10 additionally requires that all of the above happen simultaneously with favorable market conditions and a textbook pattern structure. It should feel rare and obvious at the same time.

---

*This guide is used internally by the Setup Scanner (`ai_scanner.py`), Call Analyzer (`ai_call.py`), and Hindsight Analysis (`ai_hindsight.py`) when prompting Claude to score setups.*
