# scheduler.py
# Runs the watchlist scan twice per day on market days (10:00 AM and 2:00 PM).
# Times are based on the local machine clock — ensure your machine is set to ET
# or adjust RUN_TIMES below to match your timezone offset.
#
# To start: python scheduler.py
# To stop:  Ctrl+C

import schedule
import time
from datetime import datetime
import pandas_market_calendars as mcal

from pm_agent import run_watchlist
from watchlist import WATCHLIST
from logger import get_logger

logger = get_logger(__name__)

# --- Config ---
RUN_TIMES = ["10:00", "14:00"]   # 10:00 AM and 2:00 PM local (ET) time
MARKET_CALENDAR = "NYSE"


def is_market_open_today() -> bool:
    """Returns True if the NYSE is open today (handles weekends and US market holidays)."""
    nyse = mcal.get_calendar(MARKET_CALENDAR)
    today = datetime.now().strftime("%Y-%m-%d")
    market_schedule = nyse.schedule(start_date=today, end_date=today)
    return not market_schedule.empty


def scheduled_run():
    """Triggered by the scheduler — skips if the market is closed today."""
    if not is_market_open_today():
        logger.info("Market is closed today (weekend or US holiday). Skipping scan.")
        return

    logger.info("Market is open. Launching watchlist scan...")
    try:
        run_watchlist()
        logger.info("Watchlist scan completed successfully.")
    except Exception as e:
        logger.error("Watchlist scan failed: %s", e)


# --- Register schedules ---
for run_time in RUN_TIMES:
    schedule.every().day.at(run_time).do(scheduled_run)


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("Stock Agent Scheduler Started")
    logger.info("Scheduled run times: %s ET (local machine time)", ", ".join(RUN_TIMES))
    logger.info("Watchlist: %s", ", ".join(WATCHLIST))
    logger.info("Press Ctrl+C to stop.")
    logger.info("=" * 50)

    # Run immediately on startup so you don't have to wait for the first scheduled time
    logger.info("Running initial scan on startup...")
    scheduled_run()

    while True:
        schedule.run_pending()
        time.sleep(30)   # Check every 30 seconds
