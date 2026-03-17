"""
news_tools.py
Custom Agno Toolkit providing reliable financial news search.

Replaces DuckDuckGoTools, which fails intermittently due to DuckDuckGo's
fragile vqd token extraction step having no fallback engine.

Two tools:
  get_ticker_news(ticker)         — yfinance: company-specific structured news
  search_financial_news(query)    — Google News RSS: macro and sector free-text queries

Neither requires a new API key. Google News RSS uses only Python stdlib.
"""

import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import yfinance as yf
from agno.tools import Toolkit

from logger import get_logger

logger = get_logger(__name__)


class StockNewsTools(Toolkit):
    """
    Financial news toolkit using yfinance (company news) and
    Google News RSS (macro / sector free-text search).
    No API key required for either source.
    """

    def __init__(self):
        super().__init__(name="stock_news_tools")
        self.register(self.get_ticker_news)
        self.register(self.search_financial_news)

    def get_ticker_news(self, ticker: str, max_results: int = 8) -> str:
        """
        Get recent news articles for a specific stock ticker using Yahoo Finance.
        Use this for company-specific news (earnings, M&A, executive changes, etc.).
        Also accepts ETF/index symbols for sector or macro news:
          '^GSPC' or 'SPY' → broad S&P 500 / market news
          'XLK'            → technology sector news
          'XLE'            → energy sector news
          'QQQ'            → NASDAQ-100 / growth tech news

        Args:
            ticker: Stock or ETF symbol (e.g. 'NVDA', 'SPY', 'XLK').
            max_results: Number of news items to return (default 8).

        Returns:
            JSON string with a list of news items, each containing:
            title, summary, published (ISO timestamp), source, url.
        """
        try:
            raw = yf.Ticker(ticker).get_news(count=max_results)
            results = []
            for item in raw:
                content = item.get("content", {})
                url_obj = (
                    content.get("canonicalUrl")
                    or content.get("clickThroughUrl")
                    or {}
                )
                results.append({
                    "title": content.get("title", ""),
                    "summary": content.get("summary", "")[:350],
                    "published": content.get("pubDate", ""),
                    "source": content.get("provider", {}).get("displayName", ""),
                    "url": url_obj.get("url", ""),
                })
            logger.info("yfinance news: %d items for %s", len(results), ticker)
            return json.dumps(results, indent=2)
        except Exception as exc:
            logger.warning("yfinance news failed for %s: %s", ticker, exc)
            return json.dumps({"error": str(exc), "ticker": ticker})

    def search_financial_news(self, query: str, max_results: int = 8) -> str:
        """
        Search for financial news using Google News RSS. No API key required.
        Use this for macro-economic, sector-wide, or multi-stock queries
        that cannot be retrieved by ticker alone.

        Examples:
            'Federal Reserve interest rate decision March 2026'
            'semiconductor sector AI chip tariffs supply chain'
            'S&P 500 market sentiment rally correction'
            'NVDA AAPL quarterly earnings results'

        Args:
            query: Free-text search query.
            max_results: Number of results to return (default 8).

        Returns:
            JSON string with a list of news items, each containing:
            title, url, published (RFC 2822 timestamp), source, summary.
        """
        try:
            encoded = urllib.parse.quote(query)
            url = (
                f"https://news.google.com/rss/search"
                f"?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            )
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible)"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read().decode("utf-8")

            root = ET.fromstring(content)
            channel = root.find("channel")
            items = channel.findall("item") if channel is not None else []

            results = []
            for item in items[:max_results]:
                source_el = item.find("source")
                results.append({
                    "title": item.findtext("title", ""),
                    "url": item.findtext("link", ""),
                    "published": item.findtext("pubDate", ""),
                    "source": source_el.text if source_el is not None else "",
                    "summary": item.findtext("description", "")[:300],
                })

            logger.info("Google News RSS: %d results for query '%s'", len(results), query)
            return json.dumps(results, indent=2)
        except Exception as exc:
            logger.warning("Google News RSS failed for query '%s': %s", query, exc)
            return json.dumps({"error": str(exc), "query": query})
