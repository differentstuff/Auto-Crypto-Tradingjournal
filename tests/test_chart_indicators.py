import pytest
pd  = pytest.importorskip("pandas")
np  = pytest.importorskip("numpy")


@pytest.fixture
def sample_df():
    n = 100
    np.random.seed(42)
    closes = np.linspace(100, 120, n) + np.random.randn(n) * 2
    return pd.DataFrame({
        "open":   closes - 1,
        "high":   closes + 2,
        "low":    closes - 2,
        "close":  closes,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    })


def test_compute_rsi_bounds(sample_df):
    from chart_indicators import compute_rsi
    result = compute_rsi(sample_df)
    assert "value" in result
    assert "level" in result
    assert 0 <= result["value"] <= 100
    assert result["level"] in ("overbought", "oversold", "neutral")


def test_compute_rsi_insufficient_data():
    from chart_indicators import compute_rsi
    tiny = pd.DataFrame({
        "close": [100.0, 101.0], "open": [99.0, 100.0],
        "high": [102.0, 103.0], "low": [98.0, 99.0], "volume": [1000.0, 1000.0],
    })
    result = compute_rsi(tiny)
    assert result["value"] == 50.0
    assert result["level"] == "neutral"


def test_compute_ema_alignment(sample_df):
    from chart_indicators import compute_ema_alignment
    result = compute_ema_alignment(sample_df)
    assert result["alignment"] in ("bullish", "bearish", "mixed-bullish", "mixed-bearish", "neutral")
    assert "ema20" in result and "ema50" in result and "ema200" in result


def test_compute_ema_alignment_values_are_floats(sample_df):
    from chart_indicators import compute_ema_alignment
    result = compute_ema_alignment(sample_df)
    assert isinstance(result["ema20"], float)
    assert isinstance(result["ema50"], float)
    assert isinstance(result["ema200"], float)


def test_compute_macd_keys(sample_df):
    from chart_indicators import compute_macd
    result = compute_macd(sample_df)
    assert "macd" in result and "signal" in result and "histogram" in result
    assert result["bias"] in ("bullish", "bearish")


def test_compute_adx_keys(sample_df):
    from chart_indicators import compute_adx
    result = compute_adx(sample_df)
    assert "value" in result and "trend_strength" in result
    assert result["trend_strength"] in ("strong", "trending", "weak")
    assert 0 <= result["value"] <= 100


def test_compute_prompt_text_is_compact(sample_df):
    from chart_indicators import compute_prompt_text
    text = compute_prompt_text(sample_df, sr_levels=[105.0])
    assert isinstance(text, str)
    assert len(text) < 250
    assert any(k in text.upper() for k in ["RSI", "EMA", "MACD", "ADX"])


def test_compute_prompt_text_no_sr(sample_df):
    from chart_indicators import compute_prompt_text
    text = compute_prompt_text(sample_df, sr_levels=[])
    assert isinstance(text, str) and len(text) < 250


def test_compute_prompt_text_insufficient_data():
    from chart_indicators import compute_prompt_text
    tiny = pd.DataFrame({
        "close": [100.0]*5, "open": [99.0]*5, "high": [102.0]*5,
        "low": [98.0]*5, "volume": [1000.0]*5,
    })
    text = compute_prompt_text(tiny, sr_levels=[])
    assert isinstance(text, str)
