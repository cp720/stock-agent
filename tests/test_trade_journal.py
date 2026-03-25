"""
tests/test_trade_journal.py

Tests for trade_journal.py using an in-memory SQLite database (via the
in_memory_db fixture in conftest.py).  The on-disk data/trade_journal.db
is never touched by these tests.

Coverage:
  - close_oldest_position() — FIFO close logic, partial vs full, P&L math
  - _calculate_max_drawdown() — peak-to-trough calculation
  - _calculate_sharpe_ratio() — annualized Sharpe formula
"""
import math
import numpy as np
import pytest
from datetime import datetime, timedelta

from trade_journal import (
    TradeDecision,
    OpenPosition,
    EquitySnapshot,
    close_oldest_position,
    _calculate_max_drawdown,
    _calculate_sharpe_ratio,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_decision(ticker="AAPL", action="BUY", quantity=100,
                   execution_status="executed", equity=100_000.0):
    """Insert and return a minimal TradeDecision row."""
    return TradeDecision.create(
        ticker=ticker,
        action=action,
        quantity=quantity,
        execution_status=execution_status,
        equity=equity,
    )


def _make_open_position(ticker, entry_qty, entry_price,
                        entry_date=None, decision=None):
    """Insert and return an OpenPosition row."""
    if entry_date is None:
        entry_date = datetime(2025, 1, 1, 9, 30, 0)
    d = decision or _make_decision(ticker=ticker)
    return OpenPosition.create(
        ticker=ticker,
        status="open",
        entry_date=entry_date,
        entry_price=entry_price,
        entry_qty=entry_qty,
        entry_decision=d,
    )


# ===========================================================================
# close_oldest_position() — FIFO logic
# ===========================================================================

class TestCloseOldestPosition:

    def test_full_close_marks_position_closed(self, in_memory_db):
        """Selling exactly the open quantity closes the position."""
        sell_decision = _make_decision(action="SELL")
        pos = _make_open_position("AAPL", entry_qty=100, entry_price=100.0)

        close_oldest_position("AAPL", exit_price=110.0, sell_qty=100,
                               exit_decision=sell_decision)

        pos_updated = OpenPosition.get_by_id(pos.id)
        assert pos_updated.status == "closed"
        assert pos_updated.exit_qty == 100

    def test_full_close_realized_pnl_gain(self, in_memory_db):
        """P&L = (exit - entry) × qty for a winning trade."""
        sell_d = _make_decision(action="SELL")
        pos = _make_open_position("NVDA", entry_qty=50, entry_price=200.0)

        close_oldest_position("NVDA", exit_price=220.0, sell_qty=50,
                               exit_decision=sell_d)

        pos_updated = OpenPosition.get_by_id(pos.id)
        expected_pnl = (220.0 - 200.0) * 50   # = 1000.0
        assert math.isclose(pos_updated.realized_pnl, expected_pnl, rel_tol=1e-6)

    def test_full_close_realized_pnl_loss(self, in_memory_db):
        """P&L is negative when exit price < entry price."""
        sell_d = _make_decision(action="SELL")
        pos = _make_open_position("TSLA", entry_qty=10, entry_price=300.0)

        close_oldest_position("TSLA", exit_price=270.0, sell_qty=10,
                               exit_decision=sell_d)

        pos_updated = OpenPosition.get_by_id(pos.id)
        expected_pnl = (270.0 - 300.0) * 10   # = -300.0
        assert math.isclose(pos_updated.realized_pnl, expected_pnl, rel_tol=1e-6)

    def test_pnl_pct_formula(self, in_memory_db):
        """realized_pnl_pct = ((exit - entry) / entry) * 100."""
        sell_d = _make_decision(action="SELL")
        _make_open_position("MSFT", entry_qty=20, entry_price=400.0)

        close_oldest_position("MSFT", exit_price=440.0, sell_qty=20,
                               exit_decision=sell_d)

        pos_updated = OpenPosition.get(
            (OpenPosition.ticker == "MSFT") & (OpenPosition.status == "closed")
        )
        expected_pct = ((440.0 - 400.0) / 400.0) * 100  # = 10.0
        assert math.isclose(pos_updated.realized_pnl_pct, expected_pct, rel_tol=1e-6)

    def test_holding_days_calculated(self, in_memory_db):
        """holding_days = exit_date.date() - entry_date.date() in days."""
        sell_d = _make_decision(action="SELL")
        entry_dt = datetime(2025, 1, 1, 9, 30)
        pos = _make_open_position("ANET", entry_qty=5, entry_price=100.0,
                                  entry_date=entry_dt)

        close_oldest_position("ANET", exit_price=105.0, sell_qty=5,
                               exit_decision=sell_d)

        pos_updated = OpenPosition.get_by_id(pos.id)
        # Exit date is ~now; entry was 2025-01-01 → many days, but must be >= 0
        assert pos_updated.holding_days >= 0

    def test_partial_close_creates_remainder_position(self, in_memory_db):
        """Selling 50 of 100 shares → original closed, new open position for 50."""
        sell_d = _make_decision(action="SELL")
        pos = _make_open_position("AAPL", entry_qty=100, entry_price=150.0)

        close_oldest_position("AAPL", exit_price=160.0, sell_qty=50,
                               exit_decision=sell_d)

        closed = OpenPosition.get_by_id(pos.id)
        assert closed.status == "closed"
        assert closed.exit_qty == 50

        remainder = (OpenPosition
                     .select()
                     .where(
                         (OpenPosition.ticker == "AAPL") &
                         (OpenPosition.status == "open")
                     )
                     .first())
        assert remainder is not None
        assert remainder.entry_qty == 50
        assert math.isclose(remainder.entry_price, 150.0)

    def test_fifo_closes_oldest_position_first(self, in_memory_db):
        """Two open positions — the older one (by entry_date) is closed first."""
        sell_d = _make_decision(action="SELL")
        older_entry = datetime(2025, 1, 1)
        newer_entry = datetime(2025, 2, 1)
        older = _make_open_position("GOOG", entry_qty=10, entry_price=100.0,
                                    entry_date=older_entry)
        newer = _make_open_position("GOOG", entry_qty=10, entry_price=120.0,
                                    entry_date=newer_entry)

        # Sell just 10 shares → should close the OLDER position
        close_oldest_position("GOOG", exit_price=130.0, sell_qty=10,
                               exit_decision=sell_d)

        older_updated = OpenPosition.get_by_id(older.id)
        newer_updated = OpenPosition.get_by_id(newer.id)

        assert older_updated.status == "closed"
        assert newer_updated.status == "open"   # untouched

    def test_no_open_positions_is_a_noop(self, in_memory_db):
        """No open positions for ticker → function completes without error."""
        sell_d = _make_decision(action="SELL")
        close_oldest_position("NOOP", exit_price=100.0, sell_qty=10,
                               exit_decision=sell_d)
        # If we get here without exception, the test passes
        count = OpenPosition.select().where(OpenPosition.ticker == "NOOP").count()
        assert count == 0


# ===========================================================================
# _calculate_max_drawdown()
# ===========================================================================

class TestMaxDrawdown:

    def test_fewer_than_two_snapshots_returns_zero(self, in_memory_db):
        EquitySnapshot.create(equity=100_000.0)
        assert _calculate_max_drawdown() == 0.0

    def test_no_snapshots_returns_zero(self, in_memory_db):
        assert _calculate_max_drawdown() == 0.0

    def test_peak_then_trough_then_recovery(self, in_memory_db):
        """
        Equities: [100, 105, 98, 102]
        Peak = 105 at index 1.
        Trough = 98 at index 2.
        Drawdown = (98 - 105) / 105 * 100 ≈ -6.67%
        """
        for eq in [100.0, 105.0, 98.0, 102.0]:
            EquitySnapshot.create(equity=eq)

        dd = _calculate_max_drawdown()
        expected = ((98.0 - 105.0) / 105.0) * 100
        assert math.isclose(dd, expected, rel_tol=1e-4)

    def test_all_rising_equity_returns_zero(self, in_memory_db):
        for eq in [100.0, 101.0, 102.0, 103.0]:
            EquitySnapshot.create(equity=eq)
        assert _calculate_max_drawdown() == 0.0

    def test_continuous_decline_drawdown_from_first(self, in_memory_db):
        """
        Equities: [100, 95, 90, 85]
        Max drawdown is from 100 to 85 = -15%.
        """
        for eq in [100.0, 95.0, 90.0, 85.0]:
            EquitySnapshot.create(equity=eq)

        dd = _calculate_max_drawdown()
        expected = ((85.0 - 100.0) / 100.0) * 100   # = -15.0
        assert math.isclose(dd, expected, rel_tol=1e-4)


# ===========================================================================
# _calculate_sharpe_ratio()
# ===========================================================================

class TestSharpeRatio:

    def test_fewer_than_three_snapshots_returns_zero(self, in_memory_db):
        for eq in [100.0, 101.0]:
            EquitySnapshot.create(equity=eq)
        assert _calculate_sharpe_ratio() == 0.0

    def test_no_snapshots_returns_zero(self, in_memory_db):
        assert _calculate_sharpe_ratio() == 0.0

    def test_constant_equity_returns_zero(self, in_memory_db):
        """std(returns) = 0 → Sharpe = 0.0 (division by zero guard)."""
        for _ in range(5):
            EquitySnapshot.create(equity=100_000.0)
        assert _calculate_sharpe_ratio() == 0.0

    def test_known_return_sequence_matches_formula(self, in_memory_db):
        """
        Insert known equities and verify Sharpe matches the formula:
          daily_returns = diff(equities) / equities[:-1]
          daily_rf = 0.05 / 252
          excess = daily_returns - daily_rf
          sharpe = (mean(excess) / std(excess)) * sqrt(252)
        """
        equities = [100_000.0, 101_000.0, 102_010.0, 103_030.1, 104_060.4]
        for eq in equities:
            EquitySnapshot.create(equity=eq)

        # Expected value calculated with the same formula
        eq_arr = np.array(equities)
        daily_returns = np.diff(eq_arr) / eq_arr[:-1]
        daily_rf = 0.05 / 252
        excess = daily_returns - daily_rf
        expected_sharpe = float(
            (np.mean(excess) / np.std(excess)) * np.sqrt(252)
        )

        result = _calculate_sharpe_ratio(risk_free_rate=0.05)
        assert math.isclose(result, expected_sharpe, rel_tol=1e-4)
