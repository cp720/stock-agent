from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.yfinance import YFinanceTools
from config import OPENAI_API_KEY
from instructions.fundamental_instructions import FUNDAMENTAL_INSTRUCTIONS

# --- Fundamental Analyst Agent ---

model = OpenAIChat(id="gpt-4.1", temperature=0.4, api_key=OPENAI_API_KEY)

# This agent acts as a fundamental analyst, providing insights based on company financials, news, and other relevant data. It can analyze a stock's fundamentals to determine its intrinsic value and growth potential.
fundamental_analyst_agent = Agent(
    name="Fundamental Analyst",
    role="Expert in financial ratios, earnings reports, and market sentiment.",
    model=model,
    tools=[YFinanceTools()],
    instructions=FUNDAMENTAL_INSTRUCTIONS,
    markdown=True
)

if __name__ == "__main__":
    fundamental_analyst_agent.print_response("Analyze the fundamentals for ANET and B (Barrick Mining Gold Corp) and provide recent news headlines and analyst recommendations", stream=True)