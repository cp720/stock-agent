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

# --- Trade Journal (graceful degradation) ---
try:
    from trade_journal import (
        initialize_db, TradeDecision, SignalSnapshot,
        OpenPosition, EquitySnapshot, close_oldest_position
    )
    JOURNAL_ENABLED = True
except ImportError:
    JOURNAL_ENABLED = False
    logger.warning("Trade journal not available — trade logging disabled.")

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
        webhook_result = f"Successfully sent {action} recommendation for {ticker} to n8n."
    except Exception as e:
        webhook_result = f"Failed to send notification: {str(e)}"

    # --- Auto-log trade decision to journal ---
    if JOURNAL_ENABLED:
        try:
            initialize_db()

            # Parse filled_price to float
            fp = None
            if filled_price and filled_price not in ("", "pending"):
                try:
                    fp = float(filled_price)
                except (ValueError, TypeError):
                    fp = None

            # Account snapshot
            acct_equity = acct_buying_power = acct_cash = None
            try:
                account = alpaca_trading_client.get_account()
                acct_equity = float(account.equity)
                acct_buying_power = float(account.buying_power)
                acct_cash = float(account.cash)
            except Exception:
                pass

            decision = TradeDecision.create(
                ticker=ticker,
                action=action.upper(),
                quantity=quantity,
                execution_status=execution_status,
                order_id=order_id or "",
                filled_price=fp,
                filled_qty=quantity if execution_status == "executed" else None,
                execution_note=execution_note or "",
                equity=acct_equity,
                buying_power=acct_buying_power,
                cash=acct_cash,
                thesis=thesis,
            )

            # Position lifecycle
            if execution_status == "executed" and fp is not None:
                if action.upper() == "BUY":
                    OpenPosition.create(
                        ticker=ticker,
                        status='open',
                        entry_date=datetime.now(timezone.utc),
                        entry_price=fp,
                        entry_qty=quantity,
                        entry_decision=decision,
                    )
                elif action.upper() == "SELL":
                    close_oldest_position(ticker, fp, quantity, decision)

            # Equity snapshot
            if acct_equity is not None:
                EquitySnapshot.create(
                    equity=acct_equity,
                    cash=acct_cash,
                    buying_power=acct_buying_power,
                )

            logger.info("Trade decision logged: %s %s %s (status: %s, id: %d)",
                        action, quantity, ticker, execution_status, decision.id)
        except Exception as e:
            logger.error("Failed to log trade decision: %s", e)

    return webhook_result

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


# --- Trade Journal Signal Attribution Tool ---

