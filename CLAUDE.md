# Agent Project — CLAUDE.md

## Project Overview

An AI-powered multi-agent stock trading system that analyzes equities using technical and fundamental analysis, then sends trade recommendations via webhook. All trading is **paper trading only** (Alpaca paper mode).

## Architecture

```
pm_agent.py          ← Entry point; orchestrates the Team + executes trades
├── fundamental_analyst.py   ← Fundamental Analyst Agent (yFinance)
├── technical_analyst.py     ← Technical Analyst Agent (Alpaca + pandas-ta)
└── market_news_analyst.py   ← Market News Analyst Agent (DuckDuckGo)
config.py            ← Loads all API keys from .env
watchlist.py         ← Tickers to scan each run
```

### Agent Team (Agno framework)
- **Portfolio Management Team** (`pm_agent.py`) — `agno.team.Team`, model: `gpt-4.1`
  - Coordinates sub-agents, checks account/portfolio, applies risk math, executes trades via Alpaca, fires n8n webhook
- **Fundamental Analyst** (`fundamental_analyst.py`) — `agno.agent.Agent`, model: `gpt-4.1`
  - Uses `YFinanceTools` for valuation ratios, earnings, news, analyst consensus; outputs score 1–10
- **Technical Analyst** (`technical_analyst.py`) — `agno.agent.Agent`, model: `gpt-4.1`
  - Fetches 60-day OHLCV from Alpaca, calculates RSI-14 and ROC-10 via `pandas-ta`

## Key Dependencies

| Package | Purpose |
|---|---|
| `agno` | Agent/Team framework |
| `openai` | LLM backend (GPT-4.1) |
| `alpaca-py` | Historical data + paper trading |
| `yfinance` | Fundamental data |
| `pandas-ta-openbb` | Technical indicators (RSI, ROC) |
| `python-dotenv` | Secrets management |

## Environment Variables (`.env`)

```
ALPACA_API_KEY=          # Market data
ALPACA_API_SECRET=       # Market data
ALPACA_TRADING_API_KEY=  # Paper trading
ALPACA_TRADING_SECRET=   # Paper trading
ALPACA_BASE_URL=         # Alpaca base URL
OPENAI_API_KEY=          # OpenAI API
```

## Running the Project

```bash
# Activate virtual environment
source venv/bin/activate   # or venv_agent

# Run full team analysis
python pm_agent.py

# Run individual agents standalone
python fundamental_analyst.py
python technical_analyst.py
```

## Trade Decision Logic

1. **Fundamental Score > 7** AND **Technical = Bullish (RSI not overbought, Momentum positive)** → BUY
2. **Fundamental Score < 4** AND **Technical = Bearish** → SELL (if held)
3. Otherwise → HOLD

**Position sizing formula:** `Shares = (Equity × 0.10) / CurrentPrice` (10% equity risk per trade)

## Trade Execution

After the PM agent decides BUY/SELL/HOLD, it executes trades via `execute_trade()` in `pm_agent.py` using Alpaca's paper trading API. Built-in safeguards:

- **Market hours check** — orders only submitted when market is open
- **No short selling** — SELL orders skipped if position not held
- **Max position size** — no single position > 15% of total equity (`MAX_POSITION_PCT`)
- **Daily trade limit** — max 10 trades per day (`MAX_DAILY_TRADES`)

The PM agent flow: Phase 0 (classify) → Phase 1 (investigate) → Phase 2 (account check) → Phase 3 (risk-sizing) → **Phase 4 (execute)** → Phase 5 (notify).

## Notifications

Trade recommendations and execution results are sent via **n8n webhook** using `send_n8n_notification()` in `pm_agent.py`. The webhook payload includes `execution_status`, `order_id`, and `filled_price`.

## Two Virtual Environments

- `venv/` — base environment
- `venv_agent/` — alternate environment (also has `playhouse`/peewee, `soupsieve`)

Use whichever is active; both should have the required packages.

## Important Notes

- **Paper trading only** — `TradingClient(..., paper=True)` is hardcoded
- API keys must never be committed; they live in `.env` (gitignored)
- The n8n webhook URL in `pm_agent.py` is a test webhook endpoint
