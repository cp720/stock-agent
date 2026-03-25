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
    _filter_and_rank,
    _get_alpaca_candidates,
    _get_yfinance_candidates,
    get_dynamic_watchlist,
    MAX_CANDIDATES,
    MIN_PRICE,
    MIN_RVOL,
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
