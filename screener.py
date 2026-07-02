"""
screener.py
Dynamic watchlist generator — replaces static watchlist.py with market-driven candidates.

Two-stage pipeline
------------------
Stage 1 — Generate (~40-70 raw symbols):
    Alpaca ScreenerClient  → most actives (top 25 by volume)
                           → top movers: gainers (15) + losers (15)
    yfinance screeners     → "day_gainers" (top 25)
                           → "most_actives" (top 25)

Stage 2 — Filter & rank:
    Fetch 35 days of daily OHLCV from Alpaca (one batch request)
    Compute: price (last bar close), avg_vol (30-day mean), RVOL = today / avg
    Keep:    price >= $5, RVOL >= 1.5x
    Sort:    descending RVOL
    Cap:     MAX_CANDIDATES (15)

    RVOL intraday handling: during regular hours "today's" daily bar is partial, so a
    naive today/avg ratio understates RVOL (~25% of a day's volume by 10:30 would look
    like RVOL 0.25). While the session is open, RVOL is computed as
        max(today_cumulative / (avg_vol × expected_session_fraction),  prior-day RVOL)
    where expected_session_fraction comes from a U-shaped intraday volume curve. This
    captures "unusually active right now" AND "was unusually active yesterday" without
    penalizing morning runs. Outside market hours the last bar is complete and the
    plain ratio is used unchanged.

Fallback: returns WATCHLIST from watchlist.py if all sources fail or filter yields 0.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yfinance as yf
from alpaca.data.enums import DataFeed, MarketType, MostActivesBy
from alpaca.data.historical import ScreenerClient, StockHistoricalDataClient
from alpaca.data.requests import (
    MarketMoversRequest,
    MostActivesRequest,
    StockBarsRequest,
)
from alpaca.data.timeframe import TimeFrame

from config import ALPACA_API_KEY, ALPACA_SECRET_KEY
from logger import get_logger
from watchlist import WATCHLIST as STATIC_WATCHLIST

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MIN_PRICE = 5.0          # Minimum last-bar close price ($)
MIN_RVOL = 1.5           # Today's volume must be >= 1.5x 30-day average
MAX_CANDIDATES = 15      # Maximum tickers returned to the PM agent
_BAR_LOOKBACK_DAYS = 35  # Fetch 35 calendar days to ensure >= 30 trading-day bars

# Cumulative fraction of a typical US-equity session's volume traded by N minutes
# after the 9:30 ET open. U-shaped: heavy open, quiet midday, heavy close.
# Piecewise-linear interpolation between anchors; session is 390 minutes.
_INTRADAY_CUM_VOL_PROFILE = [
    (0, 0.00), (15, 0.12), (30, 0.19), (60, 0.28), (120, 0.42),
    (180, 0.52), (240, 0.61), (300, 0.71), (360, 0.85), (390, 1.00),
]
_MIN_SESSION_FRACTION = 0.05  # floor — avoids wild extrapolation in the first minutes
_ET = ZoneInfo("America/New_York")


def _expected_session_fraction(now_et: datetime) -> float | None:
    """Expected cumulative fraction of the day's volume traded by `now_et`.

    Returns None outside regular hours (before 9:30 or after 16:00 ET) — the last
    daily bar is then complete and RVOL needs no intraday adjustment. Weekends and
    holidays are handled upstream: the last bar's date won't match today, so the
    partial-bar path is never taken.
    """
    minutes = (now_et.hour - 9) * 60 + (now_et.minute - 30)
    if minutes <= 0 or minutes >= 390:
        return None
    for (m0, f0), (m1, f1) in zip(_INTRADAY_CUM_VOL_PROFILE, _INTRADAY_CUM_VOL_PROFILE[1:]):
        if minutes <= m1:
            frac = f0 + (f1 - f0) * (minutes - m0) / (m1 - m0)
            return max(frac, _MIN_SESSION_FRACTION)
    return None


# ---------------------------------------------------------------------------
# Stage 1 helpers — candidate generation
# ---------------------------------------------------------------------------

def _get_alpaca_candidates(top: int = 25) -> set[str]:
    """
    Pull most-actives + top gainers + top losers from Alpaca ScreenerClient.
    Uses the market-data API keys (same pair as StockHistoricalDataClient).
    """
    tickers: set[str] = set()
    try:
        client = ScreenerClient(api_key=ALPACA_API_KEY, secret_key=ALPACA_SECRET_KEY)

        # Most active by volume
        actives = client.get_most_actives(
            MostActivesRequest(top=top, by=MostActivesBy.VOLUME)
        )
        for s in actives.most_actives:
            tickers.add(s.symbol)

        # Top movers — gainers and losers
        movers = client.get_market_movers(
            MarketMoversRequest(top=top // 2, market_type=MarketType.STOCKS)
        )
        for m in movers.gainers:
            tickers.add(m.symbol)
        for m in movers.losers:
            tickers.add(m.symbol)

        logger.info("Alpaca screener: %d raw candidates", len(tickers))
    except Exception as exc:
        logger.warning("Alpaca ScreenerClient failed: %s", exc)

    return tickers


def _get_yfinance_candidates() -> set[str]:
    """
    Pull day_gainers and most_actives from yfinance predefined screeners.
    No API key required.
    """
    tickers: set[str] = set()
    for screen_name in ("day_gainers", "most_actives"):
        try:
            result = yf.screen(screen_name, count=25)
            for q in result.get("quotes", []):
                sym = q.get("symbol")
                if sym:
                    tickers.add(sym)
            logger.info("yfinance '%s': %d tickers", screen_name, len(result.get("quotes", [])))
        except Exception as exc:
            logger.warning("yfinance screen '%s' failed: %s", screen_name, exc)

    return tickers


# ---------------------------------------------------------------------------
# Stage 2 — filter and rank by RVOL using Alpaca bar data
# ---------------------------------------------------------------------------

def _filter_and_rank(symbols: list[str], now_et: datetime | None = None) -> list[str]:
    """
    Batch-fetch daily bars for all candidates (one Alpaca API call), then:
      - Compute price (last bar close) and RVOL (today vs 30-day avg volume)
      - During regular hours, today's bar is partial: RVOL is the max of the
        intraday-adjusted ratio (today ÷ expected session fraction) and the prior
        complete day's RVOL — see module docstring
      - Drop: price < MIN_PRICE or RVOL < MIN_RVOL
      - Sort by RVOL descending, cap at MAX_CANDIDATES

    `now_et` is injectable for tests; defaults to the current Eastern time.
    Returns a list of ticker symbols.
    """
    if not symbols:
        return []

    if now_et is None:
        now_et = datetime.now(_ET)
    session_frac = _expected_session_fraction(now_et)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_BAR_LOOKBACK_DAYS)

    try:
        data_client = StockHistoricalDataClient(
            api_key=ALPACA_API_KEY,
            secret_key=ALPACA_SECRET_KEY,
        )
        bars_df = data_client.get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=symbols,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                feed=DataFeed.IEX,  # free-tier safe; default SIP rejects the most-recent window
            )
        ).df  # MultiIndex DataFrame: (symbol, timestamp)
    except Exception as exc:
        logger.error("Alpaca bar fetch failed in screener filter: %s — passing through raw list.", exc)
        return symbols[:MAX_CANDIDATES]

    scored: list[tuple[str, float]] = []

    for sym in symbols:
        try:
            sym_bars = bars_df.xs(sym, level="symbol")

            if len(sym_bars) < 5:
                continue  # Not enough history to compute a reliable avg

            price = float(sym_bars["close"].iloc[-1])
            if price < MIN_PRICE:
                continue

            # RVOL: today (last bar) vs mean of all prior bars in the window
            avg_vol = float(sym_bars["volume"].iloc[:-1].mean())
            today_vol = float(sym_bars["volume"].iloc[-1])

            if avg_vol <= 0:
                continue

            rvol = today_vol / avg_vol

            # Mid-session, the last bar is today's PARTIAL bar — the plain ratio
            # understates activity. Use the better of (a) today's volume scaled up
            # by the expected session fraction and (b) the prior complete day's
            # RVOL, so a morning run sees both live spikes and yesterday's heat.
            last_bar_date = sym_bars.index[-1].date()
            if session_frac is not None and last_bar_date == now_et.date():
                adjusted_today = (today_vol / session_frac) / avg_vol
                prev_rvol = 0.0
                prev_avg = float(sym_bars["volume"].iloc[:-2].mean()) if len(sym_bars) >= 7 else 0.0
                if prev_avg > 0:
                    prev_rvol = float(sym_bars["volume"].iloc[-2]) / prev_avg
                rvol = max(adjusted_today, prev_rvol)

            if rvol < MIN_RVOL:
                continue

            scored.append((sym, rvol))

        except (KeyError, IndexError):
            continue  # Symbol sparse or missing from Alpaca response

    scored.sort(key=lambda x: x[1], reverse=True)
    final = [sym for sym, _ in scored[:MAX_CANDIDATES]]

    logger.info(
        "Screener filter (price>=%.0f, RVOL>=%.1fx): %d/%d passed → %s",
        MIN_PRICE, MIN_RVOL, len(final), len(symbols), final,
    )
    return final


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_dynamic_watchlist() -> list[str]:
    """
    Build and return a dynamic list of up to MAX_CANDIDATES tickers for the PM agent.

    Pipeline:
      1. Alpaca ScreenerClient  — most-actives + top movers
      2. yfinance               — day_gainers + most_actives
      3. Union and deduplicate
      4. Filter by price >= $5 and RVOL >= 1.5x using Alpaca daily bars
      5. Sort by RVOL descending, cap at MAX_CANDIDATES
      6. Fall back to STATIC_WATCHLIST if result is empty

    Returns:
        list[str]: Ticker symbols ready for analysis.
    """
    logger.info("=== Dynamic Screener: generating watchlist ===")

    # Stage 1 — gather raw candidates from all sources
    raw: set[str] = _get_alpaca_candidates(top=25) | _get_yfinance_candidates()
    logger.info("Combined raw candidates: %d unique symbols", len(raw))

    if not raw:
        logger.warning("All screener sources returned 0 candidates — using static WATCHLIST.")
        return list(STATIC_WATCHLIST)

    # Stage 2 — filter and rank
    filtered = _filter_and_rank(list(raw))

    if not filtered:
        logger.warning(
            "All %d candidates failed price/RVOL filters — using static WATCHLIST.", len(raw)
        )
        return list(STATIC_WATCHLIST)

    logger.info("=== Dynamic Screener complete: %d tickers ===", len(filtered))
    return filtered


# ---------------------------------------------------------------------------
# CLI — run screener standalone for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging as _logging

    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    candidates = get_dynamic_watchlist()
    print(f"\n{'='*50}")
    print(f"  Dynamic Watchlist ({len(candidates)} tickers)")
    print(f"{'='*50}")
    for t in candidates:
        print(f"  {t}")
    print(f"{'='*50}\n")
