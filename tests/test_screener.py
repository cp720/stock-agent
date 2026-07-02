"""
tests/test_screener.py

Tests for screener.py:
  - _filter_and_rank()  — price filter, RVOL filter, sort, cap at 15
  - _get_alpaca_candidates()  — error handling
  - _get_yfinance_candidates() — error handling
  - get_dynamic_watchlist()   — fallback to static WATCHLIST
"""
import pandas as pd
import numpy as np
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from screener import (
    _expected_session_fraction,
    _filter_and_rank,
    _get_alpaca_candidates,
    _get_yfinance_candidates,
    get_dynamic_watchlist,
    MAX_CANDIDATES,
    MIN_PRICE,
    MIN_RVOL,
    _ET,
    _MIN_SESSION_FRACTION,
)
from watchlist import WATCHLIST as STATIC_WATCHLIST


# ---------------------------------------------------------------------------
# Helper: build a MultiIndex (symbol, timestamp) DataFrame
# ---------------------------------------------------------------------------

def _make_bars_df(
    symbol: str,
    n_bars: int = 35,
    close_price: float = 50.0,
    avg_volume: float = 1_000_000.0,
    today_rvol: float = 2.0,
) -> pd.DataFrame:
    """Single-symbol bars DataFrame with controlled close/volume."""
    dates = pd.date_range("2025-01-01", periods=n_bars, freq="D")
    vols = np.full(n_bars, avg_volume)
    vols[-1] = avg_volume * today_rvol  # last bar is "today"
    closes = np.full(n_bars, close_price)

    return pd.DataFrame(
        {
            "open": closes * 0.99,
            "high": closes * 1.01,
            "low": closes * 0.98,
            "close": closes,
            "volume": vols,
        },
        index=pd.MultiIndex.from_tuples(
            [(symbol, d) for d in dates], names=["symbol", "timestamp"]
        ),
    )


def _mock_bars_response(*symbol_dfs):
    """Wraps several per-symbol DataFrames into a mock bars response."""
    combined = pd.concat(symbol_dfs)
    mock_resp = MagicMock()
    mock_resp.df = combined
    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = mock_resp
    return mock_client


# ===========================================================================
# _filter_and_rank() tests
# ===========================================================================

class TestFilterAndRank:

    def test_empty_input_returns_empty_list(self):
        assert _filter_and_rank([]) == []

    def test_symbol_missing_from_bars_is_skipped(self):
        """Symbol requested but absent from Alpaca response → excluded."""
        df = _make_bars_df("AAPL", close_price=100.0, today_rvol=2.0)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["AAPL", "MISSING"])
        assert "MISSING" not in result

    def test_price_below_min_is_dropped(self):
        df = _make_bars_df("CHEAP", close_price=MIN_PRICE - 0.01, today_rvol=3.0)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["CHEAP"])
        assert result == []

    def test_price_at_min_passes(self):
        df = _make_bars_df("OK", close_price=MIN_PRICE, today_rvol=3.0)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["OK"])
        assert "OK" in result

    def test_rvol_below_threshold_is_dropped(self):
        """today_vol / avg_vol < MIN_RVOL → excluded."""
        df = _make_bars_df("LOW", close_price=50.0, avg_volume=1_000_000, today_rvol=MIN_RVOL - 0.1)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["LOW"])
        assert result == []

    def test_rvol_at_threshold_passes(self):
        df = _make_bars_df("PASS", close_price=50.0, avg_volume=1_000_000, today_rvol=MIN_RVOL)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["PASS"])
        assert "PASS" in result

    def test_zero_avg_volume_is_skipped_safely(self):
        """avg_vol = 0 → division guard → symbol excluded, no crash."""
        n = 35
        dates = pd.date_range("2025-01-01", periods=n, freq="D")
        vols = np.zeros(n)  # all zeros
        vols[-1] = 500_000   # today has volume, but avg of prior bars = 0
        closes = np.full(n, 50.0)
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": vols},
            index=pd.MultiIndex.from_tuples(
                [("ZERO", d) for d in dates], names=["symbol", "timestamp"]
            ),
        )
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["ZERO"])
        assert result == []

    def test_insufficient_bars_skipped(self):
        """Symbol with < 5 bars → excluded."""
        dates = pd.date_range("2025-01-01", periods=4, freq="D")
        df = pd.DataFrame(
            {"open": [50]*4, "high": [51]*4, "low": [49]*4, "close": [50]*4, "volume": [1e6]*4},
            index=pd.MultiIndex.from_tuples(
                [("FEW", d) for d in dates], names=["symbol", "timestamp"]
            ),
        )
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["FEW"])
        assert result == []

    def test_results_sorted_by_rvol_descending(self):
        """Higher RVOL symbol should appear first."""
        df_high = _make_bars_df("HIGH", close_price=50.0, avg_volume=1_000_000, today_rvol=5.0)
        df_low  = _make_bars_df("LOW",  close_price=50.0, avg_volume=1_000_000, today_rvol=2.0)
        combined = pd.concat([df_high, df_low])
        mock_resp = MagicMock(); mock_resp.df = combined
        mock_client = MagicMock(); mock_client.get_stock_bars.return_value = mock_resp
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["LOW", "HIGH"])  # intentionally reversed input
        assert result[0] == "HIGH"
        assert result[1] == "LOW"

    def test_results_capped_at_max_candidates(self):
        """More than MAX_CANDIDATES valid symbols → only MAX_CANDIDATES returned."""
        symbols = [f"SYM{i:02d}" for i in range(MAX_CANDIDATES + 5)]
        dfs = [_make_bars_df(s, close_price=50.0, today_rvol=2.0) for s in symbols]
        combined = pd.concat(dfs)
        mock_resp = MagicMock(); mock_resp.df = combined
        mock_client = MagicMock(); mock_client.get_stock_bars.return_value = mock_resp
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(symbols)
        assert len(result) <= MAX_CANDIDATES

    def test_alpaca_bar_fetch_failure_falls_back_to_raw_list(self):
        """If bar fetch raises, returns first MAX_CANDIDATES from the raw list."""
        mock_client = MagicMock()
        mock_client.get_stock_bars.side_effect = Exception("network error")
        symbols = ["A", "B", "C"]
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(symbols)
        assert set(result).issubset(set(symbols))


