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
from logger import get_logger

# --- Technical Analyst Agent ---

logger = get_logger(__name__)

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

    # --- VWAP (20-day rolling) ---
    vwap_20: float = Field(..., description="20-day rolling Volume Weighted Average Price — institutional benchmark")
    price_vs_vwap: str = Field(..., description="Whether price is Above or Below the 20-day rolling VWAP")

    # --- ADX (Trend Strength + Direction) ---
    adx_value: float = Field(..., description="14-period Average Directional Index — measures trend strength (direction-neutral)")
    adx_trend_strength: str = Field(..., description="Strong Trend (ADX>25), Moderate (ADX 20–25), Ranging (ADX<20)")
    di_plus: float = Field(..., description="+DI — bullish directional movement indicator")
    di_minus: float = Field(..., description="-DI — bearish directional movement indicator")
    adx_direction: str = Field(..., description="Bullish (+DI > -DI) or Bearish (-DI > +DI) — directional bias from ADX")
    signal_confidence: str = Field(
        ...,
        description=(
            "Overall signal reliability based on ADX:\n"
            "  High   — ADX > 25: strong trend, indicator votes are highly reliable\n"
            "  Moderate — ADX 20–25: trend forming, signals are reasonably reliable\n"
            "  Low    — ADX < 20: ranging/choppy market, signals are less reliable"
        )
    )

    # --- Bollinger Bands (20-period, 2σ) ---
    bb_upper: float = Field(..., description="Upper Bollinger Band (SMA-20 + 2σ)")
    bb_lower: float = Field(..., description="Lower Bollinger Band (SMA-20 − 2σ)")
    bb_percent_b: float = Field(..., description="BB %B — where price sits within the bands (0=lower band, 1=upper band; can exceed range)")
    bb_width: float = Field(..., description="Normalized BB width = (upper − lower) / middle — low width signals volatility squeeze")
    bb_squeeze: bool = Field(..., description="True when BB width is in the bottom 20% of its 20-period range — breakout alert")
    bb_signal: str = Field(..., description="Overbought (BB%B > 0.80), Oversold (BB%B < 0.20), or Neutral")

    # --- Synthesized Signal ---
    overall_signal: str = Field(
        ...,
        description=(
            "Synthesized verdict across all 8 indicators (RSI, Momentum, MACD, SMA-20, SMA-50, VWAP, BB, ADX direction): "
            "Bullish = 5+ of 8 bullish votes, Bearish = 5+ of 8 bearish votes, Neutral = mixed. "
            "Weighted by signal_confidence — in Low-confidence (Ranging) markets, note that votes are less reliable."
        )
    )


