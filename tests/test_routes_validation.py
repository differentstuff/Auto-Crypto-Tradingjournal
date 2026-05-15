"""Tests for API input validation."""
import json
import pytest


def test_limits_bulk_update_rejects_invalid_status(client):
    """Invalid status value should be rejected."""
    resp = client.post('/api/limits/bulk-update',
                       data=json.dumps({"ids": [999], "status": "hacked"}),
                       content_type='application/json')
    data = resp.get_json()
    assert data.get("ok") is False
    assert "status" in data.get("error", "").lower()


def test_limits_bulk_update_accepts_waiting_status(client):
    """'waiting' is a valid status and should not fail on validation."""
    resp = client.post('/api/limits/bulk-update',
                       data=json.dumps({"ids": [], "status": "waiting"}),
                       content_type='application/json')
    data = resp.get_json()
    # ids=[] gives "ids list required" error, NOT a status error
    assert "invalid" not in str(data.get("error", "")).lower()
    assert "status" not in str(data.get("error", "")).lower()
