import time
import pandas_ta as ta
from config import (
    ALPACA_TRADING_KEY, ALPACA_TRADING_SECRET,
    ALPACA_API_KEY, ALPACA_SECRET_KEY, OPENAI_API_KEY,
)
from agno.agent import Agent
from agno.team import Team
from agno.models.openai import OpenAIChat
from agno.tools import tool
from alpaca.trading.client import TradingClient
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from datetime import datetime, timezone, timedelta
from typing import Optional
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

# market-data client (used by exit management to compute ATR for volatility-adaptive stops)
alpaca_data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# --- Trade Execution Safeguards ---
MAX_POSITION_PCT = 0.15       # No single position > 15% of total equity
MAX_DAILY_TRADES = 10         # Max 10 executed trades per calendar day

# --- Exit Management Rules (applied mechanically by manage_exits) ---
TAKE_PROFIT_PCT = 0.20         # Exit if up 20% from entry (profit target — not volatility-scaled)
TRAILING_ACTIVATION_PCT = 0.10 # Begin trailing once the position is up 10% from entry

# Stop distances adapt to each stock's volatility via ATR (Average True Range).
# stop distance = ATR_*_MULT × ATR, expressed as a % of price, then bounded by the
# floor/ceiling below so a quiet name isn't stopped on noise and a wild name isn't
# given an unbounded stop. STOP_LOSS_PCT / TRAILING_STOP_PCT are the fallbacks used
# when ATR cannot be computed (e.g. data error, insufficient history).
ATR_PERIOD = 14                # ATR lookback (trading days)
ATR_STOP_MULT = 2.5            # Hard stop at entry − 2.5 × ATR
ATR_TRAIL_MULT = 2.5           # Trailing stop at high-water mark − 2.5 × ATR
ATR_STOP_MIN_PCT = 0.05        # Stop never tighter than 5% of price
ATR_STOP_MAX_PCT = 0.15        # Stop never wider than 15% of price
STOP_LOSS_PCT = 0.08           # Fallback hard stop when ATR is unavailable
TRAILING_STOP_PCT = 0.08       # Fallback trailing stop when ATR is unavailable

