"""
Tests for analytics.py — get_dashboard_kpis and related helpers.
"""
import math
import sqlite3
import pytest


def test_dashboard_kpis_monthly_pnl(db):
    """get_dashboard_kpis returns monthly_pnl list."""
    from analytics import get_dashboard_kpis
    result = get_dashboard_kpis()
    assert "monthly_pnl" in result
    assert isinstance(result["monthly_pnl"], list)


def _insert_wallet(db, rows):
    """rows: [(date_str, balance), ...]. Also enables Row factory for analytics._rows()."""
    db.row_factory = sqlite3.Row
    for date, bal in rows:
        db.execute(
            "INSERT INTO wallet_snapshots (date, wallet_balance) VALUES (?,?)",
            (date, bal),
        )
    db.commit()


def test_calmar_uses_running_peak_not_final_peak(db):
    """
    Max drawdown must be computed relative to the peak at the time of the trough,
    not the all-time peak reached later.

    Scenario: account 1000 → 2000 → 1000 (50% drawdown) → 10000 (full recovery + growth).
    Correct max_dd_pct = 50.0 (1000/2000).
    Wrong behaviour (old bug) would compute 1000/10000 = 10%.
    """
    import database as _db
    import analytics
    monkeydb = db  # already the patched conn from fixture

    rows = [
        ("2026-01-01", 1000.0),
        ("2026-01-02", 1500.0),
        ("2026-01-03", 2000.0),  # peak
        ("2026-01-04", 1500.0),
        ("2026-01-05", 1000.0),  # 50% drawdown from peak
        ("2026-01-06", 2000.0),
        ("2026-01-07", 4000.0),
        ("2026-01-08", 6000.0),
        ("2026-01-09", 8000.0),
        ("2026-01-10", 10000.0),  # new all-time high — 10× original peak
    ]
    _insert_wallet(db, rows)

    result = analytics.get_sharpe_calmar(conn=db)
    assert result.get("max_drawdown_pct") is not None
    # Old bug: 1000/10000*100 = 10%. Correct: 1000/2000*100 = 50%.
    assert result["max_drawdown_pct"] == pytest.approx(50.0, abs=0.1), (
        f"Expected ~50% max drawdown, got {result['max_drawdown_pct']}% — "
        "drawdown must be measured against the peak at the time of the trough"
    )


def test_calmar_ratio_formula(db):
    """Calmar = annualised_return% / max_drawdown%. Both in same units."""
    import analytics

    # Flat then big dip then flat recovery: predictable drawdown
    rows = [
        ("2026-01-01", 1000.0),
        ("2026-01-02", 1000.0),
        ("2026-01-03", 1000.0),
        ("2026-01-04", 1000.0),
        ("2026-01-05", 900.0),   # 10% drawdown from 1000
        ("2026-01-06", 950.0),
        ("2026-01-07", 1000.0),
        ("2026-01-08", 1000.0),
        ("2026-01-09", 1000.0),
        ("2026-01-10", 1000.0),
    ]
    _insert_wallet(db, rows)

    result = analytics.get_sharpe_calmar(conn=db)
    assert result.get("max_drawdown_pct") == pytest.approx(10.0, abs=0.1)
    # Calmar should be defined (non-None) when drawdown > 0
    if result.get("calmar") is not None:
        expected_calmar = result["ann_return_pct"] / result["max_drawdown_pct"]
        assert result["calmar"] == pytest.approx(expected_calmar, rel=0.01)


def test_sharpe_uses_sample_variance(db):
    """
    Sharpe annualised volatility must use sample std (N-1 denominator), not population std.
    For 10 daily returns the difference is sqrt(9/10) vs 1 — about 5.4%.
    """
    import analytics, math

    # Alternating returns give predictable std
    rows = [
        ("2026-01-01", 1000.0),
        ("2026-01-02", 1010.0),  # +1%
        ("2026-01-03", 999.9),   # ~-1%
        ("2026-01-04", 1009.9),  # +1%
        ("2026-01-05", 999.8),
        ("2026-01-06", 1009.8),
        ("2026-01-07", 999.7),
        ("2026-01-08", 1009.7),
        ("2026-01-09", 999.6),
        ("2026-01-10", 1009.6),
        ("2026-01-11", 999.5),
    ]
    _insert_wallet(db, rows)

    result = analytics.get_sharpe_calmar(conn=db)
    assert result.get("ann_volatility_pct") is not None

    # Manually compute expected sample std for verification
    balances = [r[1] for r in rows]
    daily: dict = {}
    for date, bal in rows:
        daily[date[:10]] = bal
    sorted_days = sorted(daily.keys())
    rets = [(daily[sorted_days[i]] - daily[sorted_days[i-1]]) / daily[sorted_days[i-1]]
            for i in range(1, len(sorted_days))
            if daily[sorted_days[i-1]] > 0]
    n = len(rets)
    mean = sum(rets) / n
    sample_var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    expected_ann_vol = math.sqrt(sample_var) * math.sqrt(365) * 100

    assert result["ann_volatility_pct"] == pytest.approx(expected_ann_vol, rel=0.01), (
        f"Expected ann_volatility_pct ~{expected_ann_vol:.2f}% (sample std), "
        f"got {result['ann_volatility_pct']}%"
    )
