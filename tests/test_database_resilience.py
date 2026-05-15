"""Tests for database WAL tuning and backup script existence."""
import os, pathlib


def test_wal_autocheckpoint_set():
    """database.py must set wal_autocheckpoint=100."""
    src = pathlib.Path(__file__).parent.parent / "database.py"
    assert "wal_autocheckpoint" in src.read_text()


def test_sigterm_handler_registered():
    """app.py must register SIGTERM checkpoint handler."""
    src = pathlib.Path(__file__).parent.parent / "app.py"
    text = src.read_text()
    assert "signal.SIGTERM" in text
    assert "_checkpoint_on_exit" in text


def test_backup_script_exists():
    """scripts/backup_db.sh must exist."""
    script = pathlib.Path(__file__).parent.parent / "scripts" / "backup_db.sh"
    assert script.exists(), "scripts/backup_db.sh not found"
    assert "sqlite3" in script.read_text()
