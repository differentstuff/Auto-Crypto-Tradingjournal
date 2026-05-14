"""Tests for kill zone detection and urgency annotation in ai_scanner."""
import sys, os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_london_killzone_start_is_active():
    """07:00 UTC is inside London kill zone (inclusive start)."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=7) is True


def test_london_killzone_middle_is_active():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=8) is True


def test_london_killzone_end_is_inactive():
    """10:00 UTC is exclusive end of London window → outside."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=10) is False


def test_ny_am_killzone_is_active():
    """13:00 UTC is inside NY AM kill zone."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=13) is True


def test_ny_am_killzone_end_is_inactive():
    """15:00 UTC is exclusive end of NY AM window → outside."""
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=15) is False


def test_outside_both_killzones_morning():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=5) is False


def test_outside_both_killzones_evening():
    from ai_scanner import _is_in_kill_zone
    assert _is_in_kill_zone(utc_hour=20) is False


def test_annotate_outside_killzone_appends_warning():
    """Result outside kill zone gets warning appended to urgency field."""
    from ai_scanner import _annotate_kill_zone
    result = {"urgency": "Now", "setup_score": 8}
    annotated = _annotate_kill_zone(result, utc_hour=5)
    assert "⚠ Outside kill zone" in annotated["urgency"]
    assert "Now" in annotated["urgency"]


def test_annotate_inside_killzone_no_change():
    """Result inside kill zone must not be modified."""
    from ai_scanner import _annotate_kill_zone
    result = {"urgency": "Now", "setup_score": 8}
    annotated = _annotate_kill_zone(result, utc_hour=8)
    assert annotated["urgency"] == "Now"


def test_annotate_missing_urgency_field():
    """Result with no urgency key gets urgency set to warning string."""
    from ai_scanner import _annotate_kill_zone
    result = {"setup_score": 7}
    annotated = _annotate_kill_zone(result, utc_hour=20)
    assert annotated["urgency"] == "⚠ Outside kill zone"


def test_annotate_inside_killzone_missing_urgency_no_change():
    """Inside kill zone: missing urgency field stays missing (no spurious key added)."""
    from ai_scanner import _annotate_kill_zone
    result = {"setup_score": 7}
    annotated = _annotate_kill_zone(result, utc_hour=9)
    assert "urgency" not in annotated
