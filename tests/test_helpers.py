"""Tests for helpers.py — _ok(), _err(), strip_fence(), build_cached_messages()."""
import sys
import os
import contextlib
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_json(r):
    """Extract dict from either a real Flask Response or the _FakeResponse stub."""
    if hasattr(r, "get_json"):
        return r.get_json()
    return r


@contextlib.contextmanager
def _maybe_app_ctx():
    """
    If 'flask' in sys.modules is the real Flask (has Flask class), create an
    application context so jsonify() works. Also evict 'helpers' from sys.modules
    so it re-imports with the real Flask jsonify (not a previously cached stub).
    If it's the lightweight stub installed by conftest, no context is needed.
    """
    flask_mod = sys.modules.get("flask")
    is_real_flask = flask_mod is not None and hasattr(flask_mod, "Flask")
    if is_real_flask:
        # Evict cached helpers so it picks up real Flask's jsonify
        if "helpers" in sys.modules:
            del sys.modules["helpers"]
        import importlib
        import flask as real_flask
        app = real_flask.Flask(__name__)
        app.config["TESTING"] = True
        with app.app_context():
            yield
    else:
        yield


# ── _ok() ──────────────────────────────────────────────────────────────────────

def test_ok_wraps_data():
    with _maybe_app_ctx():
        from helpers import _ok
        r = _ok({"x": 1})
        d = _get_json(r)
        assert d["ok"] is True
        assert d["data"]["x"] == 1


def test_ok_with_list():
    with _maybe_app_ctx():
        from helpers import _ok
        r = _ok([1, 2, 3])
        d = _get_json(r)
        assert d["ok"] is True
        assert d["data"] == [1, 2, 3]


def test_ok_with_none():
    with _maybe_app_ctx():
        from helpers import _ok
        r = _ok(None)
        d = _get_json(r)
        assert d["ok"] is True
        assert d["data"] is None


def test_ok_with_nested_dict():
    with _maybe_app_ctx():
        from helpers import _ok
        r = _ok({"a": {"b": 2}})
        d = _get_json(r)
        assert d["ok"] is True
        assert d["data"]["a"]["b"] == 2


# ── _err() ─────────────────────────────────────────────────────────────────────

def test_err_returns_ok_false():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("bad input")
        resp = r[0] if isinstance(r, tuple) else r
        d = _get_json(resp)
        assert d["ok"] is False


def test_err_contains_message():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("bad input")
        resp = r[0] if isinstance(r, tuple) else r
        d = _get_json(resp)
        assert "bad input" in d["error"]


def test_err_default_code_400():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("bad input")
        assert isinstance(r, tuple)
        assert r[1] == 400


def test_err_custom_code():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("server error", 500)
        assert isinstance(r, tuple)
        assert r[1] == 500


def test_err_500_returns_generic_message():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("detailed internal error", 500)
        resp = r[0] if isinstance(r, tuple) else r
        d = _get_json(resp)
        # CWE-209: 5xx must not expose internal details
        assert "detailed internal error" not in d["error"]
        assert "Internal server error" in d["error"]


def test_err_truncates_long_message():
    with _maybe_app_ctx():
        from helpers import _err
        long_msg = "x" * 300
        r = _err(long_msg)
        resp = r[0] if isinstance(r, tuple) else r
        d = _get_json(resp)
        assert len(d["error"]) <= 200


def test_err_404_code():
    with _maybe_app_ctx():
        from helpers import _err
        r = _err("not found", 404)
        assert r[1] == 404


# ── strip_fence() ──────────────────────────────────────────────────────────────

def test_strip_fence_removes_markdown():
    from helpers import strip_fence
    assert strip_fence("```json\n{}\n```") == "{}"


def test_strip_fence_passthrough():
    from helpers import strip_fence
    assert strip_fence("plain text") == "plain text"


def test_strip_fence_with_language_tag():
    from helpers import strip_fence
    result = strip_fence("```python\nprint('hi')\n```")
    assert result == "print('hi')"


def test_strip_fence_multiline():
    from helpers import strip_fence
    raw = "```json\n{\n  \"a\": 1\n}\n```"
    result = strip_fence(raw)
    assert "```" not in result
    assert '"a": 1' in result


def test_strip_fence_no_closing_backtick():
    from helpers import strip_fence
    raw = "```json\n{}"
    result = strip_fence(raw)
    assert "```json" not in result


# ── build_cached_messages() ────────────────────────────────────────────────────

def test_build_cached_messages_basic():
    from helpers import build_cached_messages
    msgs = build_cached_messages(context="ctx", prompt="prompt")
    assert isinstance(msgs, list)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg["role"] == "user"
    assert isinstance(msg["content"], list)


def test_build_cached_messages_has_prompt():
    from helpers import build_cached_messages
    msgs = build_cached_messages(context="ctx", prompt="my question")
    content = msgs[0]["content"]
    texts = [c["text"] for c in content if c.get("type") == "text"]
    assert any("my question" in t for t in texts)


def test_build_cached_messages_with_image():
    from helpers import build_cached_messages
    msgs = build_cached_messages(context="ctx", prompt="p", image_b64="abc123")
    content = msgs[0]["content"]
    types = [c["type"] for c in content]
    assert "image" in types


def test_build_cached_messages_stable_prefix_cached():
    from helpers import build_cached_messages
    from constants import PROMPT_CACHE_MIN_CHARS
    long_prefix = "x" * PROMPT_CACHE_MIN_CHARS
    msgs = build_cached_messages(context="ctx", prompt="p", stable_prefix=long_prefix)
    content = msgs[0]["content"]
    cached = [c for c in content if c.get("cache_control")]
    assert len(cached) >= 1


def test_build_cached_messages_short_prefix_not_cached():
    from helpers import build_cached_messages
    msgs = build_cached_messages(context="ctx", prompt="p", stable_prefix="short")
    content = msgs[0]["content"]
    cached = [c for c in content if c.get("cache_control")]
    assert len(cached) == 0
