from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.yfinance import YFinanceTools
from config import OPENAI_API_KEY

# --- Fundamental Analyst Agent ---

model = OpenAIChat(id="gpt-4.1", temperature=0.4, api_key=OPENAI_API_KEY)

# This agent acts as a fundamental analyst, providing insights based on company financials, news, and other relevant data. It can analyze a stock's fundamentals to determine its intrinsic value and growth potential.
fundamental_analyst_agent = Agent(
    name="Fundamental Analyst",
    role="Expert in financial ratios, earnings reports, and market sentiment.",
    model=model,
    tools=[YFinanceTools()],
    instructions=[
        "Start by checking the company's valuation ratios (P/E, P/S) relative to its sector.",
        "Review the latest news headlines to identify any immediate risks or catalysts.",
        "Review earnings reports for revenue growth, profit margins, and cash flow trends.",
        "Summarize the consensus among Wall Street analysts (Buy, Hold, or Sell).",
        "Look for 'Free Cash Flow' and 'Debt-to-Equity' to ensure the company is financially stable.",
        "Provide a final 'Fundamental Score' (1-10) with a brief justification."
    ],
    markdown=True
)

if __name__ == "__main__":
    fundamental_analyst_agent.print_response("Analyze the fundamentals for ANET and B (Barrick Mining Gold Corp) and provide recent news headlines and analyst recommendations", stream=True)