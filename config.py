import os
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# Basic Keys - used for data retrieval
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_API_SECRET")

# Trading specific keys - currently paper trading only
ALPACA_TRADING_KEY = os.getenv("ALPACA_TRADING_API_KEY")
ALPACA_TRADING_SECRET = os.getenv("ALPACA_TRADING_SECRET")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")

# --- Validate required environment variables ---
_REQUIRED_VARS = {
    "ALPACA_API_KEY": ALPACA_API_KEY,
    "ALPACA_API_SECRET": ALPACA_SECRET_KEY,
    "ALPACA_TRADING_API_KEY": ALPACA_TRADING_KEY,
    "ALPACA_TRADING_SECRET": ALPACA_TRADING_SECRET,
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "N8N_WEBHOOK_URL": N8N_WEBHOOK_URL,
}

_missing = [k for k, v in _REQUIRED_VARS.items() if not v]
if _missing:
    raise EnvironmentError(
        f"Missing required environment variables: {', '.join(_missing)}\n"
        "Please check your .env file."
    )
