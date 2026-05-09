def test_models_defined():
    from constants import MODEL, FAST_MODEL, ANTHROPIC_API_KEY
    assert MODEL == "claude-sonnet-4-6"
    assert "haiku" in FAST_MODEL.lower()
    assert isinstance(ANTHROPIC_API_KEY, str)

def test_cache_ttls_positive():
    from constants import CHART_CACHE_TTL, SCANNER_CACHE_TTL, MARKET_CACHE_TTL, NANSEN_CACHE_TTL
    assert CHART_CACHE_TTL > 0
    assert SCANNER_CACHE_TTL > 0
    assert MARKET_CACHE_TTL > 0
    assert NANSEN_CACHE_TTL > 0

def test_scanner_thresholds():
    from constants import SCANNER_MIN_SCORE, SCANNER_FULL_DETAIL_TOP_N, SCANNER_MAX_WORKERS
    assert 1 <= SCANNER_MIN_SCORE <= 10
    assert SCANNER_FULL_DETAIL_TOP_N >= 1
    assert 1 <= SCANNER_MAX_WORKERS <= 8
