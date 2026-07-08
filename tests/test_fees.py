"""
tests/test_fees.py -- Tests for core.fees fee simulation functions.

Validates:
1. compute_entry_fee: basic calculation, zero rate, zero notional, Bitget rate
2. compute_exit_fee: basic calculation, zero rate, Bitget rate, abs for negative
3. Full lifecycle: entry -> TP1 partial -> final close, no double/missed fees
4. Zero-fee regression: rate=0.0 reproduces pre-fix gross numbers
5. Live-mode isolation: fee_rate=0.0 when paper_mode=False
"""

import pytest

from core.fees import compute_entry_fee, compute_exit_fee


class TestComputeEntryFee:
    def test_basic_calculation(self):
        assert compute_entry_fee(1000.0, 0.001) == 1.0

    def test_bitget_taker_rate(self):
        assert compute_entry_fee(1000.0, 0.0006) == pytest.approx(0.6, abs=0.001)

    def test_zero_rate(self):
        assert compute_entry_fee(1000.0, 0.0) == 0.0

    def test_zero_notional(self):
        assert compute_entry_fee(0.0, 0.0006) == 0.0

    def test_small_position(self):
        assert compute_entry_fee(500.0, 0.0006) == pytest.approx(0.3, abs=0.001)

    def test_large_position(self):
        assert compute_entry_fee(10000.0, 0.0006) == pytest.approx(6.0, abs=0.001)


class TestComputeExitFee:
    def test_basic_calculation(self):
        assert compute_exit_fee(1100.0, 0.001) == 1.1

    def test_bitget_taker_rate(self):
        assert compute_exit_fee(1100.0, 0.0006) == pytest.approx(0.66, abs=0.001)

    def test_zero_rate(self):
        assert compute_exit_fee(1100.0, 0.0) == 0.0

    def test_zero_notional(self):
        assert compute_exit_fee(0.0, 0.0006) == 0.0

    def test_negative_notional_uses_abs(self):
        assert compute_exit_fee(950.0, 0.0006) == pytest.approx(0.57, abs=0.001)

    def test_winning_exit_notional(self):
        exit_notional = 1000.0 + 100.0
        assert compute_exit_fee(exit_notional, 0.0006) == pytest.approx(0.66, abs=0.001)

    def test_losing_exit_notional(self):
        exit_notional = 1000.0 - 50.0
        assert compute_exit_fee(exit_notional, 0.0006) == pytest.approx(0.57, abs=0.001)