# ===========================================================================
# Intraday RVOL adjustment tests
# ===========================================================================

# The _make_bars_df helper generates bars ending 2025-02-04; use that as "today"
# for partial-bar scenarios.
_LAST_BAR_DAY = datetime(2025, 2, 4)


def _et_time(hour: int, minute: int) -> datetime:
    return _LAST_BAR_DAY.replace(hour=hour, minute=minute, tzinfo=_ET)


class TestExpectedSessionFraction:

    def test_before_open_returns_none(self):
        assert _expected_session_fraction(_et_time(9, 0)) is None

    def test_at_open_returns_none(self):
        assert _expected_session_fraction(_et_time(9, 30)) is None

    def test_after_close_returns_none(self):
        assert _expected_session_fraction(_et_time(16, 0)) is None
        assert _expected_session_fraction(_et_time(19, 45)) is None

    def test_just_after_open_is_floored(self):
        """First minutes would extrapolate wildly — floored to _MIN_SESSION_FRACTION."""
        frac = _expected_session_fraction(_et_time(9, 31))
        assert frac == _MIN_SESSION_FRACTION

    def test_10am_matches_profile_anchor(self):
        """30 minutes into the session → 0.19 anchor exactly."""
        assert _expected_session_fraction(_et_time(10, 0)) == pytest.approx(0.19)

    def test_fraction_increases_monotonically(self):
        times = [(10, 0), (11, 0), (12, 30), (14, 0), (15, 30), (15, 59)]
        fracs = [_expected_session_fraction(_et_time(h, m)) for h, m in times]
        assert all(a < b for a, b in zip(fracs, fracs[1:]))


