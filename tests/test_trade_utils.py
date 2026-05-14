"""Tests for trade_utils.py — normalize_symbol(), normalize_direction()."""
import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub chart_context before importing trade_utils (avoids real Bitget calls)
if "chart_context" not in sys.modules:
    import types
    _cc = types.ModuleType("chart_context")
    _cc.get_chart_context = MagicMock(return_value={})
    _cc.get_binance_price = MagicMock(return_value=None)
    sys.modules["chart_context"] = _cc


class TestNormalizeSymbol:
    def test_bare_base_passthrough(self):
        # normalize_symbol only strips separators; it does NOT append USDT
        from trade_utils import normalize_symbol
        assert normalize_symbol("BTC") == "BTC"

    def test_already_has_usdt_passthrough(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("BTCUSDT") == "BTCUSDT"

    def test_slash_removed(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("BTC/USDT") == "BTCUSDT"

    def test_hyphen_removed(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("BTC-USDT") == "BTCUSDT"

    def test_underscore_removed(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("BTC_USDT") == "BTCUSDT"

    def test_lowercase_uppercased(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("btcusdt") == "BTCUSDT"

    def test_mixed_case(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("BtcUSDT") == "BTCUSDT"

    def test_eth_passthrough(self):
        from trade_utils import normalize_symbol
        assert normalize_symbol("ETHUSDT") == "ETHUSDT"

    def test_empty_string(self):
        from trade_utils import normalize_symbol
        result = normalize_symbol("")
        # Should return an empty string without crashing
        assert result == ""

    def test_none_input(self):
        from trade_utils import normalize_symbol
        # None → treated as empty string via (s or '').upper()...
        result = normalize_symbol(None)
        assert isinstance(result, str)


class TestNormalizeDirection:
    def test_long_lowercase(self):
        from trade_utils import normalize_direction
        result = normalize_direction("long")
        assert result == "Long"

    def test_buy(self):
        from trade_utils import normalize_direction
        result = normalize_direction("buy")
        assert result == "Long"

    def test_open_long(self):
        from trade_utils import normalize_direction
        result = normalize_direction("open_long")
        assert result == "Long"

    def test_short_uppercase(self):
        from trade_utils import normalize_direction
        result = normalize_direction("SHORT")
        assert result == "Short"

    def test_sell(self):
        from trade_utils import normalize_direction
        result = normalize_direction("sell")
        assert result == "Short"

    def test_open_short(self):
        from trade_utils import normalize_direction
        result = normalize_direction("open_short")
        assert result == "Short"

    def test_long_mixed_case(self):
        from trade_utils import normalize_direction
        # Input is lowercased before matching
        result = normalize_direction("Long")
        assert result == "Long"

    def test_unknown_passthrough(self):
        from trade_utils import normalize_direction
        result = normalize_direction("sideways")
        assert result == "sideways"  # unknown values are passed through unchanged

    def test_empty_string(self):
        from trade_utils import normalize_direction
        result = normalize_direction("")
        assert isinstance(result, str)

    def test_none_input(self):
        from trade_utils import normalize_direction
        # None doesn't match any known direction; the function returns the original input
        result = normalize_direction(None)
        # Just verify it doesn't crash (it returns None since None is not in known values)
        assert result is None or isinstance(result, str)
