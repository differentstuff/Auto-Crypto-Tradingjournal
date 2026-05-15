"""
Tests for analytics.py — get_dashboard_kpis and related helpers.
"""
import pytest


def test_dashboard_kpis_monthly_pnl(db):
    """get_dashboard_kpis returns monthly_pnl list."""
    from analytics import get_dashboard_kpis
    result = get_dashboard_kpis()
    assert "monthly_pnl" in result
    assert isinstance(result["monthly_pnl"], list)
