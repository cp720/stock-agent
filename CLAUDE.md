# Agent Project — CLAUDE.md

## Project Overview

An AI-powered multi-agent stock trading system that analyzes equities using technical and fundamental analysis, then sends trade recommendations via webhook. All trading is **paper trading only** (Alpaca paper mode).

## Architecture

```
pm_agent.py          ← Entry point; orchestrates the Team + executes trades
├── fundamental_analyst.py   ← Fundamental Analyst Agent (yFinance)
├── technical_analyst.py     ← Technical Analyst Agent (Alpaca + pandas-ta)
└── market_news_analyst.py   ← Market News Analyst Agent (DuckDuckGo)
screener.py          ← Dynamic watchlist generator (Alpaca ScreenerClient + yFinance)
trade_journal.py     ← Trade journal: Peewee models, P&L reporting, CLI
config.py            ← Loads all API keys from .env
watchlist.py         ← Static fallback watchlist (used if screener returns 0 results)
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
| `peewee` | ORM for trade journal (SQLite) |
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
source venv_agent/bin/activate   # or venv_agent

# Run full team analysis (screener runs automatically)
python pm_agent.py

# Test the dynamic screener standalone
python screener.py

# Run individual agents standalone
python fundamental_analyst.py
python technical_analyst.py
```

## Dynamic Screener

Each run, `screener.py` replaces the static watchlist with a market-driven candidate list. The static `watchlist.py` is kept only as a fallback (used when the screener returns 0 results).

**Two-stage pipeline:**

| Stage | Source | What it pulls |
|---|---|---|
| Stage 1 | Alpaca `ScreenerClient` | Most actives (top 25 by volume) + top movers: gainers (15) + losers (15) |
| Stage 1 | yFinance `yf.screen()` | `"day_gainers"` (top 25) + `"most_actives"` (top 25) |
| Stage 2 | Alpaca daily bars | Filter by price ≥ $5 and RVOL ≥ 1.5× — sort by RVOL descending, cap at 15 |

**RVOL (Relative Volume)** = today's bar volume ÷ 30-day average volume. High RVOL signals unusual activity (earnings, news, institutional flow) — the key signal to prioritize for deeper analysis.

**Scalability path (future):**
- News heat layer — yfinance `.get_news()` count in last 24h as a tiebreaker
- Earnings calendar filter — avoid or target earnings via yfinance `.calendar`
- Sector concentration cap — limit to N tickers per sector
- FMP fundamental pre-filter — eliminate junk before running full analysis

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

The PM agent flow: Phase 0 (classify) → Phase 1 (investigate) → Phase 2 (account check + risk assessment) → Phase 3 (risk-sizing) → **Phase 4 (execute)** → Phase 5 (notify) → Phase 6 (journal).

## Portfolio Risk Assessment

The PM agent calls `get_portfolio_risk_assessment()` in Phase 2 to get a comprehensive portfolio snapshot before making trade decisions. Returns four sections:

- **Exposure Summary** — total equity, cash, invested amount, cash/invested percentages, number of positions
- **Per-Position Detail** — for each holding: market value, cost basis, unrealized P&L ($ and %), portfolio weight %, intraday P&L
- **Risk Metrics** — largest position concentration, total unrealized P&L, intraday P&L, drawdown from peak equity, day-over-day change
- **Advisory Risk Flags** — human-readable warnings when thresholds are breached:
  - `HIGH CONCENTRATION` — single position ≥ 12% of equity
  - `PORTFOLIO DRAWDOWN` — equity ≥ 5% below peak
  - `LOW CASH` — cash < 20% of equity
  - `HEAVY EXPOSURE` — invested > 80% of equity
  - `INTRADAY LOSS` — portfolio down > 1% today
  - `UNREALIZED LOSS` — total unrealized P&L ≤ -5%

Risk flags are **advisory only** — they inform the PM agent's sizing and decision logic in Phase 3 but do not hard-block any action.

## Notifications

Trade recommendations and execution results are sent via **n8n webhook** using `send_n8n_notification()` in `pm_agent.py`. The webhook payload includes `execution_status`, `order_id`, and `filled_price`.

## Trade Journal / P&L Tracker

Every decision (BUY, SELL, HOLD, skipped, failed) is automatically logged to a SQLite database at `data/trade_journal.db` using Peewee ORM. Signal attribution data (all technical indicators, fundamental score, news sentiment) is captured via the `log_trade_signals` tool in Phase 6.

**CLI reporting:**
```bash
python trade_journal.py report      # P&L summary + per-ticker breakdown
python trade_journal.py decisions   # Last 20 trade decisions
python trade_journal.py signals     # Per-signal attribution analysis
python trade_journal.py positions   # Open positions in journal
python trade_journal.py all         # All reports
```

**Schema:** 4 tables — `trade_decisions`, `signal_snapshots`, `open_positions`, `equity_snapshots`. Position lifecycle uses FIFO matching (oldest BUY closed first on SELL).

## Two Virtual Environments

- `venv/` — base environment
- `venv_agent/` — alternate environment (also has `playhouse`/peewee, `soupsieve`)

Use whichever is active; both should have the required packages.

## Important Notes

- **Paper trading only** — `TradingClient(..., paper=True)` is hardcoded
- API keys must never be committed; they live in `.env` (gitignored)
- The n8n webhook URL in `pm_agent.py` is a test webhook endpoint