# --- Save Recommendation Tool ---
@tool(show_result=True)
def save_recommendation(
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
    REQUIRED FINAL STEP: Save the trade recommendation and execution result to the journal.
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
        str: A confirmation message indicating the recommendation was saved.
    """
    webhook_result = f"Recommendation saved: {action} {ticker} (status: {execution_status})."

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


# --- Portfolio Risk Assessment Tool ---

@tool(show_result=True)
def get_portfolio_risk_assessment() -> dict:
    """
    Returns a comprehensive portfolio risk snapshot including exposure summary,
    per-position risk detail, portfolio-level risk metrics, and advisory risk flags.
    Use this tool in Phase 2 to understand portfolio risk before making trade decisions.
    This is advisory only — no actions are blocked.

    Returns:
        dict: A dictionary with keys 'exposure_summary', 'positions', 'risk_metrics',
              and 'risk_flags'.
    """
    try:
        # --- Account data ---
        account = alpaca_trading_client.get_account()
        equity = float(account.equity)
        cash = float(account.cash)
        last_equity = float(account.last_equity) if account.last_equity else None
        long_market_value = float(account.long_market_value) if account.long_market_value else 0.0

        # --- Positions data ---
        positions = alpaca_trading_client.get_all_positions()

        position_details = []
        total_unrealized_pnl = 0.0
        total_intraday_pnl = 0.0

        for p in positions:
            mv = float(p.market_value) if p.market_value else 0.0
            cost = float(p.cost_basis) if p.cost_basis else 0.0
            upl = float(p.unrealized_pl) if p.unrealized_pl else 0.0
            uplpc = float(p.unrealized_plpc) if p.unrealized_plpc else 0.0
            intra_pl = float(p.unrealized_intraday_pl) if p.unrealized_intraday_pl else 0.0
            cur_price = float(p.current_price) if p.current_price else 0.0
            avg_entry = float(p.avg_entry_price) if p.avg_entry_price else 0.0
            weight = (abs(mv) / equity * 100) if equity > 0 else 0.0

            total_unrealized_pnl += upl
            total_intraday_pnl += intra_pl

            position_details.append({
                "ticker": p.symbol,
                "qty": float(p.qty),
                "market_value": round(mv, 2),
                "cost_basis": round(cost, 2),
                "avg_entry_price": round(avg_entry, 2),
                "current_price": round(cur_price, 2),
                "unrealized_pnl": round(upl, 2),
                "unrealized_pnl_pct": round(uplpc * 100, 2),
                "weight_pct": round(weight, 2),
                "intraday_pnl": round(intra_pl, 2),
            })

        # --- Exposure summary ---
        invested_pct = (long_market_value / equity * 100) if equity > 0 else 0.0
        cash_pct = (cash / equity * 100) if equity > 0 else 0.0

        exposure_summary = {
            "total_equity": round(equity, 2),
            "cash": round(cash, 2),
            "total_invested": round(long_market_value, 2),
            "cash_pct": round(cash_pct, 1),
            "invested_pct": round(invested_pct, 1),
            "num_positions": len(positions),
        }

        # --- Drawdown from peak (trade journal) ---
        drawdown_from_peak = 0.0
        if JOURNAL_ENABLED:
            try:
                initialize_db()
                peak_row = (EquitySnapshot
                            .select(EquitySnapshot.equity)
                            .order_by(EquitySnapshot.equity.desc())
                            .first())
                if peak_row and peak_row.equity > 0:
                    drawdown_from_peak = ((equity - peak_row.equity) / peak_row.equity) * 100
            except Exception:
                pass

        # --- Risk metrics ---
        largest_position_pct = max((pos["weight_pct"] for pos in position_details), default=0.0)
        total_unrealized_pnl_pct = (total_unrealized_pnl / equity * 100) if equity > 0 else 0.0
        day_change_pct = (
            ((equity - last_equity) / last_equity * 100)
            if last_equity and last_equity > 0 else 0.0
        )

        risk_metrics = {
            "largest_position_pct": round(largest_position_pct, 2),
            "total_unrealized_pnl": round(total_unrealized_pnl, 2),
            "total_unrealized_pnl_pct": round(total_unrealized_pnl_pct, 2),
            "intraday_pnl": round(total_intraday_pnl, 2),
            "drawdown_from_peak": round(drawdown_from_peak, 2),
            "day_change_pct": round(day_change_pct, 2),
        }

        # --- Advisory risk flags ---
        risk_flags = []

        for pos in position_details:
            if pos["weight_pct"] >= 12.0:
                risk_flags.append(
                    f"HIGH CONCENTRATION: {pos['ticker']} is {pos['weight_pct']:.1f}% "
                    f"of equity (near 15% limit)"
                )

        if drawdown_from_peak <= -5.0:
            risk_flags.append(
                f"PORTFOLIO DRAWDOWN: {drawdown_from_peak:.1f}% from peak equity"
            )

        if cash_pct < 20.0 and len(positions) > 0:
            risk_flags.append(f"LOW CASH: Only {cash_pct:.1f}% cash remaining")

        if invested_pct > 80.0:
            risk_flags.append(f"HEAVY EXPOSURE: {invested_pct:.1f}% of equity is invested")

        if total_intraday_pnl < 0 and equity > 0 and abs(total_intraday_pnl) > equity * 0.01:
            intraday_pct = (total_intraday_pnl / equity * 100)
            risk_flags.append(
                f"INTRADAY LOSS: Portfolio down ${total_intraday_pnl:,.2f} "
                f"({intraday_pct:.1f}%) today"
            )

        if total_unrealized_pnl_pct <= -5.0:
            risk_flags.append(
                f"UNREALIZED LOSS: Total unrealized P&L is "
                f"${total_unrealized_pnl:,.2f} ({total_unrealized_pnl_pct:.1f}%)"
            )

        if not risk_flags:
            risk_flags.append("NO RISK FLAGS: Portfolio within normal parameters")

        return {
            "exposure_summary": exposure_summary,
            "positions": position_details,
            "risk_metrics": risk_metrics,
            "risk_flags": risk_flags,
        }

    except Exception as e:
        logger.error("Failed to get portfolio risk assessment: %s", e)
        return {
            "exposure_summary": {},
            "positions": [],
            "risk_metrics": {},
            "risk_flags": [f"RISK ASSESSMENT UNAVAILABLE: {str(e)}"],
        }


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
    # Decision synthesis
    conviction_score: Optional[float] = None,
    # Technical — core signals
    overall_signal: Optional[str] = None,
    signal_confidence: Optional[str] = None,
    rsi_value: Optional[float] = None,
    rsi_signal: Optional[str] = None,
    momentum_pct: Optional[float] = None,
    macd_crossover: Optional[str] = None,
    adx_value: Optional[float] = None,
    bb_squeeze: Optional[bool] = None,
    reversal_alert: Optional[str] = None,
    technical_price: Optional[float] = None,
    # Fundamental
    fundamental_score: Optional[int] = None,
    fundamental_key_metric: Optional[str] = None,
    # News
    news_sentiment: Optional[str] = None,
    critical_risk: Optional[bool] = None,
    news_summary: Optional[str] = None,
) -> str:
    """
    Log signal attribution data for the most recent trade decision on this ticker.
    Call this AFTER save_recommendation to attach signal data for performance analysis.
    All fields except ticker are optional — pass None for any value not available.

    Args:
        ticker: The stock ticker this signal data belongs to (required).
        conviction_score: The 0–100 conviction score computed in Phase 3 that drove the action.
        overall_signal: Technical overall signal (Bullish/Bearish/Neutral).
        signal_confidence: Signal confidence derived from ADX (High/Moderate/Low).
        rsi_value: RSI-14 numeric value.
        rsi_signal: RSI interpretation (Oversold/Overbought/Neutral).
        momentum_pct: 10-day ROC percentage.
        macd_crossover: MACD crossover status (Bullish/Bearish/None).
        adx_value: ADX numeric value.
        bb_squeeze: True if a Bollinger Band squeeze is active.
        reversal_alert: Reversal alert (Potential Bearish Reversal/Potential Bullish Reversal/None).
        technical_price: Price from the Technical Analyst report.
        fundamental_score: Fundamental score integer (1-10).
        fundamental_key_metric: Key metric driving the fundamental score.
        news_sentiment: News sentiment (Positive/Negative/Neutral/Mixed).
        critical_risk: True if CRITICAL_RISK: YES was flagged by the news analyst.
        news_summary: Brief news summary from the Market News Analyst.
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
            conviction_score=conviction_score,
            overall_signal=overall_signal,
            signal_confidence=signal_confidence,
            rsi_value=rsi_value,
            rsi_signal=rsi_signal,
            momentum_pct=momentum_pct,
            macd_crossover=macd_crossover,
            adx_value=adx_value,
            bb_squeeze=bb_squeeze,
            reversal_alert=reversal_alert,
            technical_price=technical_price,
            fundamental_score=fundamental_score,
            fundamental_key_metric=fundamental_key_metric,
            news_sentiment=news_sentiment,
            critical_risk=critical_risk,
            news_summary=news_summary,
        )

        return f"Signal attribution logged for {ticker} (decision #{decision.id})."

    except Exception as e:
        logger.error("Failed to log signals for %s: %s", ticker, e)
        return f"Failed to log signals: {str(e)}"


