import numpy as np
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


# ---------------------------------------------------------------------------
# Divergence Detection Helper
# ---------------------------------------------------------------------------
def _detect_divergence(price_series: pd.Series, indicator_series: pd.Series,
                       lookback: int = 30, peak_window: int = 5) -> str:
    """
    Detect price-vs-indicator divergence using local peak/trough comparison.

    Bearish divergence: price makes a higher high but the indicator makes a lower high.
    Bullish divergence: price makes a lower low but the indicator makes a higher low.

    Returns one of: "Bearish Divergence", "Bullish Divergence", "None".
    """
    if len(price_series) < lookback:
        return "None"

    price = price_series.iloc[-lookback:].reset_index(drop=True)
    indicator = indicator_series.iloc[-lookback:].reset_index(drop=True)

    # --- Find local peaks (for bearish divergence) ---
    peaks = []
    for i in range(peak_window, len(price) - peak_window):
        window_slice = price.iloc[i - peak_window : i + peak_window + 1]
        if price.iloc[i] == window_slice.max():
            peaks.append(i)

    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]  # p1 is earlier, p2 is later
        if price.iloc[p2] > price.iloc[p1] and indicator.iloc[p2] < indicator.iloc[p1]:
            return "Bearish Divergence"

    # --- Find local troughs (for bullish divergence) ---
    troughs = []
    for i in range(peak_window, len(price) - peak_window):
        window_slice = price.iloc[i - peak_window : i + peak_window + 1]
        if price.iloc[i] == window_slice.min():
            troughs.append(i)

    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        if price.iloc[t2] < price.iloc[t1] and indicator.iloc[t2] > indicator.iloc[t1]:
            return "Bullish Divergence"

    return "None"


