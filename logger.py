# logger.py
# Centralised logging configuration for the stock agent system.
# Every module should obtain its logger via:
#
#   from logger import get_logger
#   logger = get_logger(__name__)
#
# All loggers write to:
#   - Console (stdout) — for live visibility when running interactively
#   - logs/agent.log  — rotating file (5 MB per file, 5 backups kept)
#
# Log levels used across the codebase:
#   logger.debug()    — detailed diagnostic info (disabled by default)
#   logger.info()     — normal operation: scan started, ticker analysed, etc.
#   logger.warning()  — non-fatal issues: NaN values, insufficient data
#   logger.error()    — recoverable errors: failed ticker, bad API response
#   logger.critical() — system-level failures that halt execution

import logging
import logging.handlers
import os

# --- Config ---
LOG_DIR     = "logs"
LOG_FILE    = os.path.join(LOG_DIR, "agent.log")
LOG_FORMAT  = "%(asctime)s [%(levelname)-8s] %(name)s — %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES   = 5 * 1024 * 1024   # 5 MB per file
BACKUP_COUNT = 5                 # keep 5 rotated files


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger with console and rotating file handlers attached.
    Safe to call multiple times — handlers are only added once per logger name.

    Args:
        name (str): Typically __name__ from the calling module.

    Returns:
        logging.Logger: Configured logger instance.
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    logger = logging.getLogger(name)

    # Guard against duplicate handlers if the module is imported multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # --- Console handler ---
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # --- Rotating file handler ---
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
