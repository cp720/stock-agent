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
- **Protective bracket on every BUY** — each BUY is submitted as an Alpaca **bracket order**: a market entry plus two OCO exit legs that live broker-side (see [Exit Management](#exit-management))
- **Fill polling** — after submitting, `execute_trade` polls the order (~12s) for the actual fill price instead of returning `pending`, so the journal records real entry/exit prices (required for `open_positions` lifecycle and exit reconciliation)
- **SELLs cancel bracket legs first** — a position's shares are reserved by its open bracket exit legs, so `execute_trade` cancels the ticker's open orders (OCO: canceling one leg cancels its sibling) before submitting a SELL. After a **partial** SELL, it re-arms an OCO stop-loss/take-profit on the remaining shares, priced off the latest trade (`_rearm_exit_bracket`)

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
risk_per_share = bounded_ATR_stop_pct × price        (entry-side ATR stop reference)
shares         = risk_budget / risk_per_share
```

The share count is then capped at `MAX_POSITION_PCT` (15% of equity, accounting for any existing position) and available buying power; the tool reports which bound was binding. Because sizing uses a **bounded ATR distance**, volatile names get smaller positions and quiet names larger ones for identical dollar risk. Example at ~$80k equity: KO (low vol) ~116 shares at conviction 65; TSLA (high vol) ~18 shares at conviction 80 — same ~$500–800 risked. Falls back to the fixed 8% stop distance when ATR is unavailable.

> **Note:** sizing uses an ATR-based stop reference (5–15%), but the broker-side bracket attached at execution uses a **fixed 5% stop** (`STOP_LOSS_BRACKET_PCT`). When the ATR reference is wider than 5%, the realized stop is tighter, so actual dollar risk is **≤** the budgeted amount — sizing is the conservative bound.

## Exit Management

Exits are handled **broker-side**. Every BUY is submitted as an Alpaca **bracket order** (`OrderClass.BRACKET`) in `execute_trade()` — a market entry plus two OCO (one-cancels-other) exit legs that live at Alpaca:

- **Stop-loss leg** — `STOP_LOSS_BRACKET_PCT` (5%) below the entry reference price
- **Take-profit leg** — `TAKE_PROFIT_BRACKET_PCT` (30%) above the entry reference price

Whichever leg fills first cancels the other. Because the legs are submitted with `TimeInForce.GTC` and live at the broker, **positions are protected 24/7 even when this program is not running** — there is no software exit pass to schedule. Since a market entry's fill price is unknown at submission, the leg prices are derived from the latest IEX trade price (`_latest_price()`), so expect a few cents of slippage versus an exact 5%/30%.

**Consequences of the broker-side model:**

- There is no `manage_exits()` and no `python pm_agent.py exits` command anymore.
- Stops/take-profits are **fixed percentages**, not ATR-adaptive or trailing. (Position *sizing* still uses ATR; only the live stop is fixed.)
- When a bracket leg fills at Alpaca, this program isn't notified live, so the exit is journaled **after the fact** by reconciliation (see below), not at fill time.

### Exit Reconciliation

Because bracket legs fill broker-side while this program is offline, `reconcile_broker_exits()` in `pm_agent.py` syncs those fills into the journal. It scans the last `RECONCILE_LOOKBACK_DAYS` (7) of **filled SELL orders**, and for any whose Alpaca `order_id` isn't already in `trade_decisions`, it writes a SELL `TradeDecision` and closes the matching open position(s) via the FIFO `close_oldest_position` path (computing realized P&L). It runs automatically at the start of every `run_watchlist()` scan, and standalone via:

```bash
python pm_agent.py reconcile
```

- **Idempotent** — exits are keyed by `order_id`, so re-running only picks up new fills.
- **Exits only** — entries are still journaled by the normal agent flow (`save_recommendation`). A reconciled exit is skipped if the journal has no matching open position for that ticker.

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