# ---------------------------------------------------------------------------
# Pydantic Output Model
# ---------------------------------------------------------------------------
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

    # --- OBV (On Balance Volume) ---
    obv_slope: float = Field(..., description="10-day linear regression slope of OBV — positive means institutions accumulating, negative means distributing")
    obv_trend: str = Field(..., description="Rising (accumulation — smart money buying) or Falling (distribution — smart money selling)")
    obv_divergence: str = Field(
        ...,
        description=(
            "Bearish — price rising but OBV falling (distribution at a top)\n"
            "Bullish — price falling but OBV rising (accumulation at a bottom)\n"
            "None — price and OBV are directionally aligned"
        )
    )

    # --- Stochastic Oscillator (14, 3, 3) ---
    stoch_k: float = Field(..., description="Stochastic %K (14-period, smoothed 3)")
    stoch_d: float = Field(..., description="Stochastic %D (3-period signal line of %K)")
    stoch_signal: str = Field(..., description="Overbought (%K > 80), Oversold (%K < 20), or Neutral")

    # --- Divergence Detection ---
    rsi_divergence: str = Field(
        ...,
        description=(
            "Bearish Divergence — price higher high but RSI lower high (momentum fading at top)\n"
            "Bullish Divergence — price lower low but RSI higher low (momentum building at bottom)\n"
            "None — no divergence detected"
        )
    )
    macd_divergence: str = Field(
        ...,
        description=(
            "Bearish Divergence — price higher high but MACD histogram lower high\n"
            "Bullish Divergence — price lower low but MACD histogram higher low\n"
            "None — no divergence detected"
        )
    )

    # --- Reversal Alert ---
    reversal_alert: str = Field(
        ...,
        description=(
            "Potential Bearish Reversal — 2+ bearish reversal factors converge (sell pressure building)\n"
            "Potential Bullish Reversal — 2+ bullish reversal factors converge (buying pressure building)\n"
            "None — no converging reversal signals"
        )
    )
    reversal_factors: str = Field(
        ...,
        description="Comma-separated list of specific reversal factors detected (e.g. 'RSI bearish divergence, OBV distribution, Stochastic overbought')"
    )

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
    RSI, Momentum (ROC), MACD, SMA-20, SMA-50, VWAP-20, ADX, Bollinger Bands,
    OBV, Stochastic Oscillator, and RSI/MACD divergence detection.
    Returns a synthesized overall signal, signal confidence, and reversal alert.

    Args:
        symbols (List[str]): A list of stock ticker symbols to analyze.
    Returns:
        List[TechnicalIndicatorResults]: Full technical indicator results per symbol.
    """

    # 200 calendar days (~140 trading days) gives all indicators a safe warm-up buffer
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
            typical_price = (df['high'] + df['low'] + df['close']) / 3
            df['VWAP_20'] = (
                (typical_price * df['volume']).rolling(20).sum()
                / df['volume'].rolling(20).sum()
            )

            # --- ADX (14) — trend strength and directional bias ---
            adx_df = ta.adx(df['high'], df['low'], df['close'], length=14)
            df['ADX_14']   = adx_df['ADX_14']
            df['DI_Plus']  = adx_df['DMP_14']   # +DI
            df['DI_Minus'] = adx_df['DMN_14']   # -DI

            # --- Bollinger Bands (20-period, 2σ) ---
            bb_df = ta.bbands(df['close'], length=20, std=2)
            # Column names vary by pandas-ta version (e.g. 'BBU_20_2.0' vs 'BBU_20_2.0_2.0')
            # Use prefix lookup for version-agnostic access
            bb_col = {c[:3]: c for c in bb_df.columns}  # {'BBU': 'BBU_...', 'BBL': 'BBL_...', ...}
            df['BB_Upper']     = bb_df[bb_col['BBU']]
            df['BB_Lower']     = bb_df[bb_col['BBL']]
            df['BB_Width']     = bb_df[bb_col['BBB']]   # BBB = Bandwidth (may also appear as BBW)
            df['BB_Percent_B'] = bb_df[bb_col['BBP']]

            # BB Squeeze: True when width is in the bottom 20% of its 20-period range
            bb_width_min = df['BB_Width'].rolling(20).min()
            bb_width_max = df['BB_Width'].rolling(20).max()
            bb_width_range = bb_width_max - bb_width_min
            bb_width_pct = (df['BB_Width'] - bb_width_min) / bb_width_range.replace(0, float('nan'))
            df['BB_Squeeze'] = bb_width_pct < 0.20

            # --- OBV (On Balance Volume) ---
            df['OBV'] = ta.obv(df['close'], df['volume'])

            # 10-day OBV slope via linear regression
            obv_recent = df['OBV'].iloc[-10:].values
            if len(obv_recent) == 10 and not np.any(np.isnan(obv_recent)):
                x = np.arange(10, dtype=float)
                obv_slope_val = float(np.polyfit(x, obv_recent, 1)[0])
            else:
                obv_slope_val = 0.0

            # --- Stochastic Oscillator (14, 3, 3) ---
            stoch_df = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
            df['Stoch_K'] = stoch_df['STOCHk_14_3_3']
            df['Stoch_D'] = stoch_df['STOCHd_14_3_3']

            # --- RSI Divergence ---
            rsi_divergence = _detect_divergence(df['close'], df['RSI'], lookback=30, peak_window=5)

            # --- MACD Histogram Divergence ---
            macd_divergence = _detect_divergence(df['close'], df['MACD_hist'], lookback=30, peak_window=5)

            # ---------------------------------------------------------------
            # Extract latest values
            # ---------------------------------------------------------------
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
            stoch_k       = latest['Stoch_K']
            stoch_d       = latest['Stoch_D']

            # Guard against NaN (insufficient warm-up data)
            required = [
                rsi_value, momentum_pct, macd_line, macd_signal, macd_hist,
                sma_20, sma_50, vwap_20, adx_value, di_plus, di_minus,
                bb_upper, bb_lower, bb_width, bb_percent_b,
                stoch_k, stoch_d,
            ]
            if any(pd.isna(v) for v in required):
                logger.warning("Insufficient data to compute all indicators for %s — skipping.", symbol)
                continue

            # ---------------------------------------------------------------
            # Interpret each indicator
            # ---------------------------------------------------------------
            rsi_signal      = "Oversold" if rsi_value < 30 else "Overbought" if rsi_value > 70 else "Neutral"
            momentum_signal = "Positive" if momentum_pct > 0 else "Negative"
            macd_crossover  = "Bullish" if macd_line > macd_signal else "Bearish"
            price_vs_sma_20 = "Above" if latest_price > sma_20 else "Below"
            price_vs_sma_50 = "Above" if latest_price > sma_50 else "Below"
            price_vs_vwap   = "Above" if latest_price > vwap_20 else "Below"

            # ADX: trend strength and direction
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

            # Bollinger Bands: price position within bands
            if bb_percent_b > 0.80:
                bb_signal_str = "Overbought"
            elif bb_percent_b < 0.20:
                bb_signal_str = "Oversold"
            else:
                bb_signal_str = "Neutral"

            # OBV: trend and divergence vs price
            obv_trend = "Rising" if obv_slope_val > 0 else "Falling"

            if momentum_pct > 0 and obv_slope_val < 0:
                obv_divergence = "Bearish"   # price rising, smart money selling
            elif momentum_pct < 0 and obv_slope_val > 0:
                obv_divergence = "Bullish"   # price falling, smart money buying
            else:
                obv_divergence = "None"

            # Stochastic: overbought / oversold
            if stoch_k > 80:
                stoch_signal = "Overbought"
            elif stoch_k < 20:
                stoch_signal = "Oversold"
            else:
                stoch_signal = "Neutral"

            # ---------------------------------------------------------------
            # Reversal Alert — fires when 2+ factors converge
            # ---------------------------------------------------------------
            bearish_factors = []
            bullish_factors = []

            if rsi_divergence == "Bearish Divergence":
                bearish_factors.append("RSI bearish divergence")
            if macd_divergence == "Bearish Divergence":
                bearish_factors.append("MACD bearish divergence")
            if obv_divergence == "Bearish":
                bearish_factors.append("OBV distribution")
            if stoch_signal == "Overbought":
                bearish_factors.append("Stochastic overbought")
            if bb_signal_str == "Overbought":
                bearish_factors.append("BB upper band pressure")

            if rsi_divergence == "Bullish Divergence":
                bullish_factors.append("RSI bullish divergence")
            if macd_divergence == "Bullish Divergence":
                bullish_factors.append("MACD bullish divergence")
            if obv_divergence == "Bullish":
                bullish_factors.append("OBV accumulation")
            if stoch_signal == "Oversold":
                bullish_factors.append("Stochastic oversold")
            if bb_signal_str == "Oversold":
                bullish_factors.append("BB lower band support")

            if len(bearish_factors) >= 2:
                reversal_alert = "Potential Bearish Reversal"
                reversal_factors = ", ".join(bearish_factors)
            elif len(bullish_factors) >= 2:
                reversal_alert = "Potential Bullish Reversal"
                reversal_factors = ", ".join(bullish_factors)
            else:
                reversal_alert = "None"
                reversal_factors = "No converging reversal signals"

            # ---------------------------------------------------------------
            # Synthesize overall signal (score out of 8)
            # ---------------------------------------------------------------
            bullish_votes = sum([
                rsi_signal != "Overbought",          # RSI not overbought = bullish
                momentum_signal == "Positive",        # Positive ROC = bullish
                macd_crossover == "Bullish",          # MACD above signal = bullish
                price_vs_sma_20 == "Above",           # Price above SMA-20 = bullish
                price_vs_sma_50 == "Above",           # Price above SMA-50 = bullish
                price_vs_vwap == "Above",             # Price above VWAP = bullish
                bb_percent_b > 0.50,                  # Price in upper half of BB = bullish
                adx_direction == "Bullish",           # +DI > -DI = bullish
            ])
            bearish_votes = 8 - bullish_votes

            if bullish_votes >= 5:
                overall_signal = "Bullish"
            elif bearish_votes >= 5:
                overall_signal = "Bearish"
            else:
                overall_signal = "Neutral"

            # ---------------------------------------------------------------
            # Build result
            # ---------------------------------------------------------------
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
                bb_signal=bb_signal_str,
                obv_slope=round(obv_slope_val, 2),
                obv_trend=obv_trend,
                obv_divergence=obv_divergence,
                stoch_k=round(stoch_k, 2),
                stoch_d=round(stoch_d, 2),
                stoch_signal=stoch_signal,
                rsi_divergence=rsi_divergence,
                macd_divergence=macd_divergence,
                reversal_alert=reversal_alert,
                reversal_factors=reversal_factors,
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
    role="Expert Technical Stock Analyst specializing in price action, trend strength, momentum, volatility, and reversal detection.",
    model=model,
    tools=[get_technical_indicators],
    instructions=TECHNICAL_INSTRUCTIONS,
    markdown=True
)



if __name__ == "__main__":
    response = technical_analyst_agent.run("Analyze the technical indicators for ANET and APLD and provide insights on their current market conditions.")
    report = response.content
    print(report)
