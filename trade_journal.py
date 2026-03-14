import os
import sys
import numpy as np
import pandas as pd
import peewee as pw
from datetime import datetime
from logger import get_logger

logger = get_logger(__name__)

# --- Database Setup ---
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trade_journal.db")


def get_database():
    """Create data directory and return database instance."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return pw.SqliteDatabase(DB_PATH, pragmas={
        'journal_mode': 'wal',
        'foreign_keys': 1,
    })


db = get_database()


class BaseModel(pw.Model):
    class Meta:
        database = db


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TradeDecision(BaseModel):
    """Every decision the PM agent makes — BUY, SELL, HOLD, skipped, failed."""
    id = pw.AutoField()
    timestamp = pw.DateTimeField(default=datetime.utcnow, index=True)
    ticker = pw.CharField(max_length=10, index=True)
    action = pw.CharField(max_length=10)                # BUY, SELL, HOLD
    quantity = pw.IntegerField(default=0)

    # Execution result
    execution_status = pw.CharField(max_length=20)      # executed, skipped, failed, hold
    order_id = pw.CharField(max_length=100, default="")
    filled_price = pw.FloatField(null=True)
    filled_qty = pw.IntegerField(null=True)
    execution_note = pw.TextField(default="")

    # Account snapshot at decision time
    equity = pw.FloatField(null=True)
    buying_power = pw.FloatField(null=True)
    cash = pw.FloatField(null=True)

    # LLM thesis
    thesis = pw.TextField(default="")

    class Meta:
        table_name = 'trade_decisions'


class SignalSnapshot(BaseModel):
    """Signal attribution data captured at decision time. One-to-one with TradeDecision."""
    id = pw.AutoField()
    decision = pw.ForeignKeyField(TradeDecision, backref='signals',
                                  unique=True, on_delete='CASCADE')

    # Technical signals
    overall_signal = pw.CharField(max_length=20, null=True)
    signal_confidence = pw.CharField(max_length=20, null=True)
    rsi_value = pw.FloatField(null=True)
    rsi_signal = pw.CharField(max_length=20, null=True)
    momentum_pct = pw.FloatField(null=True)
    momentum_signal = pw.CharField(max_length=20, null=True)
    macd_crossover = pw.CharField(max_length=20, null=True)
    price_vs_sma_20 = pw.CharField(max_length=10, null=True)
    price_vs_sma_50 = pw.CharField(max_length=10, null=True)
    price_vs_vwap = pw.CharField(max_length=10, null=True)
    adx_value = pw.FloatField(null=True)
    adx_direction = pw.CharField(max_length=20, null=True)
    bb_signal = pw.CharField(max_length=20, null=True)
    bb_squeeze = pw.BooleanField(null=True)
    bb_percent_b = pw.FloatField(null=True)
    obv_trend = pw.CharField(max_length=20, null=True)
    obv_divergence = pw.CharField(max_length=20, null=True)
    stoch_signal = pw.CharField(max_length=20, null=True)
    rsi_divergence = pw.CharField(max_length=30, null=True)
    macd_divergence = pw.CharField(max_length=30, null=True)
    reversal_alert = pw.CharField(max_length=40, null=True)
    reversal_factors = pw.TextField(null=True)
    technical_price = pw.FloatField(null=True)

    # Fundamental signals
    fundamental_score = pw.IntegerField(null=True)
    fundamental_key_metric = pw.TextField(null=True)

    # News signals
    news_sentiment = pw.CharField(max_length=20, null=True)
    critical_risk = pw.BooleanField(null=True)
    news_summary = pw.TextField(null=True)

    class Meta:
        table_name = 'signal_snapshots'


class OpenPosition(BaseModel):
    """Tracks position lifecycle: entry -> exit. FIFO matching."""
    id = pw.AutoField()
    ticker = pw.CharField(max_length=10, index=True)
    status = pw.CharField(max_length=10, default='open')   # open, closed

    # Entry
    entry_date = pw.DateTimeField()
    entry_price = pw.FloatField()
    entry_qty = pw.IntegerField()
    entry_decision = pw.ForeignKeyField(TradeDecision, backref='opened_positions',
                                        null=True, on_delete='SET NULL')

    # Exit (populated on close)
    exit_date = pw.DateTimeField(null=True)
    exit_price = pw.FloatField(null=True)
    exit_qty = pw.IntegerField(null=True)
    exit_decision = pw.ForeignKeyField(TradeDecision, backref='closed_positions',
                                       null=True, on_delete='SET NULL')

    # Calculated P&L
    realized_pnl = pw.FloatField(null=True)
    realized_pnl_pct = pw.FloatField(null=True)
    holding_days = pw.IntegerField(null=True)

    class Meta:
        table_name = 'open_positions'


class EquitySnapshot(BaseModel):
    """Equity over time for drawdown and Sharpe calculations."""
    id = pw.AutoField()
    timestamp = pw.DateTimeField(default=datetime.utcnow, index=True)
    equity = pw.FloatField()
    cash = pw.FloatField(null=True)
    buying_power = pw.FloatField(null=True)

    class Meta:
        table_name = 'equity_snapshots'


ALL_TABLES = [TradeDecision, SignalSnapshot, OpenPosition, EquitySnapshot]


def initialize_db():
    """Create tables if they don't exist. Safe to call multiple times."""
    try:
        db.connect(reuse_if_open=True)
        db.create_tables(ALL_TABLES, safe=True)
    except Exception as e:
        logger.error("Failed to initialize trade journal DB: %s", e)


