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

    # --- OBV (On Balance Volume) ---
    (
        "Interpret OBV (On Balance Volume) — the smart money indicator:\n\n"
        "  OBV TREND (obv_trend / obv_slope):\n"
        "  - Rising (positive slope): Institutions are accumulating — volume is flowing in on up days. "
        "This confirms bullish price action or foreshadows an upward move.\n"
        "  - Falling (negative slope): Institutions are distributing — volume is flowing out. "
        "This confirms bearish price action or foreshadows a downward move.\n\n"
        "  OBV DIVERGENCE (obv_divergence) — CRITICAL reversal signal:\n"
        "  - Bearish: Price is rising but OBV is falling. Smart money is quietly selling into retail "
        "buying pressure — classic distribution pattern at a market top.\n"
        "  - Bullish: Price is falling but OBV is rising. Smart money is accumulating shares "
        "while retail panics — classic accumulation pattern at a market bottom.\n"
        "  - None: Price and OBV are directionally aligned — no divergence.\n\n"
        "  OBV divergence is one of the most reliable reversal indicators because volume leads price."
    ),

    # --- Stochastic Oscillator (14, 3, 3) ---
    (
        "Interpret the Stochastic Oscillator (14, 3, 3):\n\n"
        "  The Stochastic is most useful in RANGING markets (ADX < 20, signal_confidence = Low) "
        "where RSI often stays mid-range and gives no signal.\n\n"
        "  - %K > 80 (Overbought): Price is at the top of its 14-day range — reversal downward likely\n"
        "  - %K < 20 (Oversold): Price is at the bottom of its 14-day range — reversal upward likely\n"
        "  - %K 20–80 (Neutral): No extreme reading\n\n"
        "  In TRENDING markets (ADX > 25), Stochastic overbought/oversold can persist for extended periods "
        "and should be given less weight — the trend can override the oscillator. "
        "In RANGING markets, Stochastic is your primary reversal tool."
    ),

    # --- Divergence Detection ---
    (
        "Interpret RSI and MACD divergence — the earliest reversal warning system:\n\n"
        "  BEARISH DIVERGENCE (rsi_divergence / macd_divergence = 'Bearish Divergence'):\n"
        "  Price makes a higher high but the indicator makes a lower high. "
        "This means momentum is fading even as price pushes higher — "
        "the rally is losing conviction and a reversal or pullback is probable.\n\n"
        "  BULLISH DIVERGENCE (rsi_divergence / macd_divergence = 'Bullish Divergence'):\n"
        "  Price makes a lower low but the indicator makes a higher low. "
        "This means selling pressure is weakening even as price drops — "
        "a bounce or trend reversal is probable.\n\n"
        "  When BOTH RSI and MACD show the same divergence type simultaneously, "
        "the reversal signal is significantly stronger. Report this as a 'double divergence'."
    ),

    # --- Reversal Alert ---
    (
        "Report the reversal_alert field prominently when it is NOT 'None':\n\n"
        "  The reversal alert fires when 2 or more reversal factors converge from this list:\n"
        "    Bearish factors: RSI bearish divergence, MACD bearish divergence, OBV distribution, "
        "Stochastic overbought, BB upper band pressure\n"
        "    Bullish factors: RSI bullish divergence, MACD bullish divergence, OBV accumulation, "
        "Stochastic oversold, BB lower band support\n\n"
        "  - 'Potential Bearish Reversal': Even if the overall_signal is currently Bullish, "
        "the trend may be exhausting. List each factor from reversal_factors.\n"
        "  - 'Potential Bullish Reversal': Even if the overall_signal is currently Bearish, "
        "a bounce may be forming. List each factor from reversal_factors.\n"
        "  - 'None': No converging reversal signals — the trend is intact.\n\n"
        "  When a reversal alert is present, it should be the FIRST thing mentioned in the summary, "
        "before the overall_signal vote tally."
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
        "  9.  OBV — trend direction, slope, and divergence status\n"
        "  10. Stochastic Oscillator — %K, %D, signal, and ranging-market context\n"
        "  11. Divergence Analysis — RSI divergence, MACD divergence, double-divergence if both fire\n"
        "  12. Reversal Alert — status and contributing factors (or 'No reversal signals')\n"
        "  13. Overall Signal: vote tally (X of 8), signal_confidence, reversal_alert, "
        "and 2–3 sentence summary\n\n"
        "If bb_squeeze = True, highlight it as a 'Breakout Alert' in section 8.\n"
        "If reversal_alert is NOT 'None', highlight it in bold in section 12."
    ),
]
