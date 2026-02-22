import pandas as pd
import pandas_ta as ta
from config import ALPACA_API_KEY, ALPACA_SECRET_KEY, OPENAI_API_KEY
from typing import List
from pydantic import BaseModel, Field
from datetime import datetime, timedelta
from agno.agent import Agent
from agno.tools import tool # Import decorator
from agno.models.openai import OpenAIChat
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
    rsi_value: float = Field(..., description="The calculated RSI (14-period)")
    rsi_signal: str = Field(..., description="Interpretation: Oversold, Overbought, or Neutral")
    momentum_pct: float = Field(..., description="The 10-day Rate of Change percentage")
    momentum_signal: str = Field(..., description="Interpretation: Positive or Negative momentum")


@tool(show_result=True)
def get_technical_indicators(symbols: List[str]) -> List[TechnicalIndicatorResults]:
    """
    Fetches historical price data for the given stock symbols, calculates technical indicators (RSI and Momentum), and provides interpretations of those indicators.
    
    Args: 
        symbols (List[str]): A list of stock ticker symbols to analyze.
    Returns:
        List[TechnicalIndicatorResults]: A list of results containing the latest price, RSI value and signal, and Momentum percentage and signal for each stock symbol.
    """

    # fetch data — 90 days gives RSI-14 and ROC-10 a comfortable warm-up window
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    
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

    # Process each symbol's data
    for symbol in symbols:
        try:
            df = bars_df.loc[symbol].copy()

            # Calculate indicators
            df['RSI'] = ta.rsi(df['close'], length=14)
            df['Momentum'] = ta.roc(df['close'], length=10)

            latest = df.iloc[-1]
            latest_price = latest['close']
            rsi_value = latest['RSI']
            momentum_pct = latest['Momentum']

            # Guard against NaN values (insufficient data for indicator warm-up)
            if pd.isna(rsi_value) or pd.isna(momentum_pct):
                print(f"Warning: Insufficient data to compute indicators for {symbol}. Skipping.")
                continue

            # Interpret indicators
            rsi_signal = "Oversold" if rsi_value < 30 else "Overbought" if rsi_value > 70 else "Neutral"
            momentum_signal = "Positive" if momentum_pct > 0 else "Negative"

            results.append(TechnicalIndicatorResults(
                symbol=symbol,
                price=round(latest_price, 2),
                rsi_value=round(rsi_value, 2),
                rsi_signal=rsi_signal,
                momentum_pct=round(momentum_pct, 4),
                momentum_signal=momentum_signal
            ))
        except KeyError:
            print(f"Error: No data returned for symbol '{symbol}'. It may be an invalid ticker.")
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
    return results

model = OpenAIChat(id="gpt-4.1", temperature=0.2, api_key=OPENAI_API_KEY)

technical_analyst_agent = Agent(
    name="Technical Analyst",
    role="Expert Technical Stock Analyst specializing in price action, RSI, and momentum trends.",
    model=model,
    tools=[get_technical_indicators],
    instructions=[
        "Use the get_technical_indicators tool to analyze the provided symbols.",
        "Focus on identifying oversold/overbought conditions via RSI.",
        "Assess trend strength using the Momentum (ROC) indicator.",
        "Provide a clear, data-driven summary for each stock."
    ],
    markdown=True
)



if __name__ == "__main__":
    response = technical_analyst_agent.run("Analyze the technical indicators for ANET and B (Barrick Mining Gold Corp) and provide insights on their current market conditions.")
    report = response.content
    print(report)