# instructions/fundamental_instructions.py
# Instructions for the Fundamental Analyst agent (fundamental_analyst.py).

FUNDAMENTAL_INSTRUCTIONS = [
    # --- Step 1: Identify Sector Context ---
    (
        "Identify the company's sector and industry. "
        "All valuation ratios must be benchmarked against sector averages, not market-wide averages. "
        "State the sector explicitly in your report."
    ),

    # --- Step 2: Valuation ---
    (
        "Analyse the company's valuation ratios (P/E and P/S). "
        "Compare them to the sector median. "
        "Flag if the stock appears significantly overvalued or undervalued relative to peers."
    ),

    # --- Step 3: Financial Health ---
    (
        "Review the following financial metrics and report a value and interpretation for each:\n"
        "  - Revenue growth (YoY %)\n"
        "  - Profit margins (gross and net)\n"
        "  - Free Cash Flow (positive or negative trend)\n"
        "  - Debt-to-Equity ratio (flag if above 2.0 as elevated risk)"
    ),

    # --- Step 4: Earnings Quality ---
    (
        "Review the most recent earnings report. "
        "Note whether the company beat or missed revenue and EPS estimates, "
        "and whether forward guidance was raised, maintained, or lowered."
    ),

    # --- Step 5: Analyst Consensus ---
    (
        "Summarise the current Wall Street analyst consensus (Buy, Hold, or Sell), "
        "including the number of analysts and the average price target if available."
    ),

    # --- Step 6: News & Catalysts ---
    (
        "Review recent news headlines (last 30 days). "
        "Identify any material risks (regulatory, legal, macro) or catalysts (product launches, partnerships, M&A). "
        "Do not let news sentiment override quantitative findings from Steps 2–5."
    ),

    # --- Step 7: Fundamental Score ---
    (
        "Assign a final 'Fundamental Score' from 1 to 10 using the following rubric:\n"
        "  - 8–10: Strong fundamentals, undervalued or fairly valued, positive catalysts\n"
        "  - 5–7:  Mixed signals, fairly valued, no major red flags but no strong conviction\n"
        "  - 1–4:  Weak financials, overvalued, negative catalysts or elevated risk\n\n"
        "Provide a 2–3 sentence justification referencing specific metrics from your analysis. "
        "The score must be returned as a plain integer in the field 'fundamental_score'."
    ),
]
