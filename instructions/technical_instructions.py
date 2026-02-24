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
        "  - Histogram: Growing histogram bars confirm the trend; shrinking bars warn of weakening momentum"
    ),

    # --- Moving Averages ---
    (
        "Interpret the moving averages (SMA-20 and SMA-50):\n"
        "  - Price above both SMAs: Short and medium-term trend are bullish\n"
        "  - Price below both SMAs: Short and medium-term trend are bearish\n"
        "  - Price above SMA-20 but below SMA-50 (or vice versa): Mixed — trend is transitioning\n"
        "  - SMA-20 above SMA-50: Bullish alignment; SMA-20 below SMA-50: Bearish alignment"
    ),

    # --- Overall Signal ---
    (
        "Report the overall_signal field ('Bullish', 'Bearish', or 'Neutral') prominently. "
        "This is a scored verdict across all 5 indicators and is the primary input for the Portfolio Manager. "
        "Always state how many indicators were bullish vs bearish (e.g. '4 of 5 bullish')."
    ),

    # --- Output Format ---
    (
        "For each stock, produce a structured report with the following sections:\n"
        "  1. Price snapshot\n"
        "  2. RSI reading and interpretation\n"
        "  3. Momentum (ROC) reading and interpretation\n"
        "  4. MACD reading and interpretation\n"
        "  5. Moving averages and price position\n"
        "  6. Overall Signal with vote tally and 1–2 sentence summary"
    ),
]
