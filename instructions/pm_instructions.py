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
        "→ DO NOT call get_account_balance, get_portfolio_positions, or save_recommendation.\n"
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
    "### Phase 2: Live Account Check & Risk Assessment",
    (
        "Call ALL THREE of these tools to get a complete picture of the account:\n"
        "  1. 'get_account_balance' — current equity, buying power, cash\n"
        "  2. 'get_portfolio_positions' — current holdings {ticker: qty}\n"
        "  3. 'get_portfolio_risk_assessment' — comprehensive risk snapshot including "
        "exposure summary, per-position detail (market value, unrealized P&L, concentration %), "
        "portfolio-level risk metrics (drawdown, intraday P&L, largest position), "
        "and advisory risk flags\n\n"
        "Review the risk_flags list from the risk assessment. These flags are advisory — "
        "they do not block any action. Use them to inform your decision in Phase 3."
    ),

    # --- Phase 3 ---
    "### Phase 3: Conviction Scoring, Risk-Sizing and Decision",
    (
        "Using the reports from Phase 1 and account data from Phase 2, compute a single "
        "CONVICTION SCORE (0–100) that blends every signal, then map it to an action. "
        "Use the price reported by the Technical Analyst as P in all formulas "
        "— do NOT fetch or estimate price from any other source.\n\n"

        "Do NOT use rigid pass/fail gates. A weak reading in one area lowers conviction; "
        "it does not by itself veto a trade. Your job is to weigh the evidence, not tick boxes.\n\n"

        "Proceed IN ORDER:\n\n"

        "**STEP A — CRITICAL RISK OVERRIDE (check FIRST, before scoring)**\n"
        "If the Market News Analyst reports CRITICAL_RISK: YES:\n"
        "  - If the stock IS currently held: set action = SELL, S = (current_position_value × 0.50) / P. "
        "Skip the conviction score entirely.\n"
        "  - If the stock is NOT held: set action = HOLD with quantity = 0 (we do not short — "
        "execute_trade blocks it). Flag the stock as 'avoid' in the thesis.\n"
        "  - State the critical risk prominently at the start of the thesis, then skip to Phase 4.\n\n"

        "**STEP B — COMPUTE THE CONVICTION SCORE (0–100)**\n"
        "Add the four components below. Show each number in your reasoning.\n\n"

        "  1. Technical component (0–45):\n"
        "     base = (bullish_votes / 8) × 45   [bullish_votes is the X in the 'X of 8' tally]\n"
        "     Then multiply 'base' by the signal_confidence multiplier from ADX:\n"
        "       High (ADX > 25) → ×1.00   |   Moderate (ADX 20–25) → ×0.85   |   Low (ADX < 20) → ×0.60\n"
        "     technical_component = base × multiplier\n\n"

        "  2. Fundamental component (0–40):\n"
        "     fundamental_component = (fundamental_score / 10) × 40\n\n"

        "  3. News adjustment (−15 to +15):\n"
        "     Positive → +15   |   Mixed → +5   |   Neutral → 0   |   Negative → −15\n\n"

        "  4. Reversal / divergence penalty (subtract):\n"
        "     reversal_alert = 'Potential Bearish Reversal' → −12\n"
        "     A double divergence (RSI AND MACD same direction bearish) → additional −6\n"
        "     OBV bearish divergence (obv_divergence = 'Bearish') → additional −4\n"
        "     'Potential Bullish Reversal' adds nothing and removes nothing — note as context only.\n\n"

        "  CONVICTION = technical_component + fundamental_component + news_adjustment − penalties\n"
        "  Clamp the result to the range 0–100.\n\n"

        "**STEP C — MAP CONVICTION TO ACTION (with hysteresis)**\n"
        "  - CONVICTION ≥ 62 → BUY\n"
        "  - CONVICTION ≤ 28 → SELL (only if the stock is currently held; if not held, HOLD — no shorting)\n"
        "  - 29–61 → HOLD\n"
        "  Hysteresis: if the stock is ALREADY held, only add to it when CONVICTION ≥ 70 "
        "(a higher bar than initiating), and only SELL/trim when CONVICTION ≤ 28. "
        "This prevents churn on borderline scores.\n\n"

        "**STEP D — SIZE THE POSITION (ATR risk-based)**\n"
        "  BUY (new position OR adding to a held one): call 'calculate_position_size' with "
        "the ticker, the CONVICTION score, and P (the Technical Analyst's price). Use the "
        "'shares' value it returns as S.\n"
        "    - The tool sizes the position by ATR volatility so that hitting the stop costs "
        "a conviction-scaled fraction of equity (0.5% at conviction 62 → 1.5% at 100). "
        "It ALREADY caps for the 15% position limit and available buying power — do NOT "
        "apply further buying-power or position caps yourself.\n"
        "    - If it returns shares = 0, set action = HOLD (there is no room or risk budget).\n"
        "    - Include the tool's 'note' (the risk/stop/shares breakdown) in the thesis.\n"
        "    - Adding to a held position still requires CONVICTION ≥ 70 from Step C.\n"
        "  SELL, held position: S = (current_position_value × 0.50) / P "
        "(P = Technical Analyst price).\n\n"

        "**STEP E — APPLY PORTFOLIO RISK FLAGS (advisory sizing adjustments, from Phase 2)**\n"
        "  - HIGH CONCENTRATION for this ticker → halve S, or HOLD if already at/over the 15% cap.\n"
        "  - HEAVY EXPOSURE or LOW CASH → halve S.\n"
        "  - PORTFOLIO DRAWDOWN ≤ −10% → strongly favor HOLD over new BUYs; if still buying, halve S.\n"
        "  - INTRADAY LOSS → note in thesis; do not override the multi-day conviction score.\n"
        "  These adjust SIZE only — they do not flip the action unless they push S to ~0.\n\n"

        "Round S down to a whole number of shares. If S rounds to 0, the action becomes HOLD."
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
    "### Phase 5: Save Recommendation",
    (
        "You MUST call 'save_recommendation' with the following fields:\n"
        "  - action: 'BUY', 'SELL', or 'HOLD'\n"
        "  - quantity: the calculated S value (0 for HOLD)\n"
        "  - execution_status: the status from Phase 4 ('executed', 'skipped', 'failed', or 'hold')\n"
        "  - order_id: the Alpaca order ID from Phase 4 (empty string if not executed)\n"
        "  - filled_price: the actual fill price from Phase 4 (empty string if not executed)\n"
        "  - execution_note: the note from Phase 4 (empty string for HOLD)\n"
        "  - thesis: 5–6 sentences covering ALL of the following:\n"
        "      0. The CONVICTION SCORE and its component breakdown, e.g. "
        "'Conviction 71/100 = technical 30.6 (6/8 × Moderate) + fundamental 28 (score 7) "
        "+ news +15 − 0 penalty → BUY'. Always show this first.\n"
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
        "and include the reason. If executed, include the order_id and filled_price.\n"
        "      7. Portfolio risk context: mention any active risk flags from the risk assessment. "
        "If none, state 'No portfolio risk flags.'"
    ),

    # --- Phase 6 ---
    "### Phase 6: Trade Journal Logging",
    (
        "After Phase 5 (Save Recommendation), call 'log_trade_signals' to record signal "
        "attribution data for this decision. This enables per-signal performance analysis.\n\n"

        "Pass the following fields — all are optional except ticker. "
        "Pass None for any field that is unavailable; never pass an empty string.\n"
        "  - ticker: the stock symbol (required)\n"
        "  - conviction_score: the 0–100 conviction score you computed in Phase 3 Step B\n"
        "  - From the Technical Analyst: overall_signal, signal_confidence, rsi_value, "
        "rsi_signal, momentum_pct, macd_crossover, adx_value, bb_squeeze, "
        "reversal_alert, technical_price\n"
        "  - From the Fundamental Analyst: fundamental_score, fundamental_key_metric\n"
        "  - From the Market News Analyst: news_sentiment, "
        "critical_risk (True/False), news_summary\n\n"

        "This step is non-blocking: if it fails, the trade decision and notification "
        "from Phases 4-5 are unaffected."
    ),
]
