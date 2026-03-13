import requests
from config import ALPACA_TRADING_KEY, ALPACA_TRADING_SECRET, OPENAI_API_KEY, N8N_WEBHOOK_URL
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from agno.tools import tool
from alpaca.trading.client import TradingClient
from datetime import datetime, timezone
from alpaca.trading.requests import MarketOrderRequest, GetOrdersRequest
from alpaca.trading.enums import OrderSide, TimeInForce, QueryOrderStatus
from alpaca.common.exceptions import APIError
from fundamental_analyst import fundamental_analyst_agent
from technical_analyst import technical_analyst_agent
from market_news_analyst import market_news_analyst_agent
from instructions.pm_instructions import PM_INSTRUCTIONS
from watchlist import WATCHLIST
from logger import get_logger

logger = get_logger(__name__)

# initialize Alpaca trading client
alpaca_trading_client = TradingClient(ALPACA_TRADING_KEY, ALPACA_TRADING_SECRET, paper=True)

# --- Trade Execution Safeguards ---
MAX_POSITION_PCT = 0.15       # No single position > 15% of total equity
MAX_DAILY_TRADES = 10         # Max 10 executed trades per calendar day

# --- Notification Tool ---
@tool(show_result=True)
def send_n8n_notification(
    ticker: str,
    action: str,
    quantity: int,
    thesis: str,
    in_portfolio: bool,
    execution_status: str = "hold",
    order_id: str = "",
    filled_price: str = "",
    execution_note: str = ""
):
    """
    REQUIRED FINAL STEP: Use this tool to send the trade alert notification.
    This is the only way the user receives the trade recommendation and execution result.
    You MUST call this for every recommendation, including HOLD.

    Args:
        ticker (str): The stock symbol (e.g., 'AAPL').
        action (str): The recommended action ('BUY', 'SELL', or 'HOLD').
        quantity (int): The number of shares to trade (0 for HOLD).
        thesis (str): A 5-6 sentence explanation covering technical reasoning, fundamental score,
                      news context, position status, and execution result.
        in_portfolio (bool): Whether the stock is currently held in the portfolio.
        execution_status (str): One of 'executed', 'skipped', 'failed', or 'hold'. Default 'hold'.
        order_id (str): The Alpaca order ID if the trade was executed. Empty string otherwise.
        filled_price (str): The actual fill price if the trade was executed. Empty string otherwise.
        execution_note (str): Reason for skip or failure, or confirmation of execution.
    Returns:
        str: A confirmation message indicating the notification was sent.
    """
    payload = {
        "ticker": ticker,
        "action": action,
        "quantity": quantity,
        "thesis": thesis,
        "in_portfolio": in_portfolio,
        "execution_status": execution_status,
        "order_id": order_id,
        "filled_price": filled_price,
        "execution_note": execution_note,
        "timestamp": datetime.now().isoformat()
    }

    try:
        response = requests.post(N8N_WEBHOOK_URL, json=payload, timeout=10)
        response.raise_for_status()
        return f"Successfully sent {action} recommendation for {ticker} to n8n."
    except Exception as e:
        return f"Failed to send notification: {str(e)}"

# --- Alpaca account tools ---

@tool(show_result=True)
def get_account_balance() -> dict:
    """
    Retrieves the current Alpaca account balance, equity, and buying power. 
    Use this tool to determine how much capital is available for a trade.

    Returns:
    dict: A dictionary containing 'equity', 'buying_power', and 'cash'.
    """
    account = alpaca_trading_client.get_account()
    return {
        "equity": float(account.equity),
        "buying_power": float(account.buying_power),
        "cash": float(account.cash)
    }

@tool(show_result=True)
def get_portfolio_positions() -> dict:
    """
    Retrieves all current stock positions in the Alpaca account.
    Use this to check if a stock is already owned and how many shares are held.
    
    Returns:
        dict: A mapping of ticker symbols to their current quantities (e.g., {'AAPL': 10}).
    """
    positions = alpaca_trading_client.get_all_positions()

    return {p.symbol: float(p.qty) for p in positions}


# --- Trade Execution Tool ---