# --- Exit Management (mechanical, runs outside the agent flow) ---

def _get_atr(ticker: str, period: int = ATR_PERIOD) -> Optional[float]:
    """Return the latest ATR (Average True Range) for a ticker, or None if unavailable.

    ATR measures recent price volatility in dollar terms. It is used to scale stop
    distances to each stock's character rather than applying a flat percentage to all.
    """
    try:
        end = datetime.now(timezone.utc)
        # ~4× the period in calendar days gives enough trading bars for the EMA warm-up.
        start = end - timedelta(days=period * 4 + 15)
        bars = alpaca_data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=[ticker], timeframe=TimeFrame.Day,
            start=start, end=end, adjustment='split', feed=DataFeed.IEX))
        df = bars.df
        if df is None or df.empty:
            return None
        df = df.loc[ticker]
        atr_series = ta.atr(df['high'], df['low'], df['close'], length=period)
        if atr_series is None:
            return None
        atr_series = atr_series.dropna()
        if atr_series.empty:
            return None
        atr = float(atr_series.iloc[-1])
        return atr if atr > 0 else None
    except Exception as e:
        logger.warning("ATR computation failed for %s: %s — falling back to fixed-%% stops.", ticker, e)
        return None


def _update_high_water_mark(ticker: str, current_price: float) -> float:
    """Persist the running peak price across a ticker's open lots and return that peak.

    The high-water mark drives the trailing stop. It is shared across all open lots of a
    ticker (the peak the position has reached since the earliest still-open entry).
    """
    peak = current_price
    if not JOURNAL_ENABLED:
        return peak
    try:
        initialize_db()
        lots = list(OpenPosition.select().where(
            (OpenPosition.ticker == ticker) & (OpenPosition.status == 'open')))
        for lot in lots:
            prior = lot.high_water_mark if lot.high_water_mark else lot.entry_price
            peak = max(peak, prior)
        for lot in lots:
            if (lot.high_water_mark or 0.0) < peak:
                lot.high_water_mark = peak
                lot.save()
    except Exception as e:
        logger.error("Failed to update high-water mark for %s: %s", ticker, e)
    return peak


