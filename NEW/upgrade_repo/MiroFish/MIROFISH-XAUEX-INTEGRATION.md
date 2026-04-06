# MiroFish → XAUEX Gold Trading Integration

## What It Does

This system uses **MiroFish** (an AI swarm intelligence engine) to predict XAUUSD (gold) market direction, then automatically executes trades via **XAUEX** (a production-grade automated trading bot connected to IC Markets via cTrader).

MiroFish simulates dozens of AI agents representing real-world actors — central bankers, hedge funds, retail traders, geopolitical analysts — who react to news you provide and interact with each other. The swarm consensus becomes a BUY/SELL/HOLD signal that XAUEX executes.

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  News/Context │────▶│   MiroFish   │────▶│    Bridge    │────▶│    XAUEX     │
│  (text file)  │     │  (AI Swarm)  │     │  (DeepSeek)  │     │  (cTrader)   │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
     You write          Simulates            Parses sim          Executes trade
     market news        actor behaviour      into signal         on IC Markets
```

### Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **MiroFish** | `MiroFish/` | Vue 3 + Flask AI simulation engine |
| **Bridge** | `MiroFish/bridge/` | Orchestrates MiroFish API → DeepSeek parsing → signal file |
| **XAUEX** | `xauex/` | Async trading bot, cTrader Open API, risk management |

---

## How It Works

### 1. Input: Market News
You provide a text file with current gold market context — Fed statements, geopolitical events, inflation data, employment numbers, etc.

### 2. MiroFish Simulation
The bridge (`gold_oracle.py`) drives the MiroFish API:
1. **Ontology generation** — identifies key actors from your news
2. **Knowledge graph** — maps relationships between actors (via Zep Cloud)
3. **Multi-round simulation** — each actor is an AI agent (DeepSeek) that makes decisions based on their role and the news
4. **Report generation** — summarizes consensus, disagreements, key actions

### 3. Signal Parsing
`signal_parser.py` sends the simulation output to DeepSeek with a structured prompt asking: *"Based on this simulation, should we BUY, SELL, or HOLD gold?"*

Returns:
```json
{
  "action": "BUY",
  "confidence": 0.85,
  "reasoning": "Fed dovish pivot + safe-haven demand",
  "stop_loss_usd": 12.0,
  "take_profit_usd": 24.0,
  "timestamp_utc": "2026-04-03T10:30:00Z"
}
```

### 4. Signal Delivery
`signal_writer.py` atomically writes the signal into `cmd.json`, which XAUEX already polls every 10 seconds.

### 5. Trade Execution
XAUEX validates the signal:
- Confidence ≥ 0.6
- Signal age < 5 minutes
- Risk gates pass (max drawdown, position limits, consecutive losses)
- Lot size calculated at 1% account risk
- Order placed via cTrader Open API

---

## Setup & Running

### Prerequisites
- Python 3.12+ (MiroFish backend requires ≤3.12, XAUEX works on 3.13)
- Node.js 18+
- DeepSeek API key
- Zep Cloud API key (free tier: app.getzep.com)
- IC Markets cTrader account (demo or live)

### 1. MiroFish
```bash
cd MiroFish
cp .env.example .env
# Edit .env: set LLM_API_KEY, LLM_BASE_URL, ZEP_API_KEY
npm run setup
npm run dev
# Frontend: http://localhost:3000, Backend: http://localhost:5001
```

### 2. Bridge
```bash
cd MiroFish
pip install -r bridge/requirements.txt

# Run with news file:
DEEPSEEK_API_KEY=your-key python -m bridge.run --news bridge/sample_news.txt

# Dry run (prints signal, doesn't write cmd.json):
DEEPSEEK_API_KEY=your-key python -m bridge.run --news bridge/sample_news.txt --dry-run

# Custom output path:
DEEPSEEK_API_KEY=your-key python -m bridge.run --news news.txt --output /path/to/cmd.json
```

### 3. XAUEX
```bash
cd xauex
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set environment variables:
export MIROFISH_MODE=true
export MIROFISH_SIGNAL_PATH=/path/to/cmd.json
export OBSERVE_ONLY=false    # true to log without executing
export CTRADER_ACCOUNT_ID=your-account
export CTRADER_ACCESS_TOKEN=your-token

python main.py
```

---

## Configuration Reference

### Bridge Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `DEEPSEEK_API_KEY` | (required) | DeepSeek API key |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | API endpoint |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Model name |
| `MIROFISH_URL` | `http://localhost:5001` | MiroFish backend URL |
| `SIGNAL_OUTPUT_PATH` | `cmd.json` | Where to write trading signals |

### XAUEX MiroFish Config
| Variable | Default | Description |
|----------|---------|-------------|
| `MIROFISH_MODE` | `false` | Enable MiroFish signal reading (disables internal strategies) |
| `MIROFISH_SIGNAL_PATH` | `cmd.json` | Path to signal file |
| `MIROFISH_SIGNAL_MAX_AGE_SECONDS` | `300` | Max signal age before considered stale |

---

## API Cost Estimate

Using DeepSeek (cheapest option):
- **Per simulation run:** ~3.3M tokens → ~$0.30–$0.70 USD
- **Per signal parse:** ~2K tokens → <$0.01
- **Model used:** `deepseek-chat` (DeepSeek-V3)

---

## Key Files

### Bridge
- `bridge/gold_oracle.py` — Drives MiroFish API (ontology → graph → sim → report)
- `bridge/signal_parser.py` — DeepSeek parses simulation → BUY/SELL/HOLD
- `bridge/signal_writer.py` — Atomic write to cmd.json
- `bridge/run.py` — CLI entry point
- `bridge/config.py` — Configuration from environment
- `bridge/sample_news.txt` — Example market news input

### XAUEX (modified)
- `xauex/config.py` — Added MIROFISH_MODE fields
- `xauex/main.py` — Added `_poll_mirofish_signal()`, gated internal strategies
- `xauex/tests/test_mirofish_signal.py` — Signal parsing tests

---

## Risk & Disclaimers

- This is experimental software. **Use at your own risk.**
- Always test on a **demo account** first.
- The kill switch (`kill_switch: true` in cmd.json) stops all trading immediately.
- MiroFish provides **sentiment-based direction**, not price targets or technical analysis.
- Signal quality depends entirely on the news/context you feed it.
- Built-in risk gates: 5% weekly drawdown limit, 2% daily, max 3 consecutive losses, max 2 open positions.

---

## Tech Stack

| Technology | Usage |
|------------|-------|
| DeepSeek V3 | LLM for simulation agents + signal parsing |
| Zep Cloud | Knowledge graph storage |
| Vue 3 + Vite | MiroFish frontend |
| Flask | MiroFish backend API |
| asyncio + Protobuf | XAUEX cTrader connection |
| cTrader Open API | Order execution on IC Markets |
