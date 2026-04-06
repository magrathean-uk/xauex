# 10 — XAUEX: Testing and Validation

## Philosophy

Each module is independently testable with no external dependencies. Tests use pure Python only — no live API connections, no real file paths, no network calls. All external dependencies are injected or mocked. The test suite must pass completely before any module proceeds to integration.

---

## Run Tests

```bash
cd /opt/xauex
source .venv/bin/activate
pytest tests/ -v
```

Exit code 0, zero skipped tests required before proceeding to next module.

---

## Module Test Checklist

### `tests/test_patterns.py`
Full list in `04-PATTERN-DETECTION.md`. Key cases:
- All three pattern types detected correctly at level
- All three rejected when thresholds not met
- Level alignment check: valid shape, wrong location → `NONE`
- Zero-range candle (doji) → `NONE`, no crash
- Pattern priority: engulfing beats pin bar when both conditions met on same candle

### `tests/test_risk.py`
Full list in `05-RISK-MODULE.md`. Key cases:
- Lot sizing formula correct for known inputs
- Below-minimum lot returns `None` (never rounded up)
- All gate types trigger at correct thresholds
- Weekly halt persists through subsequent wins
- Daily counter resets on new UTC day and on win
- State serialise → deserialise → identical values

### `tests/test_levels.py`
- Proximity: within threshold → returns level
- Proximity: outside threshold → returns `None`
- Deduplication: two levels within $1.0 → merged
- Next level direction: correct level returned above/below price
- Validation: invalid OHLC (high < low) → validation fails, previous levels retained
- Refresh trigger: different weekly bar `open_time` → refresh needed
- Refresh trigger: same `open_time` → no refresh

### `tests/test_filters.py`
Session filter: all boundary cases from `07-SESSION-FILTER.md`
News filter: all window and DST cases from `06-NEWS-FILTER.md`

---

## Integration Test: API Connection

Run manually before first deployment. No orders placed.

```bash
python tests/integration/test_api_connection.py
```

Verifies:
1. SSL connection to `demo-uk-eqx-01.p.c-trader.com:5035` succeeds
2. OAuth authentication with account 9911635 succeeds
3. Account balance returns approximately £3,000, currency GBP
4. XAUUSD symbol spec retrieved: lot size, min/max/step volume, digits
5. 200 H1 bars retrieved, all non-zero OHLC
6. Weekly bar retrieved and 8 level values extracted and valid
7. Tick stream active: receives at least 1 tick within 5 seconds
8. Token refresh flow succeeds (force-expire and re-auth)

---

## Integration Test: Paper Trade

Run manually on demo before enabling `OBSERVE_ONLY=false`.

```bash
python tests/integration/test_paper_trade.py
```

Steps:
1. Connect and authenticate
2. Get current XAUUSD bid/ask
3. Calculate lot size for £30 risk at $12 SL
4. Place a BUY market order with SL and TP
5. Verify order appears in open positions via API
6. Close the position via API
7. Verify closed position in trade history
8. Verify P&L calculated correctly
9. Verify `state.json` updated correctly after each step

---

## Smoke Test: State File

```bash
python tests/smoke/test_state_file.py
```

Verifies:
- State file created at configured path
- JSON valid and matches schema in `08-DASHBOARD.md`
- Atomic write works (no partial read possible)
- `cmd.json` kill switch field readable and writable

---

## Observe-Only Validation Week

Before switching to `OBSERVE_ONLY=false`, run the bot for one full trading week in observe mode. Manually verify:

- [ ] Levels refresh correctly on Sunday evening
- [ ] Session filter blocks signals outside 08:00–12:00 London
- [ ] Friday 16:00 cutoff fires at correct London time
- [ ] News filter blocks around at least one known event
- [ ] State file updates on every H1 close
- [ ] Dashboard displays correctly and updates every 2 seconds
- [ ] Kill switch from dashboard halts signal processing within 10 seconds
- [ ] Log file grows with no `ERROR` or `CRITICAL` entries
- [ ] Bot survives a 30-minute network interruption and reconnects cleanly
- [ ] Bot survives a process restart without losing risk gate state
- [ ] Compare bot's detected signals manually against your own chart observations — signals should align

Only after this checklist is complete and all items pass: set `OBSERVE_ONLY=false` and restart.