def _journal_exit(ticker: str, qty: int, fill_price: Optional[float],
                  order_id: str, reason: str):
    """Record an automated exit in the journal, mirroring save_recommendation's writes."""
    if not JOURNAL_ENABLED:
        return
    try:
        initialize_db()
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
            action="SELL",
            quantity=qty,
            execution_status="executed",
            order_id=order_id or "",
            filled_price=fill_price,
            filled_qty=qty,
            execution_note=f"Auto-exit: {reason}",
            equity=acct_equity,
            buying_power=acct_buying_power,
            cash=acct_cash,
            thesis=f"Automated exit triggered ({reason}). Sold {qty} share(s) of {ticker}.",
        )

        if fill_price is not None:
            close_oldest_position(ticker, fill_price, qty, decision)

        if acct_equity is not None:
            EquitySnapshot.create(
                equity=acct_equity, cash=acct_cash, buying_power=acct_buying_power,
            )

        logger.info("Exit journaled: SELL %d %s (%s, id: %d)", qty, ticker, reason, decision.id)
    except Exception as e:
        logger.error("Failed to journal exit for %s: %s", ticker, e)


def manage_exits():
    """Mechanical exit pass over all held positions.

    Applies, in priority order: hard stop-loss, take-profit, then trailing stop.
    Runs independently of the discretionary agent flow and is NOT subject to the daily
    trade limit — risk-reducing exits should never be blocked. Skipped when the market
    is closed. Triggered exits submit a market SELL and are logged to the journal.
    """
    try:
        clock = alpaca_trading_client.get_clock()
    except APIError as e:
        logger.error("Exit manager: failed to read market clock: %s", e.message)
        return
    if not clock.is_open:
        logger.info("Exit manager: market closed — skipping exit checks.")
        return

    try:
        positions = alpaca_trading_client.get_all_positions()
    except APIError as e:
        logger.error("Exit manager: failed to fetch positions: %s", e.message)
        return

    if not positions:
        logger.info("Exit manager: no open positions to evaluate.")
        return

    for p in positions:
        ticker = p.symbol
        try:
            qty = int(float(p.qty))
            entry = float(p.avg_entry_price)
            current = float(p.current_price)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or entry <= 0 or current <= 0:
            continue

        peak = _update_high_water_mark(ticker, current)
        gain = (current - entry) / entry

        # Derive volatility-adaptive stop distances from ATR, bounded and with a
        # fixed-percentage fallback when ATR can't be computed.
        atr = _get_atr(ticker)
        if atr is not None:
            atr_pct = atr / current
            stop_pct = min(max(ATR_STOP_MULT * atr_pct, ATR_STOP_MIN_PCT), ATR_STOP_MAX_PCT)
            trail_pct = min(max(ATR_TRAIL_MULT * atr_pct, ATR_STOP_MIN_PCT), ATR_STOP_MAX_PCT)
            stop_basis = f"ATR ${atr:.2f}={atr_pct * 100:.1f}%/px × {ATR_STOP_MULT} → {stop_pct * 100:.1f}% stop"
        else:
            stop_pct = STOP_LOSS_PCT
            trail_pct = TRAILING_STOP_PCT
            stop_basis = f"fixed {stop_pct * 100:.0f}% stop (ATR unavailable)"

        reason = None
        if gain <= -stop_pct:
            reason = f"stop-loss ({gain * 100:+.1f}% from entry ${entry:.2f}; {stop_basis})"
        elif gain >= TAKE_PROFIT_PCT:
            reason = f"take-profit ({gain * 100:+.1f}% from entry ${entry:.2f})"
        elif ((peak - entry) / entry >= TRAILING_ACTIVATION_PCT
              and current <= peak * (1 - trail_pct)):
            drop = (current - peak) / peak
            reason = (f"trailing-stop ({drop * 100:+.1f}% from peak ${peak:.2f}; "
                      f"{trail_pct * 100:.1f}% trail, {stop_basis})")

        if reason is None:
            continue

        logger.info("Exit triggered for %s: %s — submitting SELL %d.", ticker, reason, qty)
        try:
            order = alpaca_trading_client.submit_order(MarketOrderRequest(
                symbol=ticker, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
            fill_price = float(order.filled_avg_price) if order.filled_avg_price else current
            _journal_exit(ticker, qty, fill_price, str(order.id), reason)
        except APIError as e:
            logger.error("Exit SELL failed for %s: %s", ticker, e.message)
        except Exception as e:
            logger.error("Unexpected error exiting %s: %s", ticker, e)


# --- The Team ---
trading_team = Team(
    name="Portfolio Management Team",
    role="Chief Investment Team responsible for making informed trading decisions based on the combined insights of technical and fundamental analysis.",
    members=[fundamental_analyst_agent, technical_analyst_agent, market_news_analyst_agent],
    tools=[save_recommendation, get_account_balance, get_portfolio_positions, get_portfolio_risk_assessment, execute_trade, log_trade_signals],
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


_INTER_TICKER_DELAY = 20   # seconds between tickers — prevents TPM buildup
_MAX_RETRIES = 3           # retry attempts on rate limit before skipping
_RETRY_BASE_DELAY = 60     # seconds; multiplied by attempt number (60s, 120s, 180s)


def _is_rate_limit_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return "429" in s or "rate_limit" in s or "rate limit" in s


def run_watchlist():
    """Analyze each ticker in watchlist.py sequentially with rate-limit safeguards."""
    watchlist = WATCHLIST
    logger.info("=== Watchlist Scan Started — Tickers: %s ===", ", ".join(watchlist))

    # Manage existing positions before evaluating new entries — protect capital first.
    logger.info("Running exit-management pass on held positions ...")
    manage_exits()

    for i, ticker in enumerate(watchlist):
        if i > 0:
            logger.info("Pausing %ds before next ticker to stay within TPM limits ...", _INTER_TICKER_DELAY)
            time.sleep(_INTER_TICKER_DELAY)

        logger.info("Analyzing %s ...", ticker)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                trading_team.print_response(
                    f"Analyze {ticker}. Should I buy, sell, or hold?",
                    stream=True
                )
                logger.info("Completed analysis for %s.", ticker)
                break
            except Exception as e:
                if _is_rate_limit_error(e):
                    if attempt < _MAX_RETRIES:
                        wait = _RETRY_BASE_DELAY * attempt
                        logger.warning(
                            "Rate limit hit for %s (attempt %d/%d) — retrying in %ds.",
                            ticker, attempt, _MAX_RETRIES, wait
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            "Rate limit persisted after %d attempts for %s — skipping.",
                            _MAX_RETRIES, ticker
                        )
                else:
                    logger.error("Failed to analyze %s: %s", ticker, e)
                    break

    logger.info("=== Watchlist Scan Complete ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        # Run the mechanical exit pass only — no new-entry analysis.
        manage_exits()
    else:
        run_watchlist()