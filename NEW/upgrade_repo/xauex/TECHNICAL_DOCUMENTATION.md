# XAUEX Technical Documentation

## 1. System Overview

XAUEX is an automated, event-driven trading bot for XAUUSD (Gold) running on the cTrader Open API. It is built with **Python 3** using **asyncio** for high-performance, non-blocking I/O. It does not use the official SDK's twisted reactor, relying instead on a custom pure-asyncio protobuf transport.

### Key Characteristics
- **Architecture**: Single-process asyncio event loop.
- **Protocol**: TCP with Protocol Buffers (Google Protobuf) via cTrader Open API.
- **Strategy**: Price Action (Pinbar/Engulfing) at HTF (Weekly/Monthly) levels.
- **Risk Management**: Hard-coded constraints (1% risk, max daily/weekly loss).
- **Observability**: JSON file-based state publishing + TUI Dashboard.

---

## 2. Directory Structure & Key Files

```text
/home/bolyki/xauex/
├── main.py                 # ENTRY POINT: BotOrchestrator, startup sequence, main tick loop
├── dashboard.py            # TUI Dashboard (Textual app) - runs separately
├── config.py               # Configuration loader (reads .env)
├── .env                    # Secrets and operational flags (OBSERVE_ONLY, credentials)
├── requirements.txt        # Python dependencies
├── bot/
│   ├── api/
│   │   ├── client.py           # cTrader API client (Auth, Orders, Market Data)
│   │   ├── proto_transport.py  # Low-level TCP/SSL transport with Int32 framing
│   │   ├── reconciler.py       # Syncs local state with broker (positions/orders)
│   │   └── models.py           # Data classes (Account, Position, SymbolSpec)
│   ├── execution/
│   │   ├── executor.py         # Order execution logic
│   │   ├── position_mgr.py     # In-memory position tracking
│   │   └── stop_mgr.py         # Trailing stop calculation
│   ├── filters/
│   │   ├── session.py          # Trading window (London/NY) gating
│   │   └── news.py             # High-impact news filter (ForexFactory)
│   ├── levels/
│   │   └── htf_levels.py       # Weekly/Monthly level calculation & caching
│   ├── patterns/
│   │   ├── detector.py         # Candlestick pattern recognition (Pinbar/Engulfing)
│   │   └── candle.py           # Candle data structure
│   ├── risk/
│   │   ├── gates.py            # Decision engine (Go/No-Go based on risk limits)
│   │   ├── sizing.py           # Position sizing (Risk %, Lot size calculation)
│   │   └── limits.py           # Hard drawdown limits (Daily/Weekly)
│   ├── state/
│   │   ├── writer.py           # Writes state.json for dashboard
│   │   └── risk_persistence.py # Persists PnL/drawdown state across restarts
│   ├── health.py               # HTTP Healthcheck endpoint (:8051/health)
│   └── watchdog.py             # Connection monitoring and auto-reconnection
└── docs/                       # Project specifications
```

---

## 3. Core Modules & Responsibilities

### 3.1. Bot Orchestrator (`main.py`)
- **Role**: The "brain" of the application.
- **Responsibilities**:
  - Initializes all sub-modules (API, Levels, Risk, Execution).
  - Manages the application lifecycle (Startup -> Run -> Shutdown).
  - Handles the primary `on_tick` event.
  - Triggers the H1 candle processing loop when a new hour begins.

### 3.2. API Client (`bot/api/`)
- **`client.py`**: High-level methods (`connect`, `subscribe`, `place_order`). Handles OAuth token refreshing automatically.
- **`proto_transport.py`**: Handles the TCP socket, SSL wrapping, and Protocol Buffer message framing (4-byte length prefix).
- **`reconciler.py`**: Critical safety module. Called on startup and reconnect to ensure the bot's internal list of positions matches the broker's reality.

### 3.3. Strategy Engine
- **`bot/levels/htf_levels.py`**:
  - Fetches D1/Weekly/Monthly history on startup.
  - Computes Support/Resistance levels (Open, High, Low, Close).
  - Levels are valid for the entire week/month.
- **`bot/patterns/detector.py`**:
  - Analyzes the **last closed H1 candle**.
  - Identifies **Pinbars** (long wick rejection) or **Engulfing** (reversal) patterns.
  - **Context Aware**: A pattern is only valid if it forms *at* a valid HTF level (within `LEVEL_PROXIMITY_DOLLARS`).

### 3.4. Risk & filters
- **`bot/filters/session.py`**: Allows trading only during specific hours (default: 08:00–17:00 London).
- **`bot/filters/news.py`**: Scrapes ForexFactory. Blocks trading 60m before/after high-impact USD events.
- **`bot/risk/gates.py`**: The final check before execution. Enforces:
  - Max daily losses (2).
  - Max weekly drawdown (6%).
  - `OBSERVE_ONLY` mode (prevents real orders).

### 3.5. Execution & State
- **`bot/execution/executor.py`**: Calculates lot size based on 1% risk and SL distance. Sends `ProtoOANewOrderReq`.
- **`bot/watchdog.py`**: Monitors connection health. Detects "zombie" connections (connected but no data) and forces a reconnect.
- **`bot/state/writer.py`**: Dumps current status to `/tmp/xauex_test_state.json` (or configured path) every tick/event. This drives the dashboard.

---

## 4. Operational Data Flow

### Step 1: Inbound Tick
1. `proto_transport` receives bytes -> decodes Protobuf message.
2. `client` routes SpotEvent to `main.on_tick()`.
3. `Executor` updates trailing stops immediately (if applicable).
4. `CandleWatcher` checks timestamp. If hour changed -> **Trigger Candle Close**.

### Step 2: Candle Close Logic (`_process_candle_close`)
1. **Fetch Data**: Get last 3 H1 trendbars from API.
2. **Analysis**:
   - Is price near a Weekly/Monthly level? (Proximity check)
   - Is the last candle a Pinbar or Engulfing pattern?
   - Is the direction consistent (e.g., Bullish pattern at Support)?
3. **Gating**:
   - Is it London/NY Session?
   - Is News clear?
   - Is Risk allowance available?
4. **Execution**:
   - Calculate Stop Loss (Swing High/Low).
   - Calculate Lot Size (1% Account Risk).
   - Submit Order to cTrader.

---

## 5. Configuration (`.env`)

| Variable | Description |
| :--- | :--- |
| `OBSERVE_ONLY` | `true` = Dry Run (Log signals only), `false` = Real Trading. |
| `CTRADER_ACCOUNT_ID` | The cTrader account number. |
| `CTRADER_TOKEN` | OAuth access token. |
| `RISK_PERCENT` | Risk per trade (default `0.01` = 1%). |
| `MAX_DAILY_LOSSES` | Stop trading after N consecutive losses (default `2`). |
| `STATE_FILE_PATH` | Where `main.py` writes status JSON (default `/tmp/xauex_test_state.json`). |

## 6. Monitoring

### Dashboard
The TUI dashboard reads the state file generated by the bot.
```bash
python3 dashboard.py
```

### Logs
Logs are standard output (stdout) or redirected to file.
- **Startup**: `[STARTUP]` tags.
- **Trading**: `[EXEC]`, `[RISK]`, `[PATTERN]` tags.
- **Errors**: `[ERROR]`, `[CRITICAL]` tags.
