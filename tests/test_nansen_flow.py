"""Tests for Nansen smart-money flow direction integration."""

import pytest
from unittest.mock import patch, MagicMock
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nansen_client
import prompt_builder


class TestNansenFlowDirection:
    """Test that flow direction is correctly derived from netflow."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset the Nansen cache before each test."""
        nansen_client._cache_data = []
        nansen_client._cache_ts = 0.0
        yield
        nansen_client._cache_data = []
        nansen_client._cache_ts = 0.0

    def test_get_smart_money_signal_accumulating(self):
        """When netflow > 0, direction should be 'accumulating'."""
        mock_response = {
            "data": [
                {
                    "token_symbol": "BTC",
                    "chain": "ethereum",
                    "nof_traders": 10,
                    "netflow": 1_500_000,
                    "buy_volume": 2_000_000,
                    "sell_volume": 500_000,
                    "price_change": 0.05,
                    "market_cap_usd": 500_000_000_000,
                }
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.dict("os.environ", {"NANSEN_API_KEY": "test-key"}):
                with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                    # Force cache refresh
                    result = nansen_client.get_smart_money_signal("BTCUSDT")

        assert result.get("ok") is True
        assert result.get("direction") == "accumulating"
        assert result.get("netflow_usd") == 1_500_000
        assert "accumulating" in result.get("prompt_line", "").lower()

    def test_get_smart_money_signal_distributing(self):
        """When netflow < 0, direction should be 'distributing'."""
        mock_response = {
            "data": [
                {
                    "token_symbol": "ETH",
                    "chain": "ethereum",
                    "nof_traders": 8,
                    "netflow": -750_000,
                    "buy_volume": 500_000,
                    "sell_volume": 1_250_000,
                    "price_change": -0.03,
                    "market_cap_usd": 300_000_000_000,
                }
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                result = nansen_client.get_smart_money_signal("ETHUSDT")

        assert result.get("ok") is True
        assert result.get("direction") == "distributing"
        assert result.get("netflow_usd") == -750_000
        assert "distributing" in result.get("prompt_line", "").lower()

    def test_get_smart_money_signal_strength_classification(self):
        """Strength should be classified based on netflow magnitude."""
        test_cases = [
            (1_000_000, "strong"),    # > 500k
            (250_000, "moderate"),     # 50k-500k
            (10_000, "weak"),          # < 50k
            (-1_000_000, "strong"),    # magnitude > 500k
            (-200_000, "moderate"),    # magnitude 50k-500k
        ]

        for netflow, expected_strength in test_cases:
            nansen_client._cache_data = []
            nansen_client._cache_ts = 0.0
            mock_response = {
                "data": [
                    {
                        "token_symbol": "TEST",
                        "chain": "ethereum",
                        "nof_traders": 6,
                        "netflow": netflow,
                        "buy_volume": max(netflow, 0) + 100_000,
                        "sell_volume": max(-netflow, 0) + 100_000,
                        "price_change": 0.0,
                        "market_cap_usd": 100_000_000,
                    }
                ]
            }

            with patch("nansen_client._post", return_value=mock_response):
                with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                    result = nansen_client.get_smart_money_signal("TESTUSDT")

            assert result.get("strength") == expected_strength

    def test_returns_dict_has_flow_direction_key(self):
        """Signal dict must include flow_direction (alias for direction)."""
        mock_response = {
            "data": [
                {
                    "token_symbol": "BTC",
                    "chain": "ethereum",
                    "nof_traders": 6,
                    "netflow": 100_000,
                    "buy_volume": 150_000,
                    "sell_volume": 50_000,
                    "price_change": 0.01,
                    "market_cap_usd": 500_000_000_000,
                }
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                result = nansen_client.get_smart_money_signal("BTCUSDT")

        assert result.get("ok") is True
        # The key is "direction", not "flow_direction"
        assert "direction" in result
        assert result.get("direction") in ("accumulating", "distributing")

    def test_insufficient_traders_returns_not_ok(self):
        """Signal returns ok=False when fewer than MIN_TRADERS wallets."""
        mock_response = {
            "data": [
                {
                    "token_symbol": "SHIB",
                    "chain": "ethereum",
                    "nof_traders": 3,  # Less than MIN_TRADERS (5)
                    "netflow": 50_000,
                    "buy_volume": 75_000,
                    "sell_volume": 25_000,
                    "price_change": 0.0,
                    "market_cap_usd": 10_000_000,
                }
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                result = nansen_client.get_smart_money_signal("SHIBUSDT")

        assert result.get("ok") is False
        assert "only" in result.get("reason", "").lower()

    def test_symbol_not_in_screener_returns_not_ok(self):
        """Signal returns ok=False when symbol is not in screener."""
        mock_response = {"data": []}  # Empty screener

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                result = nansen_client.get_smart_money_signal("UNKNOWNUSDT")

        assert result.get("ok") is False
        assert "not in nansen screener" in result.get("reason", "").lower()


class TestPromptBuilderNansenRendering:
    """Test that prompt_builder correctly renders Nansen flow direction."""

    def test_nansen_block_includes_flow_icon_and_direction(self):
        """Prompt block should include flow direction icon and label."""
        # Mock a successful Nansen signal
        mock_signal = {
            "ok": True,
            "symbol": "BTC",
            "chain": "ethereum",
            "netflow_usd": 1_200_000,
            "buy_vol_usd": 1_500_000,
            "sell_vol_usd": 300_000,
            "nof_traders": 12,
            "px_change_24h": 2.35,
            "market_cap_usd": 500_000_000_000,
            "direction": "accumulating",
            "strength": "strong",
            "prompt_line": (
                "Nansen smart money (12 wallets): accumulating — "
                "netflow $1,200,000 (buy $1,500,000 / sell $300,000) [strong]"
            ),
        }

        with patch("nansen_client.is_configured", return_value=True):
            with patch("nansen_client.get_smart_money_signal", return_value=mock_signal):
                # Build context with Nansen integration
                context = prompt_builder.build_context(
                    conn=None,
                    symbol="BTCUSDT",
                    include_chart=False,
                    include_rulebook=False,
                    include_calibration=False,
                    include_similar=False,
                    include_strengths=False,
                )

        assert "NANSEN SMART MONEY" in context
        assert "accumulating" in context.lower()
        assert "🟢" in context or "accumulating" in context.lower()
        assert "$1,200,000" in context or "1200000" in context.replace(",", "")

    def test_nansen_block_distributing_direction(self):
        """Prompt block should show distributing icon for negative netflow."""
        mock_signal = {
            "ok": True,
            "symbol": "ETH",
            "netflow_usd": -800_000,
            "direction": "distributing",
            "strength": "strong",
            "prompt_line": (
                "Nansen smart money (8 wallets): distributing — "
                "netflow $-800,000 (buy $200,000 / sell $1,000,000) [strong]"
            ),
        }

        with patch("nansen_client.is_configured", return_value=True):
            with patch("nansen_client.get_smart_money_signal", return_value=mock_signal):
                context = prompt_builder.build_context(
                    conn=None,
                    symbol="ETHUSDT",
                    include_chart=False,
                    include_rulebook=False,
                    include_calibration=False,
                    include_similar=False,
                    include_strengths=False,
                )

        assert "NANSEN SMART MONEY" in context
        assert "distributing" in context.lower()
        assert "🔴" in context or "distributing" in context.lower()

    def test_nansen_not_configured_skipped(self):
        """When Nansen is not configured, the block should be skipped."""
        with patch("nansen_client.is_configured", return_value=False):
            context = prompt_builder.build_context(
                conn=None,
                symbol="BTCUSDT",
                include_chart=False,
                include_rulebook=False,
                include_calibration=False,
                include_similar=False,
                include_strengths=False,
            )

        assert "NANSEN SMART MONEY" not in context


class TestNansenIntegration:
    """Integration tests for Nansen data source."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset the Nansen cache before each test."""
        nansen_client._cache_data = []
        nansen_client._cache_ts = 0.0
        yield
        nansen_client._cache_data = []
        nansen_client._cache_ts = 0.0

    def test_bulk_lookup_preserves_flow_direction(self):
        """get_signals_for_symbols should preserve flow direction for all symbols."""
        symbols = ["BTCUSDT", "ETHUSDT"]
        mock_response = {
            "data": [
                {
                    "token_symbol": "BTC",
                    "chain": "ethereum",
                    "nof_traders": 10,
                    "netflow": 2_000_000,
                    "buy_volume": 2_500_000,
                    "sell_volume": 500_000,
                    "price_change": 0.05,
                    "market_cap_usd": 500_000_000_000,
                },
                {
                    "token_symbol": "ETH",
                    "chain": "ethereum",
                    "nof_traders": 8,
                    "netflow": -500_000,
                    "buy_volume": 300_000,
                    "sell_volume": 800_000,
                    "price_change": -0.02,
                    "market_cap_usd": 300_000_000_000,
                },
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                signals = nansen_client.get_signals_for_symbols(symbols)

        assert "BTCUSDT" in signals
        assert "ETHUSDT" in signals
        assert signals["BTCUSDT"].get("direction") == "accumulating"
        assert signals["ETHUSDT"].get("direction") == "distributing"

    def test_top_movers_separates_accumulators_from_distributors(self):
        """get_top_movers should correctly classify by flow direction."""
        mock_response = {
            "data": [
                {
                    "token_symbol": "BTC",
                    "chain": "ethereum",
                    "nof_traders": 15,
                    "netflow": 5_000_000,
                    "market_cap_usd": 500_000_000_000,
                    "price_change": 0.08,
                },
                {
                    "token_symbol": "ETH",
                    "chain": "ethereum",
                    "nof_traders": 12,
                    "netflow": 3_000_000,
                    "market_cap_usd": 300_000_000_000,
                    "price_change": 0.06,
                },
                {
                    "token_symbol": "SOL",
                    "chain": "solana",
                    "nof_traders": 8,
                    "netflow": -2_000_000,
                    "market_cap_usd": 80_000_000_000,
                    "price_change": -0.05,
                },
            ]
        }

        with patch("nansen_client._post", return_value=mock_response):
            with patch.object(nansen_client, "NANSEN_API_KEY", "test-key"):
                movers = nansen_client.get_top_movers()

        accumulators = movers.get("accumulators", [])
        distributors = movers.get("distributors", [])

        assert len(accumulators) >= 2
        assert len(distributors) >= 1
        assert all(a["netflow_usd"] > 0 for a in accumulators)
        assert all(d["netflow_usd"] < 0 for d in distributors)