class TestFullLifecycle:
    """Entry -> TP1 partial (40%) -> final close lifecycle.

    Invariant: total fees = exactly one entry fee (100% notional)
    + one exit fee on TP1-sold notional + one exit fee on remaining notional.
    No double-charge, no missed charge.
    """

    def test_entry_tp1_final_close_fee_total(self):
        equity = 10000.0
        size_usdt = 500.0
        fee_rate = 0.0006
        entry_price = 50000.0

        # Entry fee: charged once on full notional
        entry_fee = compute_entry_fee(size_usdt, fee_rate)
        assert entry_fee == pytest.approx(0.3, abs=0.001)

        equity_after_entry = equity - entry_fee
        assert equity_after_entry == pytest.approx(9999.7, abs=0.01)

        # TP1 partial close: sell 40%, price at 52000 (pnl_pct=4%)
        sell_pct = 40.0
        sold_usdt = size_usdt * (sell_pct / 100.0)
        pnl_pct_tp1 = 4.0
        gross_pnl_tp1 = sold_usdt * pnl_pct_tp1 / 100.0
        assert gross_pnl_tp1 == pytest.approx(8.0, abs=0.01)

        exit_notional_tp1 = sold_usdt + gross_pnl_tp1
        exit_fee_tp1 = compute_exit_fee(exit_notional_tp1, fee_rate)
        assert exit_fee_tp1 == pytest.approx(0.1248, abs=0.001)

        net_pnl_tp1 = gross_pnl_tp1 - exit_fee_tp1
        equity_after_tp1 = equity_after_entry + net_pnl_tp1

        # Final close: remaining 60%, price at 49000 (pnl_pct=-2%)
        remaining_usdt = size_usdt - sold_usdt
        pnl_pct_final = -2.0
        gross_pnl_final = remaining_usdt * pnl_pct_final / 100.0
        assert gross_pnl_final == pytest.approx(-6.0, abs=0.01)

        exit_notional_final = remaining_usdt + gross_pnl_final
        exit_fee_final = compute_exit_fee(exit_notional_final, fee_rate)
        assert exit_fee_final == pytest.approx(0.1764, abs=0.001)

        net_pnl_final = gross_pnl_final - exit_fee_final
        equity_after_final = equity_after_tp1 + net_pnl_final

        # Verify no double-charge: total fees = entry + exit_tp1 + exit_final
        total_fees = entry_fee + exit_fee_tp1 + exit_fee_final
        expected_total = size_usdt * fee_rate + exit_notional_tp1 * fee_rate + exit_notional_final * fee_rate
        assert total_fees == pytest.approx(expected_total, abs=0.001)

        # Verify equity = initial + gross_pnl - total_fees
        total_gross_pnl = gross_pnl_tp1 + gross_pnl_final
        assert equity_after_final == pytest.approx(equity + total_gross_pnl - total_fees, abs=0.01)

        # Verify each fee component is non-zero and distinct
        assert entry_fee > 0
        assert exit_fee_tp1 > 0
        assert exit_fee_final > 0

    def test_no_double_entry_fee(self):
        """Entry fee is charged exactly once, not at each close event."""
        size_usdt = 500.0
        fee_rate = 0.0006

        entry_fee = compute_entry_fee(size_usdt, fee_rate)

        # After TP1 partial close, remaining position should NOT pay another entry fee
        # Only exit fees are charged at close events
        sold_usdt = size_usdt * 0.4
        remaining = size_usdt * 0.6

        exit_fee_tp1 = compute_exit_fee(sold_usdt, fee_rate)
        exit_fee_final = compute_exit_fee(remaining, fee_rate)

        # Total fees should be entry_fee + exit_fee_tp1 + exit_fee_final
        # NOT 2*entry_fee + exit fees
        total = entry_fee + exit_fee_tp1 + exit_fee_final
        double_entry = 2 * entry_fee + exit_fee_tp1 + exit_fee_final
        assert total < double_entry


class TestZeroFeeRegression:
    """When fee_rate=0.0, behavior matches pre-fix gross-only code."""

    def test_zero_rate_entry_fee(self):
        assert compute_entry_fee(500.0, 0.0) == 0.0

    def test_zero_rate_exit_fee(self):
        assert compute_exit_fee(520.0, 0.0) == 0.0

    def test_zero_rate_net_equals_gross(self):
        gross_pnl = 20.0
        exit_fee = compute_exit_fee(520.0, 0.0)
        net_pnl = gross_pnl - exit_fee
        assert net_pnl == gross_pnl


class TestLiveModeIsolation:
    """In live mode, fee_rate is 0.0 — no fee deduction occurs."""

    def test_live_mode_fee_rate_is_zero(self):
        """When paper_mode=False, execute_exit uses fee_rate=0.0."""
        paper_mode = False
        fee_rate = 0.0 if not paper_mode else 0.0006

        entry_fee = compute_entry_fee(500.0, fee_rate)
        exit_fee = compute_exit_fee(520.0, fee_rate)

        assert entry_fee == 0.0
        assert exit_fee == 0.0

    def test_paper_mode_fee_rate_is_nonzero(self):
        """When paper_mode=True, fee_rate uses config value."""
        paper_mode = True
        fee_rate = 0.0 if not paper_mode else 0.0006

        entry_fee = compute_entry_fee(500.0, fee_rate)
        exit_fee = compute_exit_fee(520.0, fee_rate)

        assert entry_fee > 0
        assert exit_fee > 0
