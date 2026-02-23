# scheduler.py
# Runs the watchlist scan twice per day on market days (10:00 AM and 2:00 PM).
# Times are based on the local machine clock — ensure your machine is set to ET
# or adjust RUN_TIMES below to match your timezone offset.
#
# To start: python scheduler.py
# To stop:  Ctrl+C

import schedule
import time
import logging
from datetime import datetime
import pandas_market_calendars as mcal

from pm_agent import run_watchlist
from watchlist import WATCHLIST

# --- Logging setup ---
# Logs to both the console and a persistent scheduler.log file
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scheduler.log"),
    ]
)

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
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not is_market_open_today():
        logging.info("Market is closed today (weekend or US holiday). Skipping scan.")
        return

    logging.info(f"Starting watchlist scan at {now}")
    logging.info(f"Tickers: {', '.join(WATCHLIST)}")

    try:
        run_watchlist()
        logging.info("Watchlist scan completed successfully.")
    except Exception as e:
        logging.error(f"Watchlist scan failed: {e}")


# --- Register schedules ---
for run_time in RUN_TIMES:
    schedule.every().day.at(run_time).do(scheduled_run)


if __name__ == "__main__":
    logging.info("=" * 50)
    logging.info("Stock Agent Scheduler Started")
    logging.info(f"Scheduled run times: {', '.join(RUN_TIMES)} ET (local machine time)")
    logging.info(f"Watchlist: {', '.join(WATCHLIST)}")
    logging.info("Press Ctrl+C to stop.")
    logging.info("=" * 50)

    # Run immediately on startup so you don't have to wait for the first scheduled time
    logging.info("Running initial scan on startup...")
    scheduled_run()

    while True:
        schedule.run_pending()
        time.sleep(30)   # Check every 30 seconds