@tool(show_result=True)
def log_trade_signals(
    ticker: str,
    overall_signal: str = "",
    signal_confidence: str = "",
    rsi_value: float = 0.0,
    rsi_signal: str = "",
    momentum_pct: float = 0.0,
    momentum_signal: str = "",
    macd_crossover: str = "",
    price_vs_sma_20: str = "",
    price_vs_sma_50: str = "",
    price_vs_vwap: str = "",
    adx_value: float = 0.0,
    adx_direction: str = "",
    bb_signal: str = "",
    bb_squeeze: bool = False,
    bb_percent_b: float = 0.0,
    obv_trend: str = "",
    obv_divergence: str = "",
    stoch_signal: str = "",
    rsi_divergence: str = "",
    macd_divergence: str = "",
    reversal_alert: str = "",
    reversal_factors: str = "",
    technical_price: float = 0.0,
    fundamental_score: int = 0,
    fundamental_key_metric: str = "",
    news_sentiment: str = "",
    critical_risk: bool = False,
    news_summary: str = "",
) -> str:
    """
    Log signal attribution data for the most recent trade decision on this ticker.
    Call this AFTER send_n8n_notification to attach signal data for performance analysis.

    Args:
        ticker: The stock ticker this signal data belongs to.
        overall_signal: Technical overall signal (Bullish/Bearish/Neutral).
        signal_confidence: Signal confidence from ADX (High/Moderate/Low).
        rsi_value: RSI-14 numeric value.
        rsi_signal: RSI interpretation (Oversold/Overbought/Neutral).
        momentum_pct: 10-day ROC percentage.
        momentum_signal: Momentum interpretation (Positive/Negative).
        macd_crossover: MACD crossover status (Bullish/Bearish).
        price_vs_sma_20: Price vs SMA-20 (Above/Below).
        price_vs_sma_50: Price vs SMA-50 (Above/Below).
        price_vs_vwap: Price vs VWAP-20 (Above/Below).
        adx_value: ADX numeric value.
        adx_direction: ADX directional bias (Bullish/Bearish).
        bb_signal: Bollinger Band signal (Overbought/Oversold/Neutral).
        bb_squeeze: Whether BB squeeze is active.
        bb_percent_b: BB %B numeric value.
        obv_trend: OBV trend (Rising/Falling).
        obv_divergence: OBV divergence (Bullish/Bearish/None).
        stoch_signal: Stochastic signal (Overbought/Oversold/Neutral).
        rsi_divergence: RSI divergence status.
        macd_divergence: MACD divergence status.
        reversal_alert: Reversal alert status.
        reversal_factors: Comma-separated reversal factors.
        technical_price: Price from the Technical Analyst.
        fundamental_score: Fundamental score (1-10).
        fundamental_key_metric: Key metric driving the score.
        news_sentiment: News sentiment (Positive/Negative/Neutral/Mixed).
        critical_risk: Whether a critical risk event was flagged.
        news_summary: News summary text.
    Returns:
        str: Confirmation that signals were logged.
    """
    if not JOURNAL_ENABLED:
        return "Trade journal not available — signals not logged."

    try:
        initialize_db()

        decision = (TradeDecision
                    .select()
                    .where(TradeDecision.ticker == ticker)
                    .order_by(TradeDecision.timestamp.desc())
                    .first())

        if not decision:
            return f"No trade decision found for {ticker} — signals not logged."

        SignalSnapshot.create(
            decision=decision,
            overall_signal=overall_signal or None,
            signal_confidence=signal_confidence or None,
            rsi_value=rsi_value if rsi_value != 0.0 else None,
            rsi_signal=rsi_signal or None,
            momentum_pct=momentum_pct if momentum_pct != 0.0 else None,
            momentum_signal=momentum_signal or None,
            macd_crossover=macd_crossover or None,
            price_vs_sma_20=price_vs_sma_20 or None,
            price_vs_sma_50=price_vs_sma_50 or None,
            price_vs_vwap=price_vs_vwap or None,
            adx_value=adx_value if adx_value != 0.0 else None,
            adx_direction=adx_direction or None,
            bb_signal=bb_signal or None,
            bb_squeeze=bb_squeeze,
            bb_percent_b=bb_percent_b if bb_percent_b != 0.0 else None,
            obv_trend=obv_trend or None,
            obv_divergence=obv_divergence or None,
            stoch_signal=stoch_signal or None,
            rsi_divergence=rsi_divergence or None,
            macd_divergence=macd_divergence or None,
            reversal_alert=reversal_alert or None,
            reversal_factors=reversal_factors or None,
            technical_price=technical_price if technical_price != 0.0 else None,
            fundamental_score=fundamental_score if fundamental_score != 0 else None,
            fundamental_key_metric=fundamental_key_metric or None,
            news_sentiment=news_sentiment or None,
            critical_risk=critical_risk,
            news_summary=news_summary or None,
        )

        return f"Signal attribution logged for {ticker} (decision #{decision.id})."

    except Exception as e:
        logger.error("Failed to log signals for %s: %s", ticker, e)
        return f"Failed to log signals: {str(e)}"


# --- The Team ---
trading_team = Team(
    name="Portfolio Management Team",
    role="Chief Investment Team responsible for making informed trading decisions based on the combined insights of technical and fundamental analysis.",
    members=[fundamental_analyst_agent, technical_analyst_agent, market_news_analyst_agent],
    tools=[send_n8n_notification, get_account_balance, get_portfolio_positions, execute_trade, log_trade_signals],
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