@tool(show_result=True)
def get_technical_indicators(symbols: List[str]) -> List[TechnicalIndicatorResults]:
    """
    Fetches historical price data for the given stock symbols and calculates:
    RSI, Momentum (ROC), MACD, SMA-20, SMA-50, VWAP-20, ADX, and Bollinger Bands.
    Returns a synthesized overall signal (Bullish/Bearish/Neutral) for each symbol,
    along with signal confidence derived from ADX trend strength.

    Args:
        symbols (List[str]): A list of stock ticker symbols to analyze.
    Returns:
        List[TechnicalIndicatorResults]: Full technical indicator results per symbol.
    """

    # 200 calendar days (~140 trading days) gives ADX(14) and all indicators a safe warm-up buffer
    end_date = datetime.now()
    start_date = end_date - timedelta(days=200)

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

            # --- Rolling 20-day VWAP (institutional benchmark) ---
            # Classic VWAP resets intraday; for daily bars we use a 20-day rolling window
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            df['VWAP_20'] = (
                (typical_price * df['volume']).rolling(20).sum()
                / df['volume'].rolling(20).sum()
            )

            # --- ADX (14) — trend strength and directional bias ---
            adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
            df['ADX_14']  = adx_df['ADX_14']
            df['DI_Plus'] = adx_df['DMP_14']   # +DI
            df['DI_Minus'] = adx_df['DMN_14']  # -DI

            # --- Bollinger Bands (20-period, 2σ) ---
            bb_df = ta.bbands(df['close'], length=20, std=2)
            df['BB_Upper']     = bb_df['BBU_20_2.0']
            df['BB_Lower']     = bb_df['BBL_20_2.0']
            df['BB_Width']     = bb_df['BBW_20_2.0']   # normalized: (upper-lower)/middle
            df['BB_Percent_B'] = bb_df['BBP_20_2.0']   # 0 = lower band, 1 = upper band

            # BB Squeeze: True when current width is in the bottom 20% of its 20-period range
            bb_width_min = df['BB_Width'].rolling(20).min()
            bb_width_max = df['BB_Width'].rolling(20).max()
            bb_width_range = bb_width_max - bb_width_min
            # Avoid division by zero on perfectly flat width history
            bb_width_pct = (df['BB_Width'] - bb_width_min) / bb_width_range.replace(0, float('nan'))
            df['BB_Squeeze'] = bb_width_pct < 0.20

            latest = df.iloc[-1]
            latest_price  = latest['close']
            rsi_value     = latest['RSI']
            momentum_pct  = latest['Momentum']
            macd_line     = latest['MACD_line']
            macd_signal   = latest['MACD_signal']
            macd_hist     = latest['MACD_hist']
            sma_20        = latest['SMA_20']
            sma_50        = latest['SMA_50']
            vwap_20       = latest['VWAP_20']
            adx_value     = latest['ADX_14']
            di_plus       = latest['DI_Plus']
            di_minus      = latest['DI_Minus']
            bb_upper      = latest['BB_Upper']
            bb_lower      = latest['BB_Lower']
            bb_width      = latest['BB_Width']
            bb_percent_b  = latest['BB_Percent_B']
            bb_squeeze    = bool(latest['BB_Squeeze'])

            # Guard against NaN (insufficient warm-up data)
            required = [
                rsi_value, momentum_pct, macd_line, macd_signal, macd_hist,
                sma_20, sma_50, vwap_20, adx_value, di_plus, di_minus,
                bb_upper, bb_lower, bb_width, bb_percent_b,
            ]
            if any(pd.isna(v) for v in required):
                logger.warning("Insufficient data to compute all indicators for %s — skipping.", symbol)
                continue

            # --- Interpret each indicator ---
            rsi_signal      = "Oversold" if rsi_value < 30 else "Overbought" if rsi_value > 70 else "Neutral"
            momentum_signal = "Positive" if momentum_pct > 0 else "Negative"
            macd_crossover  = "Bullish" if macd_line > macd_signal else "Bearish"
            price_vs_sma_20 = "Above" if latest_price > sma_20 else "Below"
            price_vs_sma_50 = "Above" if latest_price > sma_50 else "Below"
            price_vs_vwap   = "Above" if latest_price > vwap_20 else "Below"

            # ADX: trend strength label and directional bias
            if adx_value > 25:
                adx_trend_strength = "Strong Trend"
                signal_confidence  = "High"
            elif adx_value >= 20:
                adx_trend_strength = "Moderate"
                signal_confidence  = "Moderate"
            else:
                adx_trend_strength = "Ranging"
                signal_confidence  = "Low"

            adx_direction = "Bullish" if di_plus > di_minus else "Bearish"

            # Bollinger Bands: where is price within the bands?
            if bb_percent_b > 0.80:
                bb_signal = "Overbought"
            elif bb_percent_b < 0.20:
                bb_signal = "Oversold"
            else:
                bb_signal = "Neutral"

            # --- Synthesize overall signal (score out of 8) ---
            # Each indicator casts one bullish or bearish vote
            bullish_votes = sum([
                rsi_signal != "Overbought",          # RSI not overbought = bullish (avoids chasing)
                momentum_signal == "Positive",        # Positive ROC = bullish
                macd_crossover == "Bullish",          # MACD above signal = bullish
                price_vs_sma_20 == "Above",           # Price above SMA-20 = short-term bullish
                price_vs_sma_50 == "Above",           # Price above SMA-50 = medium-term bullish
                price_vs_vwap == "Above",             # Price above VWAP = institutional bullish bias
                bb_percent_b > 0.50,                  # Price in upper half of BB = bullish pressure
                adx_direction == "Bullish",           # +DI > -DI = directional momentum bullish
            ])
            bearish_votes = 8 - bullish_votes

            if bullish_votes >= 5:
                overall_signal = "Bullish"
            elif bearish_votes >= 5:
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
                vwap_20=round(vwap_20, 2),
                price_vs_vwap=price_vs_vwap,
                adx_value=round(adx_value, 2),
                adx_trend_strength=adx_trend_strength,
                di_plus=round(di_plus, 2),
                di_minus=round(di_minus, 2),
                adx_direction=adx_direction,
                signal_confidence=signal_confidence,
                bb_upper=round(bb_upper, 2),
                bb_lower=round(bb_lower, 2),
                bb_percent_b=round(bb_percent_b, 4),
                bb_width=round(bb_width, 4),
                bb_squeeze=bb_squeeze,
                bb_signal=bb_signal,
                overall_signal=overall_signal,
            ))

        except KeyError:
            logger.error("No data returned for symbol '%s' — it may be an invalid ticker.", symbol)
        except Exception as e:
            logger.error("Unexpected error processing %s: %s", symbol, e)

    return results

model = OpenAIChat(id="gpt-4.1", temperature=0.2, api_key=OPENAI_API_KEY)

technical_analyst_agent = Agent(
    name="Technical Analyst",
    role="Expert Technical Stock Analyst specializing in price action, trend strength, momentum, and volatility analysis.",
    model=model,
    tools=[get_technical_indicators],
    instructions=TECHNICAL_INSTRUCTIONS,
    markdown=True
)



if __name__ == "__main__":
    response = technical_analyst_agent.run("Analyze the technical indicators for ANET and APLD and provide insights on their current market conditions.")
    report = response.content
    print(report)
