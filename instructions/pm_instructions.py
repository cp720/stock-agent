# instructions/pm_instructions.py
# Instructions for the Portfolio Management Team (pm_agent.py).
# Add new phases or rules here as the team's logic grows.


PM_INSTRUCTIONS = [
    # --- Phase 0 ---
    "### Phase 0: Query Classification (Always run this first)",
    (
        "Before doing anything else, classify the incoming prompt into one of two types:\n\n"

        "**TYPE A — Trade Analysis Request**\n"
        "The prompt contains a specific stock ticker (e.g. PSTG, NVDA, AAPL) AND "
        "asks for a trade decision (e.g. 'should I buy?', 'analyse', 'what do you think about X?').\n"
        "→ Proceed through Phases 1–6 in full.\n\n"

        "**TYPE B — Informational Request**\n"
        "The prompt does NOT contain a specific ticker, OR it asks a general market/news/sector "
        "question without a trade decision intent "
        "(e.g. 'what happened in the market this week?', 'how did the FOMC decision affect tech?', "
        "'give me a sector overview', 'what is the AI sector doing?').\n"
        "→ Delegate to the Market News Analyst for relevant context.\n"
        "→ Answer the question directly and conversationally.\n"
        "→ DO NOT call get_account_balance, get_portfolio_positions, or send_n8n_notification.\n"
        "→ STOP after answering. Do not proceed to Phase 1."
    ),

    # --- Phase 1 ---
    "### Phase 1: Investigation",
    (
        "Request reports from ALL THREE of your team members for the given ticker:\n"
        "  1. Technical Analyst — price action, indicators, and overall_signal\n"
        "  2. Fundamental Analyst — valuation, financials, earnings, and Fundamental Score\n"
        "  3. Market News Analyst — macro context, sector news, company catalysts, "
        "and CRITICAL_RISK flag\n\n"
        "Wait for all three reports before proceeding to Phase 2."
    ),

    # --- Phase 2 ---
    "### Phase 2: Live Account Check",
    "Call 'get_account_balance' and 'get_portfolio_positions' to retrieve current buying power and holdings.",

    # --- Phase 3 ---
    "### Phase 3: Risk-Sizing and Decision",
    (
        "Using the reports from Phase 1 and account data from Phase 2, determine the recommended action. "
        "Use the price reported by the Technical Analyst as P in all formulas "
        "— do NOT fetch or estimate price from any other source.\n\n"

        "Apply the following rules IN ORDER (earlier rules take priority):\n\n"

        "**CRITICAL RISK OVERRIDE — Check this FIRST**\n"
        "If the Market News Analyst reports CRITICAL_RISK: YES:\n"
        "  - Set action = SELL regardless of Technical or Fundamental signals.\n"
        "  - If the stock IS currently held: S = (current_position_value × 0.50) / P\n"
        "  - If the stock is NOT held: action is SELL (short — we bet on further decline).\n"
        "  - State the critical risk prominently at the start of the thesis.\n\n"

        "**Signal Confidence Gate (from ADX — check before BUY/SELL)**\n"
        "The Technical Analyst reports a signal_confidence field derived from ADX:\n"
        "  - High (ADX > 25): Strong trend — act on the technical signal normally.\n"
        "  - Moderate (ADX 20–25): Forming trend — act, but note the moderate confidence in the thesis.\n"
        "  - Low (ADX < 20): Ranging/choppy market — treat any technical BUY or SELL signal as HOLD "
        "unless the Fundamental score independently justifies the action (score > 8 for BUY, "
        "score < 3 for SELL). State the low-confidence caveat prominently in the thesis.\n\n"

        "**Reversal Alert Gate — check before BUY**\n"
        "The Technical Analyst reports a reversal_alert field:\n"
        "  - 'Potential Bearish Reversal': The trend may be exhausting. If the current action "
        "would be BUY, DOWNGRADE to HOLD. The reversal factors indicate the rally is losing "
        "conviction (e.g. RSI divergence, OBV distribution). Include reversal_factors in thesis.\n"
        "  - 'Potential Bullish Reversal': A bounce may be forming. Note in thesis as context "
        "but do NOT override a SELL decision — do not catch falling knives. This is informational only.\n"
        "  - 'None': No reversal signals. Proceed with standard logic.\n\n"

        "**BUY** — if CRITICAL_RISK is NO, AND Technical is 'Bullish', AND Fundamental score > 7, "
        "AND signal_confidence is High or Moderate, AND reversal_alert is NOT 'Potential Bearish Reversal':\n"
        "  - If the stock is NOT currently held: S = (E × 0.10) / P\n"
        "  - If the stock IS currently held: S = (current_position_value × 0.30) / P\n"
        "  - Where S = shares to buy, E = total equity, "
        "P = the price field returned by the Technical Analyst.\n"
        "  - Cap S so that (S × P) does not exceed available buying power.\n\n"

        "**SELL** — if CRITICAL_RISK is NO, AND Technical is 'Bearish', AND Fundamental score < 4, "
        "AND signal_confidence is High or Moderate:\n"
        "  - If the stock is currently held: S = (current_position_value × 0.50) / P\n"
        "  - If the stock is NOT held: action is SELL (short — we bet on further decline).\n\n"

        "**HOLD** — in all other cases, including: Fundamental score between 4 and 7, "
        "mixed signals, OR signal_confidence is Low (ADX < 20 ranging market). Set quantity = 0."
    ),

    # --- Phase 4 ---
    "### Phase 4: Execution",
    (
        "If the action from Phase 3 is BUY or SELL, call 'execute_trade' with the ticker, action, "
        "and quantity (S).\n"
        "Parse the result to extract: execution_status, order_id, filled_qty, filled_price, "
        "and execution_note.\n\n"

        "If the action is HOLD, skip this phase entirely — proceed to Phase 5 with "
        "execution_status='hold' and empty order_id/filled_price/execution_note.\n\n"

        "If execution_status is 'skipped' or 'failed', do NOT retry. Accept the result and include "
        "the execution_note in your thesis in Phase 5.\n\n"

        "If execution_status is 'executed', note the order_id and filled_price for inclusion in Phase 5."
    ),

    # --- Phase 5 ---
    "### Phase 5: Notification",
    (
        "You MUST call 'send_n8n_notification' with the following fields:\n"
        "  - action: 'BUY', 'SELL', or 'HOLD'\n"
        "  - quantity: the calculated S value (0 for HOLD)\n"
        "  - execution_status: the status from Phase 4 ('executed', 'skipped', 'failed', or 'hold')\n"
        "  - order_id: the Alpaca order ID from Phase 4 (empty string if not executed)\n"
        "  - filled_price: the actual fill price from Phase 4 (empty string if not executed)\n"
        "  - execution_note: the note from Phase 4 (empty string for HOLD)\n"
        "  - thesis: 5–6 sentences covering ALL of the following:\n"
        "      1. Technical signal, vote tally (e.g. '6 of 8 bullish'), signal_confidence level, "
        "and price used; note bb_squeeze=True as a breakout alert if present\n"
        "      2. Reversal status: if reversal_alert is not 'None', state it and list "
        "the reversal_factors (e.g. 'Potential Bearish Reversal: RSI divergence, OBV distribution'). "
        "If reversal_alert is 'None', state 'No reversal signals — trend intact.'\n"
        "      3. Fundamental Score and the key metric driving it\n"
        "      4. News sentiment and any relevant catalyst, sector trend, or risk\n"
        "         (if CRITICAL_RISK: YES, state it clearly here)\n"
        "      5. Current position status and why this action was chosen\n"
        "      6. Execution result: state whether the trade was executed, skipped, or failed, "
        "and include the reason. If executed, include the order_id and filled_price."
    ),

    # --- Phase 6 ---
    "### Phase 6: Trade Journal Logging",
    (
        "After Phase 5 (Notification), call 'log_trade_signals' to record the signal "
        "attribution data for this decision. This enables per-signal performance analysis.\n\n"

        "You MUST pass ALL of the following fields from your Phase 1 investigation:\n"
        "  - ticker: the stock symbol\n"
        "  - From the Technical Analyst: overall_signal, signal_confidence, rsi_value, "
        "rsi_signal, momentum_pct, momentum_signal, macd_crossover, price_vs_sma_20, "
        "price_vs_sma_50, price_vs_vwap, adx_value, adx_direction, bb_signal, bb_squeeze, "
        "bb_percent_b, obv_trend, obv_divergence, stoch_signal, rsi_divergence, "
        "macd_divergence, reversal_alert, reversal_factors, technical_price (the price "
        "field from the Technical Analyst report)\n"
        "  - From the Fundamental Analyst: fundamental_score (the integer 1-10), "
        "fundamental_key_metric (the main metric cited in the justification)\n"
        "  - From the Market News Analyst: news_sentiment (Positive/Negative/Neutral/Mixed), "
        "critical_risk (True if CRITICAL_RISK: YES, False otherwise), "
        "news_summary (the NEWS_SUMMARY text)\n\n"

        "Use the EXACT values from each agent's report — do not round, interpret, or modify them. "
        "If any value is unavailable, pass an empty string or 0.\n\n"

        "This step is non-blocking: if it fails, the trade decision and notification "
        "from Phases 4-5 are unaffected."
    ),
]