# ---------------------------------------------------------------------------
# Position Lifecycle — FIFO
# ---------------------------------------------------------------------------

def close_oldest_position(ticker: str, exit_price: float, sell_qty: int,
                          exit_decision):
    """FIFO: close the oldest open position(s) for a ticker. Supports partial exits."""
    remaining = sell_qty

    open_positions = (OpenPosition
                      .select()
                      .where(
                          (OpenPosition.ticker == ticker) &
                          (OpenPosition.status == 'open')
                      )
                      .order_by(OpenPosition.entry_date.asc()))

    for pos in open_positions:
        if remaining <= 0:
            break

        if pos.entry_qty <= remaining:
            # Fully close this position
            pos.status = 'closed'
            pos.exit_date = datetime.utcnow()
            pos.exit_price = exit_price
            pos.exit_qty = pos.entry_qty
            pos.realized_pnl = (exit_price - pos.entry_price) * pos.entry_qty
            pos.realized_pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            pos.holding_days = (pos.exit_date - pos.entry_date).days
            pos.exit_decision = exit_decision
            pos.save()
            remaining -= pos.entry_qty
        else:
            # Partial close: close sold portion, keep remainder open
            closed_qty = remaining
            remainder_qty = pos.entry_qty - closed_qty

            pos.status = 'closed'
            pos.exit_date = datetime.utcnow()
            pos.exit_price = exit_price
            pos.exit_qty = closed_qty
            pos.realized_pnl = (exit_price - pos.entry_price) * closed_qty
            pos.realized_pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
            pos.holding_days = (pos.exit_date - pos.entry_date).days
            pos.exit_decision = exit_decision
            pos.save()

            # Create new open position for the remainder
            OpenPosition.create(
                ticker=ticker,
                status='open',
                entry_date=pos.entry_date,
                entry_price=pos.entry_price,
                entry_qty=remainder_qty,
                entry_decision=pos.entry_decision,
            )
            remaining = 0


# ---------------------------------------------------------------------------
# P&L Reporting
# ---------------------------------------------------------------------------

def _get_closed_positions_df() -> pd.DataFrame:
    """Load all closed positions into a pandas DataFrame."""
    initialize_db()
    query = (OpenPosition
             .select()
             .where(OpenPosition.status == 'closed')
             .order_by(OpenPosition.exit_date.asc()))

    records = []
    for p in query:
        records.append({
            'id': p.id,
            'ticker': p.ticker,
            'entry_date': p.entry_date,
            'entry_price': p.entry_price,
            'entry_qty': p.entry_qty,
            'exit_date': p.exit_date,
            'exit_price': p.exit_price,
            'exit_qty': p.exit_qty,
            'realized_pnl': p.realized_pnl,
            'realized_pnl_pct': p.realized_pnl_pct,
            'holding_days': p.holding_days,
            'entry_decision_id': p.entry_decision_id,
        })

    return pd.DataFrame(records) if records else pd.DataFrame()


def _calculate_max_drawdown() -> float:
    """Calculate max drawdown from equity snapshots."""
    snapshots = list(EquitySnapshot
                     .select(EquitySnapshot.equity)
                     .order_by(EquitySnapshot.timestamp.asc()))

    if len(snapshots) < 2:
        return 0.0

    equities = [s.equity for s in snapshots]
    peak = equities[0]
    max_dd = 0.0

    for eq in equities:
        if eq > peak:
            peak = eq
        dd = ((eq - peak) / peak) * 100
        if dd < max_dd:
            max_dd = dd

    return max_dd