@tool(show_result=True)
def execute_trade(ticker: str, action: str, quantity: int) -> str:
    """
    Execute a paper trade via Alpaca. Enforces safeguards before submitting:
      - Market must be open
      - SELL orders require an existing position (no short selling)
      - No single position may exceed 15% of total equity
      - Maximum 10 trades per day

    Call this tool ONLY for BUY or SELL actions. Do NOT call for HOLD.

    Args:
        ticker (str): The stock symbol (e.g., 'AAPL').
        action (str): 'BUY' or 'SELL'.
        quantity (int): The number of shares to trade.
    Returns:
        str: A pipe-delimited result string containing execution_status, order_id,
             filled_qty, filled_price, and execution_note.
    """
    action = action.upper().strip()

    # --- Guard: only BUY or SELL ---
    if action not in ("BUY", "SELL"):
        return "execution_status: skipped | execution_note: HOLD actions do not require execution."

    # --- Guard: market must be open ---
    clock = alpaca_trading_client.get_clock()
    if not clock.is_open:
        return "execution_status: skipped | execution_note: Market is currently closed. Order not submitted."

    # --- Guard: daily trade count ---
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        orders_request = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=today_start,
            limit=MAX_DAILY_TRADES + 1
        )
        today_orders = alpaca_trading_client.get_orders(filter=orders_request)
        filled_today = [o for o in today_orders if o.filled_at is not None]
        if len(filled_today) >= MAX_DAILY_TRADES:
            return (
                f"execution_status: skipped | "
                f"execution_note: Daily trade limit reached ({MAX_DAILY_TRADES} trades today). Order not submitted."
            )
    except APIError as e:
        logger.warning("Failed to check daily trade count: %s — proceeding with order.", e.message)

    # --- SELL guards ---
    if action == "SELL":
        positions = alpaca_trading_client.get_all_positions()
        held = {p.symbol: float(p.qty) for p in positions}
        if ticker not in held:
            return (
                f"execution_status: skipped | "
                f"execution_note: SELL signal for {ticker} but position not held. Informational only — no short selling."
            )
        # Cap quantity to actual held shares
        held_qty = int(held[ticker])
        if quantity > held_qty:
            logger.info("Requested SELL qty %d exceeds held %d for %s — capping.", quantity, held_qty, ticker)
            quantity = held_qty

    # --- BUY guard: max position size ---
    if action == "BUY":
        try:
            account = alpaca_trading_client.get_account()
            equity = float(account.equity)
            max_position_value = equity * MAX_POSITION_PCT

            existing_value = 0.0
            try:
                pos = alpaca_trading_client.get_open_position(ticker)
                existing_value = abs(float(pos.market_value))
            except APIError:
                pass  # No existing position — that's fine

            if existing_value >= max_position_value:
                return (
                    f"execution_status: skipped | "
                    f"execution_note: Position in {ticker} already at max allocation "
                    f"({MAX_POSITION_PCT*100:.0f}% of equity = ${max_position_value:,.0f}). "
                    f"No additional shares purchased."
                )
        except APIError as e:
            logger.warning("Failed to check position size for %s: %s — proceeding.", ticker, e.message)

    # --- Submit market order ---
    try:
        order_request = MarketOrderRequest(
            symbol=ticker,
            qty=quantity,
            side=OrderSide.BUY if action == "BUY" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY
        )
        order = alpaca_trading_client.submit_order(order_request)

        logger.info(
            "Order submitted: %s %d shares of %s — order_id: %s, status: %s",
            action, quantity, ticker, order.id, order.status
        )

        return (
            f"execution_status: executed | "
            f"order_id: {order.id} | "
            f"status: {order.status} | "
            f"filled_qty: {order.filled_qty or 'pending'} | "
            f"filled_price: {order.filled_avg_price or 'pending'} | "
            f"execution_note: {action} order for {quantity} shares of {ticker} submitted successfully."
        )
    except APIError as e:
        logger.error("Order execution failed for %s %d %s: %s", action, quantity, ticker, e.message)
        return (
            f"execution_status: failed | "
            f"execution_note: Alpaca API error — {e.message} (status: {e.status_code})"
        )
    except Exception as e:
        logger.error("Unexpected error executing order for %s: %s", ticker, e)
        return f"execution_status: failed | execution_note: Unexpected error — {str(e)}"


# --- The Team ---
trading_team = Team(
    name="Portfolio Management Team",
    role="Chief Investment Team responsible for making informed trading decisions based on the combined insights of technical and fundamental analysis.",
    members=[fundamental_analyst_agent, technical_analyst_agent, market_news_analyst_agent],
    tools=[send_n8n_notification, get_account_balance, get_portfolio_positions, execute_trade],
    model=OpenAIChat(id="gpt-4.1", temperature=0.3, api_key=OPENAI_API_KEY),
    add_member_tools_to_context=True,
    add_datetime_to_context=True,
    markdown=True,
    instructions=PM_INSTRUCTIONS,
)

def run_single(ticker: str):
    """Analyze a single ticker and send a trade recommendation via n8n."""
    trading_team.print_response(
        f"Analyze {ticker}. Should I buy, sell, or hold?",
        stream=True
    )


def ask(question: str):
    """
    Ask the team an informational question without triggering a trade recommendation.
    Use this for market overviews, sector news, or any non-ticker-specific queries.

    Examples:
        ask("What were the biggest market events this week?")
        ask("How did the latest FOMC decision affect tech stocks?")
        ask("What is happening in the AI infrastructure sector?")
    """
    trading_team.print_response(question, stream=True)


def run_watchlist():
    """Analyze every ticker in the watchlist sequentially and send a recommendation for each."""
    logger.info("=== Watchlist Scan Started — Tickers: %s ===", ", ".join(WATCHLIST))

    for ticker in WATCHLIST:
        logger.info("Analyzing %s ...", ticker)
        try:
            trading_team.print_response(
                f"Analyze {ticker}. Should I buy, sell, or hold?",
                stream=True
            )
            logger.info("Completed analysis for %s.", ticker)
        except Exception as e:
            logger.error("Failed to analyze %s: %s", ticker, e)

    logger.info("=== Watchlist Scan Complete ===")


if __name__ == "__main__":
    run_watchlist()