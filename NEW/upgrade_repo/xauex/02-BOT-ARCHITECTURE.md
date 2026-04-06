# 02 — XAUEX: Bot Architecture

## Process Model

Single Python asyncio event loop. All modules are coroutines. No threading. All blocking I/O is async.

```
main.py
  └── BotOrchestrator
        ├── ApiClient          (cTrader Open API connection)
        ├── LevelManager       (HTF levels, weekly refresh)
        ├── CandleWatcher      (H1 bar close detection)
        ├── PatternDetector    (candle classification)
        ├── SessionFilter      (time gates)
        ├── NewsFilter         (economic event gate)
        ├── RiskGates          (daily/weekly/consecutive loss gates)
        ├── PositionManager    (open trade tracking)
        ├── Executor           (order placement)
        └── StateWriter        (JSON state file output)
```

---

## Startup Sequence

On process start, run in this order before entering the main loop:

1. Load and validate config from `.env` — exit on any missing or invalid value
2. Check `CTRADER_TOKEN_EXPIRY` — refresh OAuth token if within 5 minutes of expiry
3. Connect to cTrader Open API (`demo-uk-eqx-01.p.c-trader.com:5035`, SSL)
4. Authenticate with access token
5. Fetch account state: balance, equity, open positions
6. Fetch XAUUSD symbol spec: lot size, min/max/step volume, digits, pip value
7. Fetch H1 bar history: minimum 200 bars for XAUUSD
8. Compute HTF levels (weekly/monthly OHLC) — see `03-HTF-LEVELS.md`
9. Restore risk gate state from `state.json` if it exists (handles restart mid-day)
10. Fetch and cache economic calendar for current week
11. Write initial state file
12. Subscribe to XAUUSD spot price tick stream
13. Poll `cmd.json` for kill switch every 10 seconds (background task)
14. Enter main event loop

If any step 1–10 fails, exit with non-zero code. systemd restarts after 30 seconds.

---

## Main Event Loop

Tick-driven. On each incoming price tick from the XAUUSD stream:

```
on_tick(price, timestamp):
  1. Update CandleWatcher with tick timestamp
  2. If H1 candle has NOT closed → return
  3. If H1 candle HAS closed:
     a. Fetch last 3 confirmed closed H1 bars from API
     b. LevelManager.refresh_if_needed()
     c. SessionFilter.is_tradeable(now_utc)         → skip if False
     d. RiskGates.can_trade()                        → skip if False
     e. NewsFilter.is_clear(now_utc)                 → skip if False
     f. LevelManager.price_at_level(candle.close)    → skip if None
     g. PatternDetector.detect(prev, signal, level)  → skip if NONE
     h. RiskSizing.calculate_lot_size(...)           → skip if None
     i. Executor.place_order(pattern_result, lot)
  4. Executor.check_pending_inside_bar_orders()
  5. StateWriter.write(current_state)
```

Steps 3c–3i run only on H1 candle close. The tick handler only updates the candle watcher on non-close ticks.

Always fetch confirmed closed bars from the API for pattern detection. Never use in-memory tick accumulation for OHLC construction.

---

## H1 Candle Close Detection

```python
class CandleWatcher:
    def __init__(self):
        self.current_candle_open: datetime | None = None

    def on_tick(self, tick_time: datetime) -> bool:
        """Returns True when a new H1 candle has opened (previous one closed)."""
        candle_open = tick_time.replace(minute=0, second=0, microsecond=0)
        if self.current_candle_open is None:
            self.current_candle_open = candle_open
            return False
        if candle_open != self.current_candle_open:
            self.current_candle_open = candle_open
            return True
        return False
```

On candle close detection, fetch the last 3 H1 bars from the API to get confirmed OHLC. Index -1 is the newly opened (forming) bar. Index -2 is the just-closed signal candle. Index -3 is the previous candle (needed for engulfing and inside bar detection).

---

## OAuth Token Management

- Store `access_token`, `refresh_token`, `token_expiry` (Unix timestamp) in `.env`
- On startup: if `token_expiry - now < 300` seconds, refresh before connecting
- During operation: check before every order placement
- On successful refresh: overwrite token fields in `.env` file atomically
- On refresh failure: set `bot_status = HALTED_AUTH_FAILURE`, write state, log CRITICAL, stop trading (do not exit process — maintain state file updates)

---

## Kill Switch Polling

Background coroutine, runs every 10 seconds:

```python
async def poll_kill_switch():
    while True:
        await asyncio.sleep(10)
        try:
            cmd = read_json(config.cmd_file_path)
            if cmd.get("kill_switch") is True:
                log.warning("[KILL SWITCH] Activated. Halting trading.")
                orchestrator.set_status("HALTED_KILL_SWITCH")
                orchestrator.write_state()
        except FileNotFoundError:
            pass
```

Kill switch halts new trade entry only. Does not close or modify existing positions. Bot process continues running.

To resume: dashboard writes `{"kill_switch": false}` to `cmd.json`.

---

## Error Handling Policy

| Error | Action |
|---|---|
| Network disconnect | Reconnect with exponential backoff: 1s, 2s, 4s, 8s, max 60s |
| API error on order | Log, skip this signal, do not retry |
| Invalid OHLC from API | Log, skip level refresh, retain previous levels |
| Token expiry | Refresh; if fails, halt and write state |
| Unhandled exception | Log full traceback, exit (systemd restarts) |

Never silently swallow exceptions. Every caught exception must be logged with full context.

---

## Reconnection Logic

On disconnect:
1. Set `bot_status = RECONNECTING`, write state file
2. Cancel active subscriptions
3. Wait backoff interval
4. Re-run startup sequence from step 3 (connect) onwards
5. On reconnect: fetch position state from API — do not assume any order was filled or cancelled during disconnect

---

## Observe-Only Mode

When `OBSERVE_ONLY=true` in `.env`:
- All modules run normally including signal detection and risk gate evaluation
- No orders are placed
- All signals logged as: `[EXECUTOR] OBSERVE_ONLY — would have placed LONG 0.03 lots at ~2720.45`
- State file written as normal
- Use this mode for the full first week of operation to validate signal quality before enabling live orders

---

## Graceful Shutdown

On `SIGTERM` or `SIGINT`:
1. Stop accepting new signals
2. Write `SHUTDOWN` to `bot_status` in state file
3. Log shutdown with UTC timestamp
4. Close API connection cleanly
5. Exit code 0

Do not force-close positions on shutdown. Operator manages open positions when stopping deliberately.

---

## State Written to `state.json` on Every H1 Close

The `StateWriter` is called at the end of every H1 candle close cycle regardless of whether a trade was taken. Fields written include:

- Current account balance and equity
- All open positions with unrealised P&L
- Last 20 H1 close prices (rolling buffer — maintain in memory)
- Trade entry markers on the chart buffer
- Last signal result (pattern, level, gate result, action)
- Risk gate state (consecutive losses, weekly P&L, halted flags)
- Active HTF levels
- Bot status
- Last error (if any)

See `08-DASHBOARD.md` for the complete `state.json` schema.
