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

**Position sizing:** ATR risk-based (see [Position Sizing](#position-sizing-atr-risk-based) below) — each BUY is sized so that hitting its ATR stop costs a conviction-scaled fraction of equity, capped at 15% of equity and available buying power.

## Trade Execution

After the PM agent decides BUY/SELL/HOLD, it executes trades via `execute_trade()` in `pm_agent.py` using Alpaca's paper trading API. Built-in safeguards:

- **Market hours check** — orders only submitted when market is open
- **No short selling** — SELL orders skipped if position not held
- **Max position size** — no single position > 15% of total equity (`MAX_POSITION_PCT`)
- **Daily trade limit** — max 10 trades per day (`MAX_DAILY_TRADES`)

The PM agent flow: Phase 0 (classify) → Phase 1 (investigate) → Phase 2 (account check + risk assessment) → Phase 3 (conviction scoring + risk-sizing) → **Phase 4 (execute)** → Phase 5 (save recommendation) → Phase 6 (journal).

### Conviction Score (Phase 3)

Phase 3 no longer uses rigid pass/fail gates. It computes a single **conviction score (0–100)** that blends every signal, then maps it to an action:

```
conviction = technical (0–45, votes/8 × 45 × ADX-confidence multiplier)
           + fundamental (0–40, score/10 × 40)
           + news (−15 to +15)
           − reversal/divergence penalties
```

- **≥ 62 → BUY** (size via ATR risk-based sizing — see below)
- **≤ 28 → SELL** (held positions only — no shorting)
- **29–61 → HOLD**
- Hysteresis: adding to a held position requires conviction ≥ 70. `CRITICAL_RISK: YES` still hard-overrides to SELL.

The score is persisted to `signal_snapshots.conviction_score`. Use `python trade_journal.py signals` to see win rate by conviction band and recalibrate the 62/28 thresholds from realized P&L.

### Position Sizing (ATR risk-based)

For BUY actions, Phase 3 calls the `calculate_position_size` tool rather than a fixed equity percentage. It sizes the position so that if the position's **ATR stop is hit, the loss equals a conviction-scaled fraction of equity**:

```
risk_pct       = 0.5% at conviction 62 → 1.5% at conviction 100   (RISK_PER_TRADE_MIN/MAX)
risk_budget    = equity × risk_pct
risk_per_share = bounded_ATR_stop_pct × price        (same stop the exit manager uses)
shares         = risk_budget / risk_per_share
```

The share count is then capped at `MAX_POSITION_PCT` (15% of equity, accounting for any existing position) and available buying power; the tool reports which bound was binding. Because entry size and exit stop derive from the **same bounded ATR distance**, volatile names get smaller positions and quiet names larger ones for identical dollar risk. Example at ~$80k equity: KO (low vol) ~116 shares at conviction 65; TSLA (high vol) ~18 shares at conviction 80 — same ~$500–800 risked. Falls back to the fixed 8% stop distance when ATR is unavailable.

## Exit Management

Held positions are managed mechanically by `manage_exits()` in `pm_agent.py`, which runs at the start of every watchlist scan (and standalone via `python pm_agent.py exits`). It is **separate from the discretionary agent flow** and **not subject to the daily trade limit** — risk-reducing exits are never blocked. For each position it applies, in priority order:

- **Stop-loss** — exit if price falls `ATR_STOP_MULT × ATR` below entry (volatility-adaptive)
- **Take-profit** — exit if up ≥ 20% from entry (`TAKE_PROFIT_PCT`)
- **Trailing stop** — once up ≥ 10% (`TRAILING_ACTIVATION_PCT`), exit if price falls `ATR_TRAIL_MULT × ATR` below the high-water mark

**ATR-adaptive stops:** stop distances scale to each stock's volatility instead of a flat percentage. The distance is `multiplier × ATR-14` expressed as a % of price, bounded to `[ATR_STOP_MIN_PCT, ATR_STOP_MAX_PCT]` (5–15%) so quiet names aren't stopped on noise and volatile names aren't given unbounded room. ATR is computed from Alpaca daily bars via the IEX feed (free-tier safe). If ATR can't be computed, the stop falls back to the fixed `STOP_LOSS_PCT` / `TRAILING_STOP_PCT` (8%). Example: KO → ~6.3% stop, AAPL → ~8.8%, TSLA → ~12.3%.

The high-water mark is persisted in `open_positions.high_water_mark`. Exits submit a market SELL and journal through the FIFO `close_oldest_position` path, so closed trades populate the conviction/signal performance reports.

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
