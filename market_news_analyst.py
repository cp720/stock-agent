# market_news_analyst.py
# Market News Analyst Agent — provides macro, sector, and company-specific
# news context for the Portfolio Management Team.
#
# Output includes:
#   - SENTIMENT:           Positive | Negative | Neutral | Mixed
#   - CRITICAL_RISK:       YES | NO
#   - CRITICAL_RISK_DETAIL: description or N/A
#   - NEWS_SUMMARY:        2-3 sentence synthesis
#
# News is CONTEXT ONLY. It does not cast a vote in the technical signal
# scoring. Critical risk events (fraud, SEC, bankruptcy, etc.) trigger a
# SELL override in the PM agent regardless of Technical/Fundamental signals.

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.duckduckgo import DuckDuckGoTools
from config import OPENAI_API_KEY
from instructions.news_instructions import NEWS_INSTRUCTIONS

model = OpenAIChat(id="gpt-4.1", temperature=0.2, api_key=OPENAI_API_KEY)

market_news_analyst_agent = Agent(
    name="Market News Analyst",
    role=(
        "Expert financial news analyst specialising in identifying macro trends, "
        "sector developments, and company-specific catalysts or risks from recent news. "
        "Flags critical risk events (fraud, SEC investigations, bankruptcy) that require "
        "an immediate SELL override in the trading decision."
    ),
    model=model,
    tools=[DuckDuckGoTools()],
    instructions=NEWS_INSTRUCTIONS,
    markdown=True,
)


if __name__ == "__main__":
    market_news_analyst_agent.print_response(
        "Analyse recent news for PSTG (Pure Storage). "
        "Cover macro context, storage/tech sector news, and any company-specific developments.",
        stream=True
    )