def _calculate_sharpe_ratio(risk_free_rate: float = 0.05) -> float:
    """Annualized Sharpe ratio from equity snapshots (~252 trading days/year)."""
    snapshots = list(EquitySnapshot
                     .select(EquitySnapshot.equity)
                     .order_by(EquitySnapshot.timestamp.asc()))

    if len(snapshots) < 3:
        return 0.0

    equities = np.array([s.equity for s in snapshots])
    daily_returns = np.diff(equities) / equities[:-1]

    if len(daily_returns) == 0 or np.std(daily_returns) == 0:
        return 0.0

    daily_rf = risk_free_rate / 252
    excess_returns = daily_returns - daily_rf
    sharpe = (np.mean(excess_returns) / np.std(excess_returns)) * np.sqrt(252)

    return float(sharpe)


def report_summary():
    """Print overall P&L summary statistics."""
    initialize_db()
    df = _get_closed_positions_df()

    if df.empty:
        print("No closed positions found. Nothing to report.")
        return

    total_trades = len(df)
    winners = df[df['realized_pnl'] > 0]
    losers = df[df['realized_pnl'] < 0]

    win_rate = len(winners) / total_trades * 100
    total_pnl = df['realized_pnl'].sum()
    avg_gain = winners['realized_pnl_pct'].mean() if not winners.empty else 0
    avg_loss = losers['realized_pnl_pct'].mean() if not losers.empty else 0
    avg_holding = df['holding_days'].mean()
    max_dd = _calculate_max_drawdown()
    sharpe = _calculate_sharpe_ratio()

    print("=" * 60)
    print("TRADE JOURNAL — P&L SUMMARY")
    print("=" * 60)
    print(f"Total Closed Trades:   {total_trades}")
    print(f"Win Rate:              {win_rate:.1f}%")
    print(f"Total Realized P&L:    ${total_pnl:,.2f}")
    print(f"Avg Gain (winners):    {avg_gain:+.2f}%")
    print(f"Avg Loss (losers):     {avg_loss:+.2f}%")
    print(f"Avg Holding Period:    {avg_holding:.1f} days")
    print(f"Max Drawdown:          {max_dd:+.2f}%")
    print(f"Sharpe Ratio:          {sharpe:.2f}")
    print("-" * 60)

    best = df.loc[df['realized_pnl'].idxmax()]
    worst = df.loc[df['realized_pnl'].idxmin()]
    print(f"Best Trade:  {best['ticker']} "
          f"+${best['realized_pnl']:,.2f} ({best['realized_pnl_pct']:+.1f}%)")
    print(f"Worst Trade: {worst['ticker']} "
          f"${worst['realized_pnl']:,.2f} ({worst['realized_pnl_pct']:+.1f}%)")
    print("=" * 60)


def report_by_ticker():
    """Print P&L breakdown per ticker."""
    df = _get_closed_positions_df()
    if df.empty:
        print("No closed positions.")
        return

    print("\nPER-TICKER BREAKDOWN")
    print("-" * 60)

    for ticker, group in df.groupby('ticker'):
        wins = len(group[group['realized_pnl'] > 0])
        total = len(group)
        wr = wins / total * 100
        pnl = group['realized_pnl'].sum()
        avg_pnl_pct = group['realized_pnl_pct'].mean()
        print(f"  {ticker:6s}  Trades: {total:3d}  "
              f"Win Rate: {wr:5.1f}%  "
              f"Total P&L: ${pnl:>10,.2f}  "
              f"Avg: {avg_pnl_pct:+.1f}%")


