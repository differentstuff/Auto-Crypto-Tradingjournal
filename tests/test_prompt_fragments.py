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
