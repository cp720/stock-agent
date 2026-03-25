"""
tests/test_pm_tools.py

Tests for the pure-logic portions of pm_agent.py:
  - get_portfolio_risk_assessment() — risk flag threshold boundary tests
  - execute_trade()                 — safeguard short-circuit tests

All Alpaca API calls are mocked. JOURNAL_ENABLED is patched to False to
skip the drawdown DB query (tested separately in test_trade_journal.py).
"""
import pytest
from unittest.mock import MagicMock, patch

import pm_agent
from pm_agent import (
    get_portfolio_risk_assessment,
    execute_trade,
    MAX_POSITION_PCT,
    MAX_DAILY_TRADES,
)
from tests.conftest import make_mock_account, make_mock_position


# ---------------------------------------------------------------------------
# Helper: build risk assessment result with controlled inputs
# ---------------------------------------------------------------------------

def _risk_result(
    equity: float = 100_000.0,
    cash: float = 30_000.0,
    long_market_value: float = 70_000.0,
    last_equity: float = None,
    positions=None,
):
    """
    Call get_portfolio_risk_assessment() with a fully mocked Alpaca client.
    Returns the resulting dict.
    """
    if positions is None:
        positions = []

    account = make_mock_account(
        equity=equity,
        cash=cash,
        long_market_value=long_market_value,
        last_equity=last_equity if last_equity is not None else equity,
    )

    mock_client = MagicMock()
    mock_client.get_account.return_value = account
    mock_client.get_all_positions.return_value = positions

    with (
        patch.object(pm_agent, "alpaca_trading_client", mock_client),
        patch.object(pm_agent, "JOURNAL_ENABLED", False),
    ):
        return get_portfolio_risk_assessment.entrypoint()


def _flags(result: dict) -> list:
    return result["risk_flags"]


def _has_flag(result: dict, keyword: str) -> bool:
    return any(keyword in f for f in _flags(result))


# ===========================================================================
# Risk flag — HIGH CONCENTRATION (position ≥ 12% of equity)
# ===========================================================================

class TestHighConcentrationFlag:

    def test_exactly_12pct_triggers_flag(self):
        pos = make_mock_position("AAPL", market_value=12_000.0)  # 12% of 100k
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert _has_flag(result, "HIGH CONCENTRATION")

    def test_below_12pct_no_flag(self):
        pos = make_mock_position("AAPL", market_value=11_900.0)  # 11.9%
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert not _has_flag(result, "HIGH CONCENTRATION")

    def test_multiple_positions_only_heavy_one_flagged(self):
        pos_big   = make_mock_position("NVDA", market_value=15_000.0)  # 15%
        pos_small = make_mock_position("MSFT", market_value=5_000.0)   # 5%
        result = _risk_result(equity=100_000.0,
                              long_market_value=20_000.0,
                              positions=[pos_big, pos_small])
        flags = _flags(result)
        assert any("NVDA" in f and "HIGH CONCENTRATION" in f for f in flags)
        assert not any("MSFT" in f and "HIGH CONCENTRATION" in f for f in flags)


# ===========================================================================
# Risk flag — PORTFOLIO DRAWDOWN (drawdown ≤ -5% from peak equity)
# ===========================================================================

