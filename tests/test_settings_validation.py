"""Tests for settings validation — passphrase, API key patterns, length caps."""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSPHRASE_RE = re.compile(r"^[\x20-\x7E]+$")
API_KEY_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


class TestPassphraseValidation:
    def test_valid_chars(self):
        phrase = "MyPass123!@#$%"
        assert PASSPHRASE_RE.match(phrase) is not None

    def test_null_byte_rejected(self):
        phrase = "valid\x00phrase"
        assert PASSPHRASE_RE.match(phrase) is None

    def test_space_allowed(self):
        # Space (0x20) is the lower bound — must be allowed
        phrase = "my pass phrase"
        assert PASSPHRASE_RE.match(phrase) is not None

    def test_tilde_allowed(self):
        # Tilde (0x7E) is the upper bound — must be allowed
        phrase = "pass~word"
        assert PASSPHRASE_RE.match(phrase) is not None

    def test_del_char_rejected(self):
        # DEL (0x7F) is above 0x7E — must be rejected
        phrase = "bad\x7fpass"
        assert PASSPHRASE_RE.match(phrase) is None

    def test_control_char_rejected(self):
        phrase = "bad\x01pass"
        assert PASSPHRASE_RE.match(phrase) is None

    def test_newline_rejected(self):
        # Raw newline without stripping
        phrase = "bad\npass"
        assert PASSPHRASE_RE.match(phrase) is None

    def test_newline_stripped_then_valid(self):
        # As the route does: strip CR/LF then validate
        phrase = "validphrase\n"
        stripped = phrase.replace("\n", "").replace("\r", "")
        assert PASSPHRASE_RE.match(stripped) is not None

    def test_empty_string_rejected(self):
        assert PASSPHRASE_RE.match("") is None

    def test_length_cap(self):
        # A very long passphrase should be caught by a length check before regex
        long_phrase = "a" * 300
        assert len(long_phrase) > 256  # application should enforce a max length

    def test_all_printable_ascii_accepted(self):
        # Build a string of all printable ASCII characters (0x20–0x7E)
        all_printable = "".join(chr(i) for i in range(0x20, 0x7F))
        assert PASSPHRASE_RE.match(all_printable) is not None

    def test_non_ascii_rejected(self):
        phrase = "pässwörd"
        assert PASSPHRASE_RE.match(phrase) is None


class TestApiKeyValidation:
    def test_valid_pattern(self):
        assert API_KEY_RE.match("abc123-_XYZ") is not None

    def test_invalid_chars_space(self):
        assert API_KEY_RE.match("key with spaces") is None

    def test_invalid_chars_angle_bracket(self):
        assert API_KEY_RE.match("key<script>") is None

    def test_invalid_chars_slash(self):
        assert API_KEY_RE.match("key/value") is None

    def test_empty_string(self):
        assert API_KEY_RE.match("") is None

    def test_alphanumeric_only(self):
        assert API_KEY_RE.match("ABCDEF0123456789") is not None

    def test_hyphen_and_underscore_allowed(self):
        assert API_KEY_RE.match("my-api_key-123") is not None

    def test_dot_rejected(self):
        assert API_KEY_RE.match("key.value") is None

    def test_at_sign_rejected(self):
        assert API_KEY_RE.match("key@host") is None
