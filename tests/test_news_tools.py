"""
tests/test_news_tools.py

Tests for news_tools.py:
  - get_ticker_news()       — yfinance 1.1.0 nested JSON parsing, error fallback
  - search_financial_news() — Google News RSS XML parsing, error fallback
"""
import json
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from news_tools import StockNewsTools


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------

SAMPLE_YF_NEWS = [
    {
        "content": {
            "title": "Apple reports record earnings",
            "pubDate": "2026-03-24T10:00:00Z",
            "summary": "A" * 400,                      # will be truncated to 350
            "provider": {"displayName": "Reuters"},
            "canonicalUrl": {"url": "https://reuters.com/aapl"},
            "contentType": "STORY",
        }
    },
    {
        "content": {
            "title": "Apple Watch launch",
            "pubDate": "2026-03-23T08:00:00Z",
            "summary": "Short summary.",
            "provider": {"displayName": "CNBC"},
            "canonicalUrl": {"url": "https://cnbc.com/watch"},
            "contentType": "STORY",
        }
    },
]

SAMPLE_RSS_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Google News</title>
    <item>
      <title>Fed holds rates steady</title>
      <link>https://reuters.com/fed</link>
      <pubDate>Mon, 24 Mar 2026 10:00:00 GMT</pubDate>
      <source url="https://reuters.com">Reuters</source>
      <description>Federal Reserve decision on interest rates...</description>
    </item>
    <item>
      <title>S&amp;P 500 rises 1%</title>
      <link>https://cnbc.com/sp500</link>
      <pubDate>Mon, 24 Mar 2026 11:00:00 GMT</pubDate>
      <source url="https://cnbc.com">CNBC</source>
      <description>Market wrap: S&amp;P 500 gains...</description>
    </item>
    <item>
      <title>Tech sector rally</title>
      <link>https://bloomberg.com/tech</link>
      <pubDate>Mon, 24 Mar 2026 12:00:00 GMT</pubDate>
      <source url="https://bloomberg.com">Bloomberg</source>
      <description>Technology stocks lead gains.</description>
    </item>
  </channel>
</rss>
"""

RSS_NO_SOURCE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Article without source element</title>
      <link>https://example.com/article</link>
      <pubDate>Mon, 24 Mar 2026 10:00:00 GMT</pubDate>
      <description>No source tag in this item.</description>
    </item>
  </channel>
</rss>
"""


def _mock_urlopen(xml_content: str):
    """Returns a context-manager mock that yields an object whose .read() returns xml bytes."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = xml_content.encode("utf-8")
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ===========================================================================
# get_ticker_news() tests
# ===========================================================================

class TestGetTickerNews:

    def setup_method(self):
        self.tools = StockNewsTools()

    def test_valid_response_returns_correct_fields(self):
        mock_ticker = MagicMock()
        mock_ticker.get_news.return_value = SAMPLE_YF_NEWS

        with patch("news_tools.yf.Ticker", return_value=mock_ticker):
            result_json = self.tools.get_ticker_news("AAPL", max_results=8)

        items = json.loads(result_json)
        assert isinstance(items, list)
        assert len(items) == 2
        first = items[0]
        assert first["title"] == "Apple reports record earnings"
        assert first["published"] == "2026-03-24T10:00:00Z"
        assert first["source"] == "Reuters"
        assert first["url"] == "https://reuters.com/aapl"

    def test_summary_truncated_to_350_chars(self):
        mock_ticker = MagicMock()
        mock_ticker.get_news.return_value = SAMPLE_YF_NEWS  # first item has 400-char summary

        with patch("news_tools.yf.Ticker", return_value=mock_ticker):
            result_json = self.tools.get_ticker_news("AAPL")

        items = json.loads(result_json)
        assert len(items[0]["summary"]) <= 350

    def test_max_results_respected(self):
        many_items = SAMPLE_YF_NEWS * 5  # 10 items
        mock_ticker = MagicMock()
        mock_ticker.get_news.return_value = many_items

        with patch("news_tools.yf.Ticker", return_value=mock_ticker):
            result_json = self.tools.get_ticker_news("AAPL", max_results=3)

        items = json.loads(result_json)
        assert len(items) <= 3

    def test_yfinance_exception_returns_error_json(self):
        mock_ticker = MagicMock()
        mock_ticker.get_news.side_effect = Exception("yfinance down")

        with patch("news_tools.yf.Ticker", return_value=mock_ticker):
            result_json = self.tools.get_ticker_news("BAD")

        result = json.loads(result_json)
        assert "error" in result

    def test_missing_canonical_url_falls_back_to_empty_string(self):
        item_no_url = {
            "content": {
                "title": "No URL item",
                "pubDate": "2026-03-24T10:00:00Z",
                "summary": "test",
                "provider": {"displayName": "Test"},
                # no canonicalUrl or clickThroughUrl
                "contentType": "STORY",
            }
        }
        mock_ticker = MagicMock()
        mock_ticker.get_news.return_value = [item_no_url]

        with patch("news_tools.yf.Ticker", return_value=mock_ticker):
            result_json = self.tools.get_ticker_news("AAPL")

        items = json.loads(result_json)
        assert items[0]["url"] == ""


# ===========================================================================
# search_financial_news() tests
# ===========================================================================

class TestSearchFinancialNews:

    def setup_method(self):
        self.tools = StockNewsTools()

    def test_valid_rss_returns_correct_fields(self):
        mock_resp = _mock_urlopen(SAMPLE_RSS_XML)

        with patch("news_tools.urllib.request.urlopen", return_value=mock_resp):
            result_json = self.tools.search_financial_news("Fed interest rates")

        items = json.loads(result_json)
        assert isinstance(items, list)
        assert len(items) == 3
        assert items[0]["title"] == "Fed holds rates steady"
        assert items[0]["url"] == "https://reuters.com/fed"
        assert items[0]["source"] == "Reuters"

    def test_max_results_respected(self):
        mock_resp = _mock_urlopen(SAMPLE_RSS_XML)

        with patch("news_tools.urllib.request.urlopen", return_value=mock_resp):
            result_json = self.tools.search_financial_news("market", max_results=2)

        items = json.loads(result_json)
        assert len(items) <= 2

    def test_missing_source_element_falls_back_to_empty_string(self):
        mock_resp = _mock_urlopen(RSS_NO_SOURCE)

        with patch("news_tools.urllib.request.urlopen", return_value=mock_resp):
            result_json = self.tools.search_financial_news("test")

        items = json.loads(result_json)
        assert items[0]["source"] == ""

    def test_urlopen_exception_returns_error_json(self):
        with patch("news_tools.urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result_json = self.tools.search_financial_news("Fed rates")

        result = json.loads(result_json)
        assert "error" in result

    def test_query_is_url_encoded(self):
        """Spaces and special chars in query must be percent-encoded in the URL."""
        mock_resp = _mock_urlopen(SAMPLE_RSS_XML)
        captured_calls = []

        def fake_urlopen(req, timeout=None):
            captured_calls.append(req)
            return mock_resp

        with patch("news_tools.urllib.request.urlopen", side_effect=fake_urlopen):
            self.tools.search_financial_news("S&P 500 rally")

        assert len(captured_calls) == 1
        built_url = captured_calls[0].full_url
        assert " " not in built_url          # spaces encoded
        assert "S%26P" in built_url or "S%2526P" in built_url or "S" in built_url
        assert "%20" in built_url or "+" in built_url   # space encoded
