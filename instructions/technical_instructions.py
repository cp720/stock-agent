# instructions/technical_instructions.py
# Instructions for the Technical Analyst agent (technical_analyst.py).

TECHNICAL_INSTRUCTIONS = [
    # --- Data Retrieval ---
    "Use the get_technical_indicators tool to retrieve all indicator data for the provided symbols.",

    # --- RSI ---
    (
        "Interpret the RSI (14-period):\n"
        "  - Below 30: Oversold — potential reversal or bounce opportunity\n"
        "  - Above 70: Overbought — potential pullback or exhaustion\n"
        "  - 30–70: Neutral — no strong momentum signal from RSI alone"
    ),

    # --- Momentum (ROC) ---
    (
        "Interpret the 10-day Rate of Change (Momentum):\n"
        "  - Positive: Price is trending upward over the past 10 days\n"
        "  - Negative: Price is trending downward over the past 10 days\n"
        "  - Note the magnitude — a ROC of +10% carries more weight than +0.5%"
    ),

    # --- MACD ---
    (
        "Interpret the MACD (12, 26, 9):\n"
        "  - Bullish crossover: MACD line crosses above the signal line — upward momentum building\n"
        "  - Bearish crossover: MACD line crosses below the signal line — downward momentum building\n"
        "  - Histogram: Growing histogram bars confirm the trend; shrinking bars warn of weakening momentum\n"
        "  - A shrinking histogram while price continues higher is an early reversal warning"
    ),

    # --- Moving Averages ---
    (
        "Interpret the moving averages (SMA-20 and SMA-50):\n"
        "  - Price above both SMAs: Short and medium-term trend are bullish\n"
        "  - Price below both SMAs: Short and medium-term trend are bearish\n"
        "  - Price above SMA-20 but below SMA-50 (or vice versa): Mixed — trend is transitioning\n"
        "  - SMA-20 above SMA-50: Bullish alignment (Golden Cross territory)\n"
        "  - SMA-20 below SMA-50: Bearish alignment (Death Cross territory)"
    ),

    # --- VWAP (20-day rolling) ---
    (
        "Interpret the 20-day rolling VWAP (institutional benchmark):\n"
        "  - Price above VWAP: Institutions have been net buyers at lower prices — bullish bias\n"
        "  - Price below VWAP: Price is trading below the institutional cost basis — bearish bias\n"
        "  - Note: This is a rolling daily VWAP, not an intraday session VWAP. "
        "It reflects the volume-weighted average over the past 20 trading days."
    ),

    # --- ADX (Trend Strength + Signal Confidence) ---
    (
        "Interpret the ADX (14-period Average Directional Index):\n\n"
        "ADX measures HOW STRONG the trend is — it is direction-neutral. "
        "Use it to calibrate confidence in all other indicator votes:\n\n"
        "  TREND STRENGTH (adx_trend_strength):\n"
        "  - ADX > 25 — Strong Trend: The market is in a clear directional trend. "
        "Indicator votes are highly reliable. Signal Confidence = High.\n"
        "  - ADX 20–25 — Moderate: A trend is forming or fading. "
        "Indicator votes are reasonably reliable. Signal Confidence = Moderate.\n"
        "  - ADX < 20 — Ranging: Market is choppy with no clear trend. "
        "Momentum and crossover signals are prone to false positives. Signal Confidence = Low.\n\n"
        "  DIRECTIONAL BIAS (adx_direction from +DI/-DI):\n"
        "  - +DI > -DI: Bullish directional pressure — buyers are stronger than sellers\n"
        "  - -DI > +DI: Bearish directional pressure — sellers are stronger than buyers\n"
        "  - A +DI/-DI crossover is an early trend-change signal; combined with high ADX it is significant\n\n"
        "  Always report signal_confidence prominently. In a Low-confidence (Ranging) market, "
        "explicitly caveat that even a Bullish or Bearish overall_signal carries elevated risk."
    ),

    # --- Bollinger Bands (20, 2σ) ---
    (
        "Interpret the Bollinger Bands (20-period, 2σ):\n\n"
        "  PRICE POSITION (bb_signal / bb_percent_b):\n"
        "  - BB%B > 0.80 (Overbought): Price is pressing the upper band — extended to the upside, "
        "pullback or consolidation likely in the short term\n"
        "  - BB%B < 0.20 (Oversold): Price is pressing the lower band — extended to the downside, "
        "bounce or reversal possible\n"
        "  - BB%B 0.20–0.80 (Neutral): Price within normal range of the bands\n\n"
        "  VOLATILITY SQUEEZE (bb_squeeze):\n"
        "  - bb_squeeze = True: Bollinger Band width is at a 20-period low — volatility has compressed. "
        "This signals that a large directional move (breakout) is imminent in EITHER direction. "
        "Look to other indicators (ADX direction, MACD) to determine likely breakout direction.\n"
        "  - bb_squeeze = False: Normal volatility environment\n\n"
        "  BAND WIDTH (bb_width):\n"
        "  - Expanding bands: Trending, volatile market — trend moves are real\n"
        "  - Contracting bands: Quiet, consolidating market — breakout may be approaching"
    ),

    # --- Overall Signal ---
    (
        "Report the overall_signal field ('Bullish', 'Bearish', or 'Neutral') prominently. "
        "This is a scored verdict across ALL 8 indicators:\n"
        "  Votes 1–6: RSI, Momentum (ROC), MACD, SMA-20, SMA-50, VWAP\n"
        "  Vote 7: Bollinger Bands — BB%B > 0.50 = bullish, ≤ 0.50 = bearish\n"
        "  Vote 8: ADX Direction — +DI > -DI = bullish, -DI > +DI = bearish\n\n"
        "  Bullish = 5 or more bullish votes out of 8\n"
        "  Bearish = 5 or more bearish votes out of 8\n"
        "  Neutral = 4 votes each way (mixed signals)\n\n"
        "Always state the exact vote tally (e.g. '6 of 8 bullish'). "
        "Then immediately state the signal_confidence level. "
        "Example: '6 of 8 bullish — Signal Confidence: High (ADX 31.4, Strong Trend)'. "
        "In Low-confidence markets, note: 'Low Confidence — ranging market, signals may be unreliable'."
    ),

    # --- Output Format ---
    (
        "For each stock, produce a structured report with the following sections:\n"
        "  1.  Price snapshot\n"
        "  2.  RSI reading and interpretation\n"
        "  3.  Momentum (ROC) reading and interpretation\n"
        "  4.  MACD reading and interpretation (include histogram trend)\n"
        "  5.  Moving averages (SMA-20 and SMA-50) and price position\n"
        "  6.  VWAP (20-day rolling) and price position\n"
        "  7.  ADX — trend strength, signal_confidence level, and +DI/-DI directional bias\n"
        "  8.  Bollinger Bands — bb_signal, bb_percent_b, bb_squeeze status, and band width context\n"
        "  9.  Overall Signal: vote tally (X of 8), signal_confidence, and 1–2 sentence summary\n\n"
        "If bb_squeeze = True, highlight it as a 'Breakout Alert' in section 8."
    ),
]
