import pandas as pd
import pandas_ta as ta
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, OPENAI_API_KEY
from typing import List
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from agno.agent import Agent
from agno.tools import tool # Import decorator
from agno.models.openai import OpenAIChat
from instructions.technical_instructions import TECHNICAL_INSTRUCTIONS
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

# --- Technical Analyst Agent ---

# Initialize Alpaca client
alpaca_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)

# Define output structure for technical indicators
class TechnicalIndicatorResults(BaseModel):
    symbol: str = Field(..., description="The stock ticker symbol")
    price: float = Field(..., description="Current market price in USD")

    # --- RSI ---
    rsi_value: float = Field(..., description="The calculated RSI (14-period)")
    rsi_signal: str = Field(..., description="Interpretation: Oversold (<30), Overbought (>70), or Neutral")

    # --- Momentum (ROC) ---
    momentum_pct: float = Field(..., description="The 10-day Rate of Change percentage")
    momentum_signal: str = Field(..., description="Interpretation: Positive or Negative momentum")

    # --- MACD (12, 26, 9) ---
    macd_line: float = Field(..., description="MACD line value (EMA12 - EMA26)")
    macd_signal_line: float = Field(..., description="Signal line value (9-period EMA of MACD line)")
    macd_histogram: float = Field(..., description="Histogram value (MACD line - Signal line)")
    macd_crossover: str = Field(..., description="Bullish (MACD above signal) or Bearish (MACD below signal)")

    # --- Moving Averages ---
    sma_20: float = Field(..., description="20-day Simple Moving Average")
    sma_50: float = Field(..., description="50-day Simple Moving Average")
    price_vs_sma_20: str = Field(..., description="Whether price is Above or Below the 20-day SMA")
    price_vs_sma_50: str = Field(..., description="Whether price is Above or Below the 50-day SMA")

    # --- Synthesized Signal ---
    overall_signal: str = Field(
        ...,
        description=(
            "Synthesized verdict across all indicators: Bullish, Bearish, or Neutral. "
            "Bullish = 4+ of 5 indicators are bullish. Bearish = 4+ of 5 are bearish. Neutral = mixed."
        )
    )


@tool(show_result=True)
def get_technical_indicators(symbols: List[str]) -> List[TechnicalIndicatorResults]:
    """
    Fetches historical price data for the given stock symbols and calculates
    RSI, Momentum (ROC), MACD, and Moving Averages (SMA-20, SMA-50).
    Returns a synthesized overall signal (Bullish/Bearish/Neutral) for each symbol.

    Args:
        symbols (List[str]): A list of stock ticker symbols to analyze.
    Returns:
        List[TechnicalIndicatorResults]: Full technical indicator results per symbol.
    """

    # 150 calendar days (~105 trading days) gives SMA-50 and MACD(26) a safe warm-up buffer
    end_date = datetime.now()
    start_date = end_date - timedelta(days=150)

    request_params = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start_date,
        end=end_date,
        adjustment='split'
    )

    bars_response = alpaca_client.get_stock_bars(request_params)
    bars_df = bars_response.df

    results = []

    for symbol in symbols:
        try:
            df = bars_df.loc[symbol].copy()

            # --- RSI (14) ---
            df['RSI'] = ta.rsi(df['close'], length=14)

            # --- Momentum / ROC (10) ---
            df['Momentum'] = ta.roc(df['close'], length=10)

            # --- MACD (12, 26, 9) ---
            macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
            df['MACD_line']   = macd_df['MACD_12_26_9']
            df['MACD_signal'] = macd_df['MACDs_12_26_9']
            df['MACD_hist']   = macd_df['MACDh_12_26_9']

            # --- Moving Averages ---
            df['SMA_20'] = ta.sma(df['close'], length=20)
            df['SMA_50'] = ta.sma(df['close'], length=50)

            latest = df.iloc[-1]
            latest_price  = latest['close']
            rsi_value     = latest['RSI']
            momentum_pct  = latest['Momentum']
            macd_line     = latest['MACD_line']
            macd_signal   = latest['MACD_signal']
            macd_hist     = latest['MACD_hist']
            sma_20        = latest['SMA_20']
            sma_50        = latest['SMA_50']

            # Guard against NaN (insufficient warm-up data)
            required = [rsi_value, momentum_pct, macd_line, macd_signal, macd_hist, sma_20, sma_50]
            if any(pd.isna(v) for v in required):
                print(f"Warning: Insufficient data to compute all indicators for {symbol}. Skipping.")
                continue

            # --- Interpret each indicator ---
            rsi_signal      = "Oversold" if rsi_value < 30 else "Overbought" if rsi_value > 70 else "Neutral"
            momentum_signal = "Positive" if momentum_pct > 0 else "Negative"
            macd_crossover  = "Bullish" if macd_line > macd_signal else "Bearish"
            price_vs_sma_20 = "Above" if latest_price > sma_20 else "Below"
            price_vs_sma_50 = "Above" if latest_price > sma_50 else "Below"

            # --- Synthesize overall signal (score out of 5) ---
            # Each indicator casts one bullish or bearish vote
            bullish_votes = sum([
                rsi_signal != "Overbought",          # RSI not overbought = bullish
                momentum_signal == "Positive",        # Positive momentum = bullish
                macd_crossover == "Bullish",          # MACD above signal = bullish
                price_vs_sma_20 == "Above",           # Price above SMA-20 = bullish
                price_vs_sma_50 == "Above",           # Price above SMA-50 = bullish
            ])
            bearish_votes = 5 - bullish_votes

            if bullish_votes >= 4:
                overall_signal = "Bullish"
            elif bearish_votes >= 4:
                overall_signal = "Bearish"
            else:
                overall_signal = "Neutral"

            results.append(TechnicalIndicatorResults(
                symbol=symbol,
                price=round(latest_price, 2),
                rsi_value=round(rsi_value, 2),
                rsi_signal=rsi_signal,
                momentum_pct=round(momentum_pct, 4),
                momentum_signal=momentum_signal,
                macd_line=round(macd_line, 4),
                macd_signal_line=round(macd_signal, 4),
                macd_histogram=round(macd_hist, 4),
                macd_crossover=macd_crossover,
                sma_20=round(sma_20, 2),
                sma_50=round(sma_50, 2),
                price_vs_sma_20=price_vs_sma_20,
                price_vs_sma_50=price_vs_sma_50,
                overall_signal=overall_signal,
            ))

        except KeyError:
            print(f"Error: No data returned for symbol '{symbol}'. It may be an invalid ticker.")
        except Exception as e:
            print(f"Error processing {symbol}: {e}")

    return results

model = OpenAIChat(id="gpt-4.1", temperature=0.2, api_key=OPENAI_API_KEY)

technical_analyst_agent = Agent(
    name="Technical Analyst",
    role="Expert Technical Stock Analyst specializing in price action, RSI, MACD, moving averages, and momentum trends.",
    model=model,
    tools=[get_technical_indicators],
    instructions=TECHNICAL_INSTRUCTIONS,
    markdown=True
)



if __name__ == "__main__":
    response = technical_analyst_agent.run("Analyze the technical indicators for ANET and APLD and provide insights on their current market conditions.")
    report = response.content
    print(report)