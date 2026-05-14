def test_scoring_scale_covers_range():
    from prompt_fragments import SCORING_SCALE
    assert "5" in SCORING_SCALE and "10" in SCORING_SCALE
    assert "Moderate" in SCORING_SCALE
    assert "Perfect" in SCORING_SCALE

def test_level_proximity_has_atr():
    from prompt_fragments import LEVEL_PROXIMITY_RULES
    assert "ATR" in LEVEL_PROXIMITY_RULES
    assert "R:R" in LEVEL_PROXIMITY_RULES

def test_market_context_has_funding():
    from prompt_fragments import MARKET_CONTEXT_RULES
    assert "Funding" in MARKET_CONTEXT_RULES
    assert "Fear" in MARKET_CONTEXT_RULES

def test_no_duplicate_in_ai_scanner():
    with open("ai_scanner.py") as f:
        src = f.read()
    assert "SCORING SCALE:" not in src, "Scoring scale still hardcoded in ai_scanner.py"

def test_no_duplicate_in_ai_call():
    with open("ai_call.py") as f:
        src = f.read()
    # The old inline block should be replaced by the fragment reference
    assert "Level proximity definitions" not in src, "Level proximity still hardcoded in ai_call.py"

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
