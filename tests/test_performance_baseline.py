"""
Performance baseline tests — run against live Pi.
Skipped unless --host is provided.

Usage:
  python3 -m pytest tests/test_performance_baseline.py -v -s --host=192.168.1.21:8082
"""
import time
import pytest
import requests


@pytest.fixture
def host(request):
    h = request.config.getoption("--host", default=None)
    if not h:
        pytest.skip("--host not provided; skipping live performance tests")
    return h


def test_backtest_30d_under_10s(host):
    """30-day backtest must complete in under 10 seconds on Pi."""
    t0 = time.time()
    resp = requests.post(f"http://{host}/api/backtest/run",
                         json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 30},
                         timeout=30)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert elapsed < 10.0, f"30d backtest took {elapsed:.1f}s — baseline is <10s"


def test_backtest_180d_under_30s(host):
    """180-day backtest must complete in under 30 seconds on Pi."""
    t0 = time.time()
    resp = requests.post(f"http://{host}/api/backtest/run",
                         json={"symbol": "BTCUSDT", "timeframe": "4H", "days": 180},
                         timeout=60)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert elapsed < 30.0, f"180d backtest took {elapsed:.1f}s — baseline is <30s"


def test_scanner_status_under_200ms(host):
    """Scanner status (read-only dict) must respond in under 200ms."""
    t0 = time.time()
    resp = requests.get(f"http://{host}/api/scanner/status", timeout=5)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert elapsed < 0.2, f"Scanner status took {elapsed*1000:.0f}ms — baseline is <200ms"


def test_dashboard_kpis_under_500ms(host):
    """Dashboard KPIs must respond in under 500ms."""
    t0 = time.time()
    resp = requests.get(f"http://{host}/api/dashboard/kpis", timeout=10)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    assert elapsed < 0.5, f"Dashboard took {elapsed*1000:.0f}ms — baseline is <500ms"


def test_optimizer_start_under_2s(host):
    """Optimizer start must return job_id within 2 seconds (non-blocking)."""
    t0 = time.time()
    resp = requests.get(f"http://{host}/api/backtest/optimize?symbol=BTCUSDT&n_trials=5",
                        timeout=10)
    elapsed = time.time() - t0
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "job_id" in data["data"], "Response must include job_id"
    assert elapsed < 2.0, f"Optimizer start took {elapsed:.1f}s — expected <2s (non-blocking)"
