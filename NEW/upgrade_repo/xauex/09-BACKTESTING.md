# 09 — XAUEX: Backtesting

## Purpose

Validate strategy logic against historical XAUUSD tick data before enabling live demo orders. The backtest uses identical pattern detection, risk sizing, session filter, and news filter logic as the live bot — not a simplified version. Import the actual bot modules; do not reimplement them.

---

## Data Source: Dukascopy

Free historical bid/ask tick data for XAU/USD. Required — IC Markets' own history is insufficient for tick-level stop simulation prior to ~2020.

Download URL: https://www.dukascopy.com/swiss/english/marketwatch/historical/

Automated bulk download via `duka` (Python, PyPI):

```bash
pip install duka
duka --instrument XAUUSD --start 2022-01-01 --end 2026-01-01 --ticks --folder ./data/dukascopy
```

Verify downloaded files contain both bid and ask columns. If only one side, spread simulation will be inaccurate.

---

## Data Loader (`backtester/loader.py`)

```python
@dataclass
class Tick:
    timestamp: datetime   # UTC
    bid: float
    ask: float
    mid: float            # (bid + ask) / 2
    spread: float         # ask - bid

@dataclass
class OHLCBar:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    max_spread: float     # worst spread during this bar
    tick_count: int
```

Responsibilities:
- Load Dukascopy CSV tick files from directory
- Parse timestamp, bid, ask
- Compute mid and spread
- Resample ticks into H1 OHLC bars using mid price
- Retain tick-level data per bar for stop-loss hit simulation

---

## Backtest Engine (`backtester/engine.py`)

### Event Loop

Iterates H1 bars chronologically. On each bar close:

1. Check if levels need refresh (weekly boundary crossed in bar data)
2. Run session filter using bar close time
3. Run news filter against pre-loaded static event list
4. Check level proximity using bar close price
5. Run pattern detection on last two closed bars
6. If signal: calculate lot size, simulate entry on next bar open + spread
7. Check all open positions: did stop or TP get hit during this bar (tick-level check)

### Stop Loss Hit Simulation (Tick Level)

```
for each tick within the bar:
    if long position open:
        if tick.bid <= stop_loss_price → stopped out at stop_loss_price
    if short position open:
        if tick.ask >= stop_loss_price → stopped out at stop_loss_price
```

Fill price = stop loss price (no slippage model in v1 — conservative).

### Entry Simulation

Market order entries fill at next bar open:
- Long:  next_bar.open + half_spread
- Short: next_bar.open - half_spread

### News Filter in Backtest

Use a pre-downloaded static event CSV. Community-maintained historical ForexFactory data:
```
https://github.com/wukan1986/ForexFactory
```

Load on engine init. Filter logic is identical to live (`bot/filters/news.py`) — import and reuse it.

---

## Run Command

```bash
cd /opt/xauex
source .venv/bin/activate
python -m backtester.engine \
  --data ./data/dukascopy \
  --start 2022-01-01 \
  --end 2025-12-31
```

---

## Report (`backtester/report.py`)

Output to terminal on completion:

```
=== XAUEX BACKTEST REPORT ===
Period:              2022-01-01 to 2025-12-31
Instrument:          XAUUSD

Signals detected:    312
Trades taken:        187
  Skipped (session): 68
  Skipped (news):    31
  Skipped (risk):    26

Win rate:            54.5%
Profit factor:       1.42
Total P&L:           +£2,840.00
Max drawdown:        -£412.00 (13.7%)
Max consec. losses:  4
Avg R:R achieved:    1.8

Pattern breakdown:
  BULLISH_PIN_BAR:    28/48  wins (58.3%)
  BEARISH_PIN_BAR:    24/41  wins (58.5%)
  BULLISH_ENGULFING:  19/35  wins (54.3%)
  BEARISH_ENGULFING:  17/32  wins (53.1%)
  INSIDE_BAR:         13/31  wins (41.9%)

Weekly halt triggers: 8 weeks
Daily halt triggers:  14 days
```

---

## Pass Criteria Before Enabling Live Demo Orders

All must be met:

| Metric | Minimum |
|---|---|
| Profit factor | ≥ 1.30 |
| Win rate | ≥ 45% |
| Max drawdown | ≤ 20% |
| Max consecutive losses | ≤ 6 |
| Completed trades in sample | ≥ 100 |
| Test period | ≥ 2 years, includes both trending and ranging conditions |

If any criterion fails: investigate which pattern type or level category is failing before adjusting thresholds.

---

## Known Limitations

- Entry fill is simplified (next bar open + spread). Live fills may differ by $0.50–$2.00 on fast moves.
- No weekend gap simulation in v1.
- News filter quality depends on the static event list completeness.
- Does not validate cTrader API or order execution code — that is validated separately on demo.
