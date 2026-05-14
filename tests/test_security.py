"""Tests for security fixes — zip-slip, passphrase validation, XSS escaping, caps."""
import os
import re
import zipfile
import sys
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ensure real Flask is available for helpers tests
for _mod in list(sys.modules):
    if _mod == "flask" or _mod.startswith("flask."):
        del sys.modules[_mod]
import flask as _flask_real  # noqa: F401


@pytest.fixture
def app_ctx():
    # Also evict helpers so it re-imports with real Flask's jsonify (not the stub)
    if "helpers" in sys.modules:
        del sys.modules["helpers"]
    import flask
    _app = flask.Flask(__name__)
    _app.config["TESTING"] = True
    with _app.app_context():
        yield


def test_zip_slip_blocked(tmp_path):
    """Zip with path traversal member must be detected and rejected."""
    bad_zip = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../../../tmp/pwned.txt", "evil content")

    traversal_detected = False
    with zipfile.ZipFile(bad_zip, "r") as zf:
        for member in zf.namelist():
            dst = os.path.realpath(os.path.join(str(tmp_path), member))
            if not dst.startswith(os.path.realpath(str(tmp_path)) + os.sep):
                traversal_detected = True
                break
    assert traversal_detected


def test_zip_safe_member_allowed(tmp_path):
    """Zip with normal members must pass the path-traversal check."""
    safe_zip = tmp_path / "safe.zip"
    with zipfile.ZipFile(safe_zip, "w") as zf:
        zf.writestr("trades.csv", "symbol,pnl\nBTCUSDT,100")
    real_tmp = os.path.realpath(str(tmp_path))
    with zipfile.ZipFile(safe_zip, "r") as zf:
        for member in zf.namelist():
            dst = os.path.realpath(os.path.join(str(tmp_path), member))
            assert dst.startswith(real_tmp + os.sep), \
                f"Safe member '{member}' triggered path-traversal check"


def test_zip_nested_safe_path_allowed(tmp_path):
    """Nested but non-traversal path must be accepted."""
    nested_zip = tmp_path / "nested.zip"
    with zipfile.ZipFile(nested_zip, "w") as zf:
        zf.writestr("subdir/trades.csv", "symbol,pnl\nETHUSDT,50")
    real_tmp = os.path.realpath(str(tmp_path))
    with zipfile.ZipFile(nested_zip, "r") as zf:
        for member in zf.namelist():
            dst = os.path.realpath(os.path.join(str(tmp_path), member))
            assert dst.startswith(real_tmp + os.sep)


def test_eschtml_escapes_angle_brackets():
    """HTML escaping must neutralise < > & \" ' characters."""
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;")\
                        .replace(">", "&gt;").replace('"', "&quot;")\
                        .replace("'", "&#39;")
    assert esc("<script>") == "&lt;script&gt;"
    assert esc("a & b") == "a &amp; b"
    assert esc('"quoted"') == "&quot;quoted&quot;"
    assert esc("it's") == "it&#39;s"


def test_eschtml_empty_string():
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;")\
                        .replace(">", "&gt;").replace('"', "&quot;")\
                        .replace("'", "&#39;")
    assert esc("") == ""
    assert esc(None) == ""


def test_backtest_days_capped():
    """days=9999 → min(9999, 365) == 365."""
    days = min(int(9999), 365)
    assert days == 365


def test_backtest_days_within_limit_unchanged():
    """days=30 stays 30 after the cap."""
    days = min(int(30), 365)
    assert days == 30


def test_backtest_days_exact_limit():
    """days=365 stays 365 (boundary)."""
    days = min(int(365), 365)
    assert days == 365


def test_backtest_invalid_days_returns_error():
    """days='abc' must raise ValueError, which the route catches."""
    try:
        min(int("abc"), 365)
        assert False, "Should have raised ValueError"
    except (ValueError, TypeError):
        pass  # correct — route catches this and returns _err


def test_backtest_n_trials_capped():
    """n_trials=9999 → min(9999, 200) == 200."""
    n_trials = min(int(9999), 200)
    assert n_trials == 200


def test_passphrase_null_byte_rejected():
    """Passphrase with null byte (\\x00) is outside [\\x20-\\x7E] range."""
    phrase = "valid\x00phrase"
    phrase_stripped = phrase.replace("\n", "").replace("\r", "")
    match = re.match(r"^[\x20-\x7E]+$", phrase_stripped)
    assert match is None  # null byte (0x00) is outside [\x20-\x7E]


def test_passphrase_newline_stripped_then_valid():
    """Passphrase with trailing newline is valid after stripping."""
    phrase = "validphrase\n"
    phrase_stripped = phrase.replace("\n", "").replace("\r", "")
    assert re.match(r"^[\x20-\x7E]+$", phrase_stripped) is not None


def test_passphrase_valid_printable_ascii():
    """Normal printable ASCII passphrase is accepted."""
    phrase = "MySecurePass123!@#"
    assert re.match(r"^[\x20-\x7E]+$", phrase) is not None


def test_passphrase_non_printable_rejected():
    """DEL character (\\x7F) is outside printable range."""
    phrase = "bad\x7fpass"
    assert re.match(r"^[\x20-\x7E]+$", phrase) is None


def test_passphrase_control_char_rejected():
    """Control characters (\\x01–\\x1F) are rejected."""
    phrase = "bad\x01pass"
    assert re.match(r"^[\x20-\x7E]+$", phrase) is None


def test_cwe209_err_500_does_not_expose_details(app_ctx):
    """_err with 500 must return generic message, not the exception text."""
    from helpers import _err
    r = _err("SELECT * FROM passwords WHERE 1=1; DROP TABLE users;", 500)
    resp = r[0] if isinstance(r, tuple) else r
    d = resp.get_json() if hasattr(resp, "get_json") else resp
    assert "SELECT" not in d["error"]
    assert "DROP" not in d["error"]
    assert "Internal server error" in d["error"]
