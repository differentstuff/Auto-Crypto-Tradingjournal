import pytest
pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")


@pytest.fixture
def sample_df():
    n = 100
    np.random.seed(7)
    closes = np.linspace(100, 120, n) + np.random.randn(n) * 3
    return pd.DataFrame({
        "open":   closes - 1,
        "high":   closes + 3,
        "low":    closes - 3,
        "close":  closes,
        "volume": np.random.randint(1000, 5000, n).astype(float),
    })


def test_detect_sr_levels_returns_list(sample_df):
    from chart_sr import detect_sr_levels
    levels = detect_sr_levels(sample_df)
    assert isinstance(levels, list)
    for lvl in levels:
        assert "price" in lvl
        assert "type" in lvl
        assert lvl["type"] in ("support", "resistance")
        assert "strength" in lvl
        assert "touches" in lvl

def test_detect_sr_levels_insufficient_data():
    from chart_sr import detect_sr_levels
    tiny = pd.DataFrame({
        "open": [1.0]*5, "high": [1.1]*5, "low": [0.9]*5,
        "close": [1.0]*5, "volume": [100.0]*5,
    })
    assert detect_sr_levels(tiny) == []

def test_detect_sr_max_levels(sample_df):
    from chart_sr import detect_sr_levels
    levels = detect_sr_levels(sample_df, max_levels=3)
    assert len(levels) <= 3

def test_detect_sr_sorted_by_strength(sample_df):
    from chart_sr import detect_sr_levels
    levels = detect_sr_levels(sample_df)
    strengths = [l["strength"] for l in levels]
    assert strengths == sorted(strengths, reverse=True)

def test_nearest_levels():
    from chart_sr import nearest_levels
    sr = [
        {"price": 105.0, "type": "support",    "touches": 3, "strength": 1.0},
        {"price": 112.0, "type": "resistance", "touches": 2, "strength": 0.6},
    ]
    result = nearest_levels(108.0, sr)
    assert result["support"]    == 105.0
    assert result["resistance"] == 112.0
    assert result["support_dist_pct"] > 0
    assert result["resistance_dist_pct"] > 0

def test_nearest_levels_empty():
    from chart_sr import nearest_levels
    result = nearest_levels(100.0, [])
    assert result["support"] is None
    assert result["resistance"] is None
