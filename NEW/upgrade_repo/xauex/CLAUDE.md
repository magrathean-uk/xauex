# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make test                          # Run full pytest suite
make test-fast                     # Stop on first failure, short output
pytest tests/test_patterns.py -v   # Run a single test file
pytest tests/ -k "test_name" -v    # Run a specific test by name
make lint                          # ruff + mypy checks
make build                         # Compile Rust tick_parser wheel (maturin)
make install                       # Build + pip install tick_parser wheel
make backtest DATE_FROM=2022-01-01 DATE_TO=2026-02-28
make download DATE_FROM=2022 DATE_TO=2026
make auth                          # One-time cTrader OAuth2 flow
make dashboard                     # Launch Textual TUI
```

All async tests use `pytest-asyncio` with `asyncio_mode = auto` (no decorator needed).

## Architecture

**XAUEX** is a headless async trading bot for XAUUSD (Gold) via the cTrader Open API, with a separate Textual TUI dashboard process.

### Tick-to-Order Data Flow

```
cTrader TCP/SSL → proto_transport.py (protobuf + 4-byte length framing)
  → ApiClient.on_tick()
    → BotOrchestrator.on_tick()
      ├─ Executor: update trailing stops
      └─ On H1 candle close → _process_candle_close()
           ├─ LevelManager: proximity to HTF level (weekly/monthly OHLC)
           ├─ PatternDetector: Pinbar or Engulfing on H1 close
           ├─ SessionFilter: London/NY hours only
           ├─ NewsFilter: no high-impact events ±30 min
           ├─ RiskGates: consecutive losses, weekly drawdown, OBSERVE_ONLY
           └─ Executor.place_order() → SL/TP calculation → ProtoOANewOrderReq
```

### Key Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `main.py` | `BotOrchestrator`, asyncio event loop, candle detection, orchestration |
| `bot/api/client.py` | Connect, authenticate, subscribe to ticks, place orders, token refresh |
| `bot/api/proto_transport.py` | Low-level TCP/SSL framing, protobuf encode/decode |
| `bot/api/reconciler.py` | Sync local position state with broker on startup/reconnect |
| `bot/levels/htf_levels.py` | Weekly/monthly OHLC level computation and weekly cache |
| `bot/patterns/detector.py` | Pinbar and Engulfing detection on H1 bars |
| `bot/filters/session.py` | London/NY session gating (blocks Fri after 16:00 UK) |
| `bot/filters/news.py` | ForexFactory scraper; blocks ±30 min around high-impact USD events |
| `bot/filters/trend.py` | D1/H1 dual-EMA trend bias |
| `bot/risk/gates.py` | Final Go/No-Go: consecutive losses, weekly drawdown, OBSERVE_ONLY |
| `bot/risk/sizing.py` | 1% risk rule → lot size calculation |
| `bot/execution/executor.py` | Order placement, open position tracking, trailing stop updates |
| `bot/state/writer.py` | Writes `/var/lib/xauex/state.json` every tick for the dashboard |
| `bot/state/risk_persistence.py` | Persists daily/weekly PnL counters across restarts |
| `bot/watchdog.py` | Zombie detection (no ticks >3 min → force reconnect) |
| `config.py` | Loads all settings from `.env` via python-dotenv |
| `dashboard.py` | Separate process: reads state.json every 2s, writes cmd.json kill switch |
| `tick_parser/` | Rust/PyO3 extension (via maturin) for fast Dukascopy tick parsing |

### Strategy Logic

**Primary: `LEGACY_LEVELS`** — 8 HTF levels (previous weekly + monthly OHLC). Trade when: price within ±$3 of a level + Pinbar/Engulfing pattern + session + news + risk gates pass. Entry at market, SL beyond level swing, TP at next HTF level.

**Shadow: `EMA_PULLBACK_H1`** — Runs in parallel for analysis. D1/H1 dual-EMA trend bias, enters pullbacks to EMA with pattern confirmation. Controlled by `STRATEGY_MODE` / `SHADOW_STRATEGY_MODE` in `.env`.

### Hard Risk Rules (Never Negotiate)

- 1% account risk per trade (position size calculated mechanically)
- 3 consecutive losses in one day → halt all trades that calendar day
- 5% weekly drawdown → halt until Monday 00:00 UTC
- Never move SL further from entry; never add to losing positions
- Max 2 concurrent open positions

### Inter-Process Communication

- `state.json` at `STATE_FILE_PATH` (default `/var/lib/xauex/state.json`): bot writes every tick, dashboard reads every 2s
- `cmd.json` at same directory: dashboard writes kill-switch commands, bot polls
- `~/.xauex_risk_state.json`: risk persistence across restarts (daily/weekly PnL counters)
- Health endpoint: `http://localhost:8051/health` (JSON: status, last_tick_age_seconds, positions)

### Configuration

All settings from `.env` (see `.env.example`). Critical: `OBSERVE_ONLY=true` is the default — no real orders are placed until explicitly set to `false` after demo validation. `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `CTRADER_ACCOUNT_ID`, and `CTRADER_ACCESS_TOKEN` are required for any live connection.

### Architecture Docs

Numbered `.md` files in the repo root (`00-PROJECT-OVERVIEW.md` through `11-EXECUTION-MODULE.md`) document each domain in depth. `TECHNICAL_DOCUMENTATION.md` covers system architecture. `STATUS_REPORT.md` tracks current operational state.
