import os
from dotenv import load_dotenv

# --- Configuration ---
load_dotenv()

# Basic Keys - User for data retrieval
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY")
ALPACA_SECRET_KEY = os.getenv("ALPACA_API_SECRET") 

# Trading specific keys - currently paper trading only
ALPACA_TRADING_KEY = os.getenv("ALPACA_TRADING_API_KEY")
ALPACA_TRADING_SECRET = os.getenv("ALPACA_TRADING_SECRET")

ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
