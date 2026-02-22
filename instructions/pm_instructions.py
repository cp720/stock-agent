# instructions/pm_instructions.py
# Instructions for the Portfolio Management Team (pm_agent.py).
# Add new phases or rules here as the team's logic grows.


PM_INSTRUCTIONS = [
    # --- Phase 1 ---
    "### Phase 1: Investigation",
    "Request the Technical and Fundamental reports from your members for the given ticker.",

    # --- Phase 2 ---
    "### Phase 2: Live Verification",
    "Call 'get_account_balance' and 'get_portfolio_positions' to see current funds and holdings.",

    # --- Phase 3 ---
    "### Phase 3: Risk Math",
    "If Technical is 'Bullish' and Fundamental > 7, calculate BUY quantity.",
    (
        "Use the formula: $$S = \\frac{E \\times 0.10}{P}$$ to calculate the number of shares "
        "to buy (S), where you risk only 10% of your total equity (E) at the current price (P)."
    ),
    (
        "If Technical is 'Bearish' and Fundamental < 4, recomend a SEll."
        "If the stock is currently held calculate SELL quantity. Use the same formula but based on the current position size instead of equity."
    ),
    "(Where S=Shares, E=Total Equity, P=Current Price).",
    "If the action is HOLD, still call send_n8n_notification with action='HOLD' and quantity=0.",

    # --- Phase 4 ---
    "### Phase 4: Final Action",
    "You MUST call 'send_n8n_notification' with the final recommendation.",
    "Include a 'thesis' summarizing the indicator results and the fundamental score.",
    "The thesis should be 2-3 sentences explaining the reasoning behind the recommendation.",
]