def report_signal_performance():
    """Per-signal attribution: win rate and avg P&L by signal state."""
    initialize_db()

    # Join closed positions -> entry decision -> signal snapshot
    query = (OpenPosition
             .select(OpenPosition, SignalSnapshot)
             .join(TradeDecision, on=(OpenPosition.entry_decision == TradeDecision.id))
             .join(SignalSnapshot, on=(SignalSnapshot.decision == TradeDecision.id))
             .where(OpenPosition.status == 'closed'))

    records = []
    for pos in query:
        sig = pos.entry_decision.signals[0]
        records.append({
            'realized_pnl': pos.realized_pnl,
            'realized_pnl_pct': pos.realized_pnl_pct,
            'win': pos.realized_pnl > 0,
            'rsi_signal': sig.rsi_signal,
            'overall_signal': sig.overall_signal,
            'signal_confidence': sig.signal_confidence,
            'macd_crossover': sig.macd_crossover,
            'obv_trend': sig.obv_trend,
            'obv_divergence': sig.obv_divergence,
            'bb_signal': sig.bb_signal,
            'bb_squeeze': sig.bb_squeeze,
            'stoch_signal': sig.stoch_signal,
            'reversal_alert': sig.reversal_alert,
            'news_sentiment': sig.news_sentiment,
            'fundamental_score': sig.fundamental_score,
        })

    if not records:
        print("No signal data linked to closed positions yet.")
        return

    df = pd.DataFrame(records)

    print("\nSIGNAL ATTRIBUTION ANALYSIS")
    print("=" * 60)

    signal_analyses = [
        ('rsi_signal', ['Oversold', 'Overbought', 'Neutral']),
        ('overall_signal', ['Bullish', 'Bearish', 'Neutral']),
        ('signal_confidence', ['High', 'Moderate', 'Low']),
        ('macd_crossover', ['Bullish', 'Bearish']),
        ('obv_trend', ['Rising', 'Falling']),
        ('obv_divergence', ['Bullish', 'Bearish', 'None']),
        ('bb_signal', ['Overbought', 'Oversold', 'Neutral']),
        ('bb_squeeze', [True, False]),
        ('stoch_signal', ['Overbought', 'Oversold', 'Neutral']),
        ('reversal_alert', ['Potential Bearish Reversal', 'Potential Bullish Reversal', 'None']),
        ('news_sentiment', ['Positive', 'Negative', 'Neutral', 'Mixed']),
    ]

    for col, states in signal_analyses:
        print(f"\n  {col}:")
        for state in states:
            subset = df[df[col] == state]
            if subset.empty:
                continue
            n = len(subset)
            wr = subset['win'].mean() * 100
            avg = subset['realized_pnl_pct'].mean()
            print(f"    {str(state):30s}  n={n:3d}  "
                  f"Win Rate: {wr:5.1f}%  Avg P&L: {avg:+.1f}%")

    # Fundamental score buckets
    print(f"\n  fundamental_score (buckets):")
    for low, high, label in [(8, 10, "8-10 (Strong)"), (5, 7, "5-7 (Mixed)"), (1, 4, "1-4 (Weak)")]:
        subset = df[(df['fundamental_score'] >= low) & (df['fundamental_score'] <= high)]
        if subset.empty:
            continue
        n = len(subset)
        wr = subset['win'].mean() * 100
        avg = subset['realized_pnl_pct'].mean()
        print(f"    {label:30s}  n={n:3d}  "
              f"Win Rate: {wr:5.1f}%  Avg P&L: {avg:+.1f}%")


def report_decisions(limit: int = 20):
    """Print the most recent trade decisions."""
    initialize_db()
    decisions = (TradeDecision
                 .select()
                 .order_by(TradeDecision.timestamp.desc())
                 .limit(limit))

    print(f"\nLAST {limit} TRADE DECISIONS")
    print("-" * 80)
    for d in decisions:
        fp = f"${d.filled_price:.2f}" if d.filled_price else "N/A"
        print(f"  {d.timestamp:%Y-%m-%d %H:%M}  {d.ticker:6s}  "
              f"{d.action:5s}  qty={d.quantity:4d}  "
              f"status={d.execution_status:8s}  price={fp}")


def report_open_positions():
    """Print currently open positions tracked by the journal."""
    initialize_db()
    positions = (OpenPosition
                 .select()
                 .where(OpenPosition.status == 'open')
                 .order_by(OpenPosition.entry_date.asc()))

    print("\nOPEN POSITIONS (Journal)")
    print("-" * 60)
    count = 0
    for p in positions:
        print(f"  {p.ticker:6s}  qty={p.entry_qty:4d}  "
              f"entry=${p.entry_price:.2f}  "
              f"date={p.entry_date:%Y-%m-%d}")
        count += 1
    if count == 0:
        print("  No open positions.")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    """CLI: python trade_journal.py [report|decisions|signals|positions|all]"""
    command = sys.argv[1] if len(sys.argv) > 1 else "report"

    if command == "report":
        report_summary()
        report_by_ticker()
    elif command == "decisions":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        report_decisions(limit)
    elif command == "signals":
        report_signal_performance()
    elif command == "positions":
        report_open_positions()
    elif command == "all":
        report_summary()
        report_by_ticker()
        report_signal_performance()
        report_decisions()
        report_open_positions()
    else:
        print("Usage: python trade_journal.py [report|decisions|signals|positions|all]")


if __name__ == "__main__":
    main()