class TestIntradayRvolAdjustment:

    def test_partial_bar_understated_rvol_is_rescued(self):
        """
        Mid-morning, today's cumulative volume is only 0.5x the daily average —
        the naive ratio (0.5) fails MIN_RVOL, but scaled by the 10:00 session
        fraction (0.19) the adjusted RVOL is ~2.6x → passes.
        """
        df = _make_bars_df("MORN", close_price=50.0, avg_volume=1_000_000, today_rvol=0.5)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["MORN"], now_et=_et_time(10, 0))
        assert "MORN" in result

    def test_prior_day_rvol_rescues_quiet_morning(self):
        """
        Today's partial volume is negligible, but YESTERDAY traded 3x its average —
        the prior-day RVOL keeps the ticker in the list.
        """
        n = 35
        dates = pd.date_range("2025-01-01", periods=n, freq="D")
        vols = np.full(n, 1_000_000.0)
        vols[-2] = 3_000_000.0   # yesterday: 3x average
        vols[-1] = 50_000.0      # today so far: negligible
        closes = np.full(n, 50.0)
        df = pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes, "volume": vols},
            index=pd.MultiIndex.from_tuples(
                [("YEST", d) for d in dates], names=["symbol", "timestamp"]
            ),
        )
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["YEST"], now_et=_et_time(10, 0))
        assert "YEST" in result

    def test_outside_market_hours_uses_plain_ratio(self):
        """
        Same understated data evaluated pre-market → last bar treated as complete,
        plain ratio (0.5) fails MIN_RVOL as before.
        """
        df = _make_bars_df("PREM", close_price=50.0, avg_volume=1_000_000, today_rvol=0.5)
        mock_client = _mock_bars_response(df)
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["PREM"], now_et=_et_time(8, 0))
        assert result == []

    def test_stale_last_bar_not_treated_as_partial(self):
        """
        Mid-session now_et but the last bar is from a PRIOR day (weekend/holiday) —
        the complete-bar path applies, no intraday scaling.
        """
        df = _make_bars_df("STALE", close_price=50.0, avg_volume=1_000_000, today_rvol=0.5)
        mock_client = _mock_bars_response(df)
        later = datetime(2025, 2, 6, 10, 0, tzinfo=_ET)  # two days after last bar
        with patch("screener.StockHistoricalDataClient", return_value=mock_client):
            result = _filter_and_rank(["STALE"], now_et=later)
        assert result == []


# ===========================================================================
# Source error-handling tests
# ===========================================================================

class TestAlpacaCandidates:

    def test_screener_client_exception_returns_empty_set(self):
        """If ScreenerClient raises, _get_alpaca_candidates returns empty set, no crash."""
        with patch("screener.ScreenerClient", side_effect=Exception("API down")):
            result = _get_alpaca_candidates()
        assert isinstance(result, set)
        assert len(result) == 0


class TestYfinanceCandidates:

    def test_yf_screen_exception_returns_empty_set(self):
        """If yf.screen() raises for all screens, returns empty set."""
        with patch("screener.yf.screen", side_effect=Exception("yf down")):
            result = _get_yfinance_candidates()
        assert isinstance(result, set)
        assert len(result) == 0

    def test_empty_quotes_returns_empty_set(self):
        """yf.screen returns a response with no 'quotes' key."""
        with patch("screener.yf.screen", return_value={}):
            result = _get_yfinance_candidates()
        assert isinstance(result, set)
        assert len(result) == 0


# ===========================================================================
# get_dynamic_watchlist() fallback behaviour
# ===========================================================================

class TestDynamicWatchlistFallback:

    def test_all_sources_fail_returns_static_watchlist(self):
        """Both Alpaca and yfinance fail → falls back to STATIC_WATCHLIST."""
        with (
            patch("screener.ScreenerClient", side_effect=Exception("down")),
            patch("screener.yf.screen", side_effect=Exception("down")),
        ):
            result = get_dynamic_watchlist()
        assert result == list(STATIC_WATCHLIST)

    def test_candidates_fail_filter_returns_static_watchlist(self):
        """
        Sources return tickers but all fail the price/RVOL filter
        (price too low) → falls back to STATIC_WATCHLIST.
        """
        cheap_df = _make_bars_df("DIRT", close_price=1.0, today_rvol=3.0)
        mock_resp = MagicMock(); mock_resp.df = cheap_df
        mock_client = MagicMock(); mock_client.get_stock_bars.return_value = mock_resp

        with (
            patch("screener.ScreenerClient", side_effect=Exception("down")),
            patch("screener.yf.screen", return_value={"quotes": [{"symbol": "DIRT"}]}),
            patch("screener.StockHistoricalDataClient", return_value=mock_client),
        ):
            result = get_dynamic_watchlist()
        assert result == list(STATIC_WATCHLIST)

    def test_valid_candidates_do_not_return_static_watchlist(self):
        """Sources return a valid ticker that passes filters → NOT the static watchlist."""
        good_df = _make_bars_df("GOOD", close_price=50.0, today_rvol=3.0)
        mock_resp = MagicMock(); mock_resp.df = good_df
        mock_client = MagicMock(); mock_client.get_stock_bars.return_value = mock_resp

        with (
            patch("screener.ScreenerClient", side_effect=Exception("down")),
            patch("screener.yf.screen", return_value={"quotes": [{"symbol": "GOOD"}]}),
            patch("screener.StockHistoricalDataClient", return_value=mock_client),
        ):
            result = get_dynamic_watchlist()
        assert "GOOD" in result
        assert result != list(STATIC_WATCHLIST)