class TestPortfolioDrawdownFlag:
    """
    JOURNAL_ENABLED=False means drawdown_from_peak stays 0.0 throughout.
    To test the flag, we patch the drawdown value inline.
    """

    def test_drawdown_at_neg5_triggers_flag(self):
        """Patch drawdown_from_peak calculation to return exactly -5.0."""
        account = make_mock_account(equity=95_000.0, cash=30_000.0,
                                    long_market_value=65_000.0)
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_all_positions.return_value = []

        peak_eq = MagicMock()
        peak_eq.equity = 100_000.0   # (95k - 100k)/100k * 100 = -5.0%

        with (
            patch.object(pm_agent, "alpaca_trading_client", mock_client),
            patch.object(pm_agent, "JOURNAL_ENABLED", True),
            patch("pm_agent.initialize_db"),
            patch("pm_agent.EquitySnapshot") as mock_snap,
        ):
            mock_snap.select.return_value.order_by.return_value.first.return_value = peak_eq
            result = get_portfolio_risk_assessment.entrypoint()

        assert _has_flag(result, "PORTFOLIO DRAWDOWN")

    def test_drawdown_at_neg4_9_no_flag(self):
        account = make_mock_account(equity=95_100.0, cash=30_000.0,
                                    long_market_value=65_100.0)
        mock_client = MagicMock()
        mock_client.get_account.return_value = account
        mock_client.get_all_positions.return_value = []

        peak_eq = MagicMock()
        peak_eq.equity = 100_000.0   # (95100 - 100000)/100000 * 100 = -4.9%

        with (
            patch.object(pm_agent, "alpaca_trading_client", mock_client),
            patch.object(pm_agent, "JOURNAL_ENABLED", True),
            patch("pm_agent.initialize_db"),
            patch("pm_agent.EquitySnapshot") as mock_snap,
        ):
            mock_snap.select.return_value.order_by.return_value.first.return_value = peak_eq
            result = get_portfolio_risk_assessment.entrypoint()

        assert not _has_flag(result, "PORTFOLIO DRAWDOWN")


# ===========================================================================
# Risk flag — LOW CASH (cash < 20% of equity, only when positions exist)
# ===========================================================================

class TestLowCashFlag:

    def test_cash_19_9_pct_with_positions_triggers_flag(self):
        pos = make_mock_position("X", market_value=10_000.0)
        result = _risk_result(equity=100_000.0, cash=19_900.0,
                              long_market_value=80_100.0, positions=[pos])
        assert _has_flag(result, "LOW CASH")

    def test_cash_20_pct_no_flag(self):
        pos = make_mock_position("X", market_value=10_000.0)
        result = _risk_result(equity=100_000.0, cash=20_000.0,
                              long_market_value=80_000.0, positions=[pos])
        assert not _has_flag(result, "LOW CASH")

    def test_low_cash_no_positions_no_flag(self):
        """LOW CASH flag only fires when positions exist (len(positions) > 0)."""
        result = _risk_result(equity=100_000.0, cash=5_000.0,
                              long_market_value=0.0, positions=[])
        assert not _has_flag(result, "LOW CASH")


# ===========================================================================
# Risk flag — HEAVY EXPOSURE (invested > 80% of equity)
# ===========================================================================

class TestHeavyExposureFlag:

    def test_invested_80_1_pct_triggers_flag(self):
        result = _risk_result(equity=100_000.0, cash=19_900.0,
                              long_market_value=80_100.0)
        assert _has_flag(result, "HEAVY EXPOSURE")

    def test_invested_exactly_80_pct_no_flag(self):
        result = _risk_result(equity=100_000.0, cash=20_000.0,
                              long_market_value=80_000.0)
        assert not _has_flag(result, "HEAVY EXPOSURE")


# ===========================================================================
# Risk flag — INTRADAY LOSS (intraday P&L loss > 1% of equity)
# ===========================================================================

class TestIntradayLossFlag:

    def test_intraday_loss_above_1pct_triggers_flag(self):
        # Loss of $1,001 on $100k equity = 1.001% → triggers flag
        pos = make_mock_position("A", market_value=50_000.0, unrealized_intraday_pl=-1_001.0)
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert _has_flag(result, "INTRADAY LOSS")

    def test_intraday_loss_below_1pct_no_flag(self):
        pos = make_mock_position("A", market_value=50_000.0, unrealized_intraday_pl=-999.0)
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert not _has_flag(result, "INTRADAY LOSS")

    def test_intraday_gain_no_flag(self):
        pos = make_mock_position("A", market_value=50_000.0, unrealized_intraday_pl=500.0)
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert not _has_flag(result, "INTRADAY LOSS")


# ===========================================================================
# Risk flag — UNREALIZED LOSS (total unrealized P&L ≤ -5% of equity)
# ===========================================================================

