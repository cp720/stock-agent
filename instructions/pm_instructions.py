# instructions/pm_instructions.py
# Instructions for the Portfolio Management Team (pm_agent.py).
# Add new phases or rules here as the team's logic grows.


PM_INSTRUCTIONS = [
    # --- Phase 1 ---
    "### Phase 1: Investigation",
    "Request the Technical and Fundamental reports from your team members for the given ticker.",
    "Wait for both reports before proceeding.",

    # --- Phase 2 ---
    "### Phase 2: Live Account Check",
    "Call 'get_account_balance' and 'get_portfolio_positions' to retrieve current buying power and holdings.",

    # --- Phase 3 ---
    "### Phase 3: Risk-Sizing and Decision",
    (
        "Using the Technical signal and Fundamental score from Phase 1, and the account data from Phase 2, "
        "determine the recommended action. Use the price reported by the Technical Analyst as P in all formulas "
        "— do NOT fetch or estimate price from any other source.\n\n"
        "determine the recommended action using these rules:\n\n"

        "**BUY** — if Technical is 'Bullish' AND Fundamental score > 7:\n"
        "  - If the stock is NOT currently held: S = (E × 0.10) / P\n"
        "  - If the stock IS currently held: S = (current_position_value × 0.30) / P\n"
        "  - Where S = shares to buy, E = total equity,"
        "  - P = the price field returned by the Technical Analyst in their report (closing price from Alpaca data).\n"
        "  - Cap S so that (S × P) does not exceed available buying power.\n\n"

        "**SELL** — if Technical is 'Bearish' AND Fundamental score < 4:\n"
        "  - If the stock is currently held: S = (current_position_value × 0.50) / P\n"
        "  - If the stock is NOT held: action is SELL (eventhough its not owned, we bet on the price decreasing more).\n\n"

        "**HOLD** — in all other cases, including when Fundamental score is between 4 and 7 "
        "(regardless of Technical signal), or when signals are mixed. Set quantity = 0."
    ),

    # --- Phase 4 ---
    "### Phase 4: Notification",
    (
        "You MUST call 'send_n8n_notification' with the following fields:\n"
        "  - action: 'BUY', 'SELL', or 'HOLD'\n"
        "  - quantity: the calculated S value (0 for HOLD)\n"
        "  - thesis: 2–3 sentences summarising the Technical signal(including the price used), Fundamental score, "
        "current position status, and why this action was chosen."
    ),
]
