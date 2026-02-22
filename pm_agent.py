import requests
from config import ALPACA_TRADING_KEY, ALPACA_TRADING_SECRET, OPENAI_API_KEY, N8N_WEBHOOK_URL
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from agno.tools import tool
from alpaca.trading.client import TradingClient
from datetime import datetime
from fundamental_analyst import fundamental_analyst_agent
from technical_analyst import technical_analyst_agent


# initialize Alpaca trading client
alpaca_trading_client = TradingClient(ALPACA_TRADING_KEY, ALPACA_TRADING_SECRET, paper=True)

# --- Notification Tool ---
@tool(show_result=True)
def send_n8n_notification(ticker: str, action: str, quantity: int, thesis: str, in_portfolio: bool):
    """
    REQUIRED FINAL STEP: Use this tool to execute the recommendation.
    This is the only way the user receives the trade alert.
    You MUST call this for every recommendation, including HOLD.

    Args:
        ticker (str): The stock symbol (e.g., 'AAPL').
        action (str): The recommended action ('BUY', 'SELL', or 'HOLD').
        quantity (int): The number of shares to trade (0 for HOLD).
        thesis (str): A 2-3 sentence explanation of the technical and fundamental reasoning.
        in_portfolio (bool): Whether the stock is currently held in the portfolio.
    Returns:
        str: A confirmation message indicating the notification was sent.
    """
    payload = {
        "ticker": ticker,
        "action": action,
        "quantity": quantity,
        "thesis": thesis,
        "in_portfolio": in_portfolio,
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


# --- The Team ---
trading_team = Team(
    name="Portfolio Management Team",
    role="Chief Investment Team responsible for making informed trading decisions based on the combined insights of technical and fundamental analysis.",
    members=[fundamental_analyst_agent, technical_analyst_agent],
    tools=[send_n8n_notification, get_account_balance, get_portfolio_positions],
    model=OpenAIChat(id="gpt-4.1", temperature=0.3, api_key=OPENAI_API_KEY),
    add_member_tools_to_context=True,
    add_datetime_to_context=True,
    markdown=True,
    # --- Instructions for the team ---
    instructions=[
        "### Phase 1: Investigation",
        "Request the Technical and Fundamental reports from your members for the given ticker.",
        
        "### Phase 2: Live Verification",
        "Call 'get_account_balance' and 'get_portfolio_positions' to see current funds and holdings.",
        
        "### Phase 3: Risk Math",
        "If Technical is 'Bullish' and Fundamental > 7, calculate BUY quantity.",
        "Use the formula: $$S = \\frac{E \\times 0.10}{P}$$ to calculate the number of shares to buy (S), where you risk only 10% of your total equity (E) at the current price (P).",
        "If Technical is 'Bearish' and Fundamental < 4, calculate SELL quantity if the stock is currently held. Use the same formula but based on the current position size instead of equity.",
        "(Where S=Shares, E=Total Equity, P=Current Price).",
        "If the action is HOLD, still call send_n8n_notification with action='HOLD' and quantity=0.",
        
        "### Phase 4: Final Action",
        "You MUST call 'send_n8n_notification' with the final recommendation.",
        "Include a 'thesis' summarizing the indicator results and the fundamental score."
    ],
)

if __name__ == "__main__":
    trading_team.print_response("Analyze PSTG. I want to know if I should buy it?", stream=True)