class TestUnrealizedLossFlag:

    def test_unrealized_loss_at_neg5_1_pct_triggers_flag(self):
        pos = make_mock_position("B", market_value=50_000.0, unrealized_pl=-5_100.0)
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert _has_flag(result, "UNREALIZED LOSS")

    def test_unrealized_loss_at_neg4_9_pct_no_flag(self):
        pos = make_mock_position("B", market_value=50_000.0, unrealized_pl=-4_900.0)
        result = _risk_result(equity=100_000.0, positions=[pos])
        assert not _has_flag(result, "UNREALIZED LOSS")


# ===========================================================================
# No flags scenario
# ===========================================================================

class TestNoRiskFlags:

    def test_healthy_portfolio_returns_no_risk_flags(self):
        pos = make_mock_position("AAPL", market_value=5_000.0)  # 5% — well within limits
        result = _risk_result(
            equity=100_000.0,
            cash=30_000.0,
            long_market_value=70_000.0,
            positions=[pos],
        )
        assert _has_flag(result, "NO RISK FLAGS")


# ===========================================================================
# execute_trade() safeguard tests
# ===========================================================================

class TestExecuteTradeSafeguards:

    def _mock_clock(self, is_open: bool):
        clock = MagicMock()
        clock.is_open = is_open
        return clock

    def _mock_orders(self, filled_count: int):
        """Return a list of mock orders that all have filled_at set."""
        return [MagicMock() for _ in range(filled_count)]

    def test_market_closed_skips_order(self):
        mock_client = MagicMock()
        mock_client.get_clock.return_value = self._mock_clock(is_open=False)

        with patch.object(pm_agent, "alpaca_trading_client", mock_client):
            result = execute_trade.entrypoint("AAPL", "BUY", 10)

        assert "skipped" in result
        assert "market" in result.lower() or "closed" in result.lower()
        mock_client.submit_order.assert_not_called()

    def test_daily_trade_limit_reached_skips_order(self):
        mock_client = MagicMock()
        mock_client.get_clock.return_value = self._mock_clock(is_open=True)
        mock_client.get_orders.return_value = self._mock_orders(MAX_DAILY_TRADES)

        with patch.object(pm_agent, "alpaca_trading_client", mock_client):
            result = execute_trade.entrypoint("AAPL", "BUY", 10)

        assert "skipped" in result
        assert "limit" in result.lower() or "daily" in result.lower()
        mock_client.submit_order.assert_not_called()

    def test_sell_without_position_skips_order(self):
        mock_client = MagicMock()
        mock_client.get_clock.return_value = self._mock_clock(is_open=True)
        # Fewer than MAX_DAILY_TRADES filled orders
        mock_client.get_orders.return_value = self._mock_orders(0)
        # No positions held
        mock_client.get_all_positions.return_value = []

        with patch.object(pm_agent, "alpaca_trading_client", mock_client):
            result = execute_trade.entrypoint("NVDA", "SELL", 5)

        assert "skipped" in result
        assert "not held" in result.lower() or "position" in result.lower()
        mock_client.submit_order.assert_not_called()

    def test_buy_at_max_allocation_skips_order(self):
        """BUY when existing position already ≥ MAX_POSITION_PCT of equity → skipped."""
        equity = 100_000.0
        max_val = equity * MAX_POSITION_PCT   # default 15 000.0

        mock_account = make_mock_account(equity=equity, cash=30_000.0)
        existing_pos = MagicMock()
        existing_pos.market_value = str(max_val)   # exactly at limit

        mock_client = MagicMock()
        mock_client.get_clock.return_value = self._mock_clock(is_open=True)
        mock_client.get_orders.return_value = self._mock_orders(0)
        mock_client.get_account.return_value = mock_account
        mock_client.get_open_position.return_value = existing_pos

        with patch.object(pm_agent, "alpaca_trading_client", mock_client):
            result = execute_trade.entrypoint("AAPL", "BUY", 50)

        assert "skipped" in result
        assert "max allocation" in result.lower() or "allocation" in result.lower()
        mock_client.submit_order.assert_not_called()
