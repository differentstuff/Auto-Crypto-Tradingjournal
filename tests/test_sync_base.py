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


def test_sync_state_try_start_acquires():
    from sync_base import SyncState
    s = SyncState()
    assert s.try_start() is True
    assert s.snapshot()["running"] is True


def test_sync_state_try_start_rejects_double():
    from sync_base import SyncState
    s = SyncState()
    s.try_start()
    assert s.try_start() is False


def test_sync_state_finish_clears_running():
    from sync_base import SyncState
    s = SyncState()
    s.try_start()
    s.finish(result={"ok": True})
    snap = s.snapshot()
    assert snap["running"] is False
    assert snap["last_result"] == {"ok": True}


def test_auto_close_calls_importable():
    """auto_close_calls and retroactive_close_calls must be importable from sync_base."""
    from sync_base import auto_close_calls, retroactive_close_calls
    assert callable(auto_close_calls)
    assert callable(retroactive_close_calls)


def test_blofin_sync_no_bitget_import():
    """blofin_sync must not import from bitget_sync (cross-import eliminated)."""
    import ast, pathlib
    src = (pathlib.Path(__file__).parent.parent / "blofin_sync.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module == "bitget_sync":
                raise AssertionError("blofin_sync still imports from bitget_sync")
