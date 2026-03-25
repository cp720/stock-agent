"""
tests/conftest.py
Shared pytest fixtures and environment setup for the stock agent test suite.

IMPORTANT: env vars MUST be set before any project module is imported,
because config.py validates and raises EnvironmentError at import time.
"""
import os

# --- Fake env vars (set before any project imports) ---
os.environ.setdefault("ALPACA_API_KEY", "TEST_ALPACA_KEY")
os.environ.setdefault("ALPACA_API_SECRET", "TEST_ALPACA_SECRET")
os.environ.setdefault("ALPACA_TRADING_API_KEY", "TEST_TRADING_KEY")
os.environ.setdefault("ALPACA_TRADING_SECRET", "TEST_TRADING_SECRET")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai-key")
os.environ.setdefault("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/test")

import numpy as np
import pandas as pd
import peewee as pw
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# In-memory Peewee DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def in_memory_db():
    """
    Binds all trade journal models to an in-memory SQLite DB for the duration
    of the test. The on-disk data/trade_journal.db is never touched.
    """
    from trade_journal import ALL_TABLES
    test_db = pw.SqliteDatabase(":memory:")
    with test_db.bind_ctx(ALL_TABLES):
        test_db.create_tables(ALL_TABLES)
        yield test_db
        test_db.drop_tables(ALL_TABLES)


# ---------------------------------------------------------------------------
# OHLCV DataFrame factory
# ---------------------------------------------------------------------------

@pytest.fixture
def make_ohlcv_df():
    """
    Returns a factory function that builds a MultiIndex (symbol, timestamp)
    DataFrame matching the shape of StockHistoricalDataClient.get_stock_bars().df.

    Parameters (all optional):
        symbols     — list of ticker strings
        n_bars      — number of daily bars (default 35)
        base_price  — starting close price (default 50.0)
        avg_volume  — volume for bars 0..n-2 (default 1_000_000)
        today_rvol  — multiplier for last bar's volume vs avg (default 2.0)
    """
    def _factory(
        symbols,
        n_bars: int = 35,
        base_price: float = 50.0,
        avg_volume: int = 1_000_000,
        today_rvol: float = 2.0,
    ) -> pd.DataFrame:
        np.random.seed(42)
        dfs = []
        for sym in symbols:
            dates = pd.date_range("2025-01-01", periods=n_bars, freq="D")
            prices = np.linspace(base_price, base_price + 5, n_bars)
            vols = np.full(n_bars, float(avg_volume))
            vols[-1] = avg_volume * today_rvol  # last bar = "today" with elevated RVOL
            df_sym = pd.DataFrame(
                {
                    "open": prices * 0.99,
                    "high": prices * 1.01,
                    "low": prices * 0.98,
                    "close": prices,
                    "volume": vols,
                },
                index=pd.MultiIndex.from_tuples(
                    [(sym, d) for d in dates], names=["symbol", "timestamp"]
                ),
            )
            dfs.append(df_sym)
        return pd.concat(dfs)

    return _factory


# ---------------------------------------------------------------------------
# Mock Alpaca account helper
# ---------------------------------------------------------------------------

def make_mock_account(
    equity: float,
    cash: float,
    long_market_value: float = 0.0,
    last_equity: float = None,
) -> MagicMock:
    """Create a MagicMock that mimics an Alpaca Account object."""
    acct = MagicMock()
    acct.equity = str(equity)
    acct.cash = str(cash)
    acct.long_market_value = str(long_market_value)
    acct.last_equity = str(last_equity if last_equity is not None else equity)
    return acct


def make_mock_position(
    symbol: str,
    market_value: float,
    qty: float = 100.0,
    unrealized_pl: float = 0.0,
    unrealized_intraday_pl: float = 0.0,
    cost_basis: float = None,
    current_price: float = None,
    avg_entry_price: float = None,
    unrealized_plpc: float = 0.0,
) -> MagicMock:
    """Create a MagicMock that mimics an Alpaca Position object."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.qty = str(qty)
    pos.market_value = str(market_value)
    pos.cost_basis = str(cost_basis if cost_basis is not None else market_value)
    pos.unrealized_pl = str(unrealized_pl)
    pos.unrealized_plpc = str(unrealized_plpc)
    pos.unrealized_intraday_pl = str(unrealized_intraday_pl)
    pos.current_price = str(current_price if current_price is not None else 100.0)
    pos.avg_entry_price = str(avg_entry_price if avg_entry_price is not None else 100.0)
    return pos
