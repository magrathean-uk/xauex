# 00 — XAUEX: Project Overview

## What This Is

XAUEX is a fully automated XAUUSD gold trading bot implementing a Higher Timeframe (HTF) support/resistance level strategy. It runs as a headless Python daemon on a Linux VPS with a Textual terminal dashboard for monitoring on the MATE desktop. It connects to IC Markets via the cTrader Open API and enforces strict mechanical risk rules at all times.

The bot is a longer-term project. Manual demo trading must prove the strategy profitable over 30+ trades before the bot is trusted with any capital. Development proceeds in parallel with manual trading.

---

## Component Map

```
┌─────────────────────────────────────────────────┐
│                Linux VPS (Hungary)               │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │          xauex daemon (Python)            │   │
│  │                                           │   │
│  │  - HTF level manager                     │   │
│  │  - H1 candle watcher                     │   │
│  │  - Pattern detector                      │   │
│  │  - Risk module                           │   │
│  │  - News filter                           │   │
│  │  - Session filter                        │   │
│  │  - Order executor                        │   │
│  │  - State file writer (JSON)              │   │
│  └──────────────┬───────────────────────────┘   │
│                 │ cTrader Open API               │
│                 │ protobuf / WebSocket            │
│                 ▼                                │
│  demo-uk-eqx-01.p.c-trader.com:5035             │
│  Account: 9911635 (IC Markets demo, GBP, 1:100) │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │     xauex-dashboard (Textual TUI)         │   │
│  │  reads /var/lib/xauex/state.json         │   │
│  │  writes /var/lib/xauex/cmd.json          │   │
│  │  runs in MATE terminal window            │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Broker API | cTrader Open API (`ctrader-open-api` PyPI) |
| Protocol | Protobuf over WebSocket |
| Auth | OAuth2, redirect URI `http://localhost:8050/callback` |
| Process management | systemd (`xauex.service`) |
| Terminal dashboard | Textual (Python TUI) |
| Config | `.env` file, never hardcoded |
| Backtesting | Custom Python engine on Dukascopy tick data |

---

## Repository Structure

```
xauex/
├── .env                        # credentials and config (never committed)
├── .env.example                # template with all required keys
├── requirements.txt
├── main.py                     # entry point, starts daemon
├── dashboard.py                # Textual TUI, run separately
├── auth.py                     # one-time OAuth2 flow on port 8050
├── config.py                   # loads and validates .env
├── bot/
│   ├── __init__.py
│   ├── api/
│   │   ├── client.py
│   │   └── models.py
│   ├── levels/
│   │   ├── htf_levels.py
│   │   └── level_store.py
│   ├── patterns/
│   │   └── detector.py
│   ├── filters/
│   │   ├── session.py
│   │   └── news.py
│   ├── risk/
│   │   ├── sizing.py
│   │   └── gates.py
│   ├── execution/
│   │   └── executor.py
│   └── state/
│       └── writer.py
├── backtester/
│   ├── loader.py
│   ├── engine.py
│   └── report.py
├── tests/
│   ├── test_patterns.py
│   ├── test_risk.py
│   ├── test_levels.py
│   ├── test_filters.py
│   └── integration/
│       ├── test_api_connection.py
│       └── test_paper_trade.py
├── ops/
│   └── xauex.service
└── docs/
    ├── 00-PROJECT-OVERVIEW.md
    ├── 01-INFRASTRUCTURE.md
    ├── 02-BOT-ARCHITECTURE.md
    ├── 03-HTF-LEVELS.md
    ├── 04-PATTERN-DETECTION.md
    ├── 05-RISK-MODULE.md
    ├── 06-NEWS-FILTER.md
    ├── 07-SESSION-FILTER.md
    ├── 08-DASHBOARD.md
    ├── 09-BACKTESTING.md
    ├── 10-TESTING-VALIDATION.md
    └── 11-EXECUTION-MODULE.md
```

---

## Strategy Summary

**Instrument:** XAUUSD only
**Account:** IC Markets cTrader demo, GBP, 1:100 leverage
**Risk per trade:** 1% of balance (≈£30 on £3,000)
**Max simultaneous trades:** 2

**Level mapping (weekly refresh):**
- Previous monthly candle OHLC → 4 levels
- Previous weekly candle OHLC → 4 levels
- 8 levels total, active until next Sunday refresh

**Entry logic (all must pass):**
1. Price within proximity of a mapped HTF level
2. H1 candle fully closed
3. Closed candle is pin bar, engulfing, or inside bar breakout
4. No red USD economic event within ±30 minutes
5. London session active: 08:00–12:00 UK time
6. All risk gates pass

**Stop loss:** 10–15 USD beyond the level
**Take profit:** Next mapped HTF level

---

## Risk Rules (Hard, Non-Negotiable)

- 3 consecutive losses in one calendar day → halt that day
- 5% account drawdown in one week → halt until Monday 00:00 UTC
- Never move stop loss further from entry
- Never add to a losing position
- No new trades Friday after 16:00 UK time
- Minimum stop distance must exceed 3× current spread

---

## Build Order

Build and independently test each module in this exact sequence. Do not proceed to the next until all tests for the current module pass.

1. `bot/api/client.py`
2. `bot/levels/htf_levels.py`
3. `bot/patterns/detector.py`
4. `bot/filters/session.py`
5. `bot/filters/news.py`
6. `bot/risk/sizing.py`
7. `bot/risk/gates.py`
8. `bot/execution/executor.py`
9. `bot/state/writer.py`
10. `main.py`
11. `dashboard.py`
12. `backtester/`

---

## What This Project Is Not

- Not a signals service or copy trading system
- Not grid, martingale, or averaging down
- Not multi-instrument — XAUUSD only
- Not production-ready until 30+ manual demo trades prove the edge
