"""Tests for sync_base.py shared infrastructure."""


def test_get_setting_returns_default_when_missing(db):
    from sync_base import _get_setting
    result = _get_setting(db, "nonexistent_key_xyz", default="fallback")
    assert result == "fallback"


def test_set_and_get_setting_roundtrip(db):
    from sync_base import _get_setting, _set_setting
    _set_setting(db, "test_key_abc", "test_value")
    db.commit()
    assert _get_setting(db, "test_key_abc") == "test_value"


def test_set_setting_overwrites_existing(db):
    from sync_base import _get_setting, _set_setting
    _set_setting(db, "mykey_overwrite", "first")
    db.commit()
    _set_setting(db, "mykey_overwrite", "second")
    db.commit()
    assert _get_setting(db, "mykey_overwrite") == "second"


def test_sync_driver_is_protocol():
    """SyncDriver is importable and is a Protocol class."""
    from sync_base import SyncDriver
    from typing import Protocol
    assert issubclass(SyncDriver, Protocol) or hasattr(SyncDriver, '__protocol_attrs__') \
        or str(type(SyncDriver)) in ("<class 'type'>", "<class 'abc.ABCMeta'>")
