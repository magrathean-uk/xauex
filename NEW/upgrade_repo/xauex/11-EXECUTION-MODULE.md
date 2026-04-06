# 11 — XAUEX: Execution Module

## Purpose

Place, track, and close orders via the cTrader Open API. This is the only module that communicates with the broker for order operations. All pre-conditions are verified by the orchestrator before the executor is called.

---

## Responsibilities

- Place market orders (pin bar, engulfing signals)
- Place stop orders (inside bar signals)
- Set SL and TP on every order at placement time
- Cancel stale inside bar stop orders after 2 H1 candles
- Track open positions via `PositionManager`
- Process position close events from the API stream
- Enforce: never move SL further from entry
- Enforce: never add to a losing position

---

## Market Order Placement

Called for: `BULLISH_PIN_BAR`, `BEARISH_PIN_BAR`, `BULLISH_ENGULFING`, `BEARISH_ENGULFING`

```python
async def place_market_order(
    direction: int,            # +1 long, -1 short
    lot_size: float,
    stop_loss_price: float,
    take_profit_price: float,
    pattern: PatternType,
    level: float,
) -> str | None:               # position_id or None on failure
```

**SL price (set by orchestrator, validated here):**
- Long: `level - sl_distance` where `sl_distance` ∈ `[SL_MIN_DOLLARS, SL_MAX_DOLLARS]`
- Short: `level + sl_distance`

**TP price:**
- Use `LevelManager.next_level_from(entry_price, direction)`
- If no next level: use `entry + 2 × sl_distance` (2:1 R:R minimum)

SL and TP must be set at order placement. Never set after fill.

**Pre-placement checks:**
1. `lot_size > 0`
2. SL distance within `[SL_MIN_DOLLARS, SL_MAX_DOLLARS]`
3. SL distance > 3× current spread
4. `PositionManager.count() < MAX_OPEN_TRADES`

Any check failing → log reason, return `None`.

---

## Inside Bar Stop Orders

Called for: `INSIDE_BAR`

```python
async def place_inside_bar_orders(
    mother_bar_high: float,
    mother_bar_low: float,
    lot_size: float,
    level: float,
) -> tuple[str | None, str | None]:   # (buy_stop_id, sell_stop_id)
```

- BUY_STOP at `mother_bar_high + spread_buffer` ($1.00 fallback)
- SELL_STOP at `mother_bar_low - spread_buffer`
- SL for BUY_STOP: `mother_bar_low - SL_MIN_DOLLARS`
- SL for SELL_STOP: `mother_bar_high + SL_MIN_DOLLARS`
- TP: same logic as market orders

Store both order IDs in pending tracker with creation candle index.

---

## Inside Bar Order Lifecycle

On every H1 candle close:

```python
async def check_pending_inside_bar_orders(current_candle_index: int) -> None:
    for pair in pending_pairs:
        if current_candle_index - pair.created_at_candle >= 2:
            await cancel_order(pair.buy_stop_id)
            await cancel_order(pair.sell_stop_id)
            log.info("[EXECUTOR] Inside bar orders expired after 2 candles. Cancelled.")
```

When one stop order fills (position opened event received):

```python
async def on_order_filled(order_id: str) -> None:
    if order_id in inside_bar_pairs:
        companion_id = inside_bar_pairs[order_id].companion
        await cancel_order(companion_id)
```

---

## Stop Loss Modification Guard

```python
def validate_sl_modification(position: TrackedPosition, new_sl: float) -> bool:
    if position.direction == "LONG" and new_sl < position.stop_loss:
        log.warning("[EXECUTOR] SL modification rejected: moving SL further from entry.")
        return False
    if position.direction == "SHORT" and new_sl > position.stop_loss:
        log.warning("[EXECUTOR] SL modification rejected: moving SL further from entry.")
        return False
    return True
```

In v1 the bot does not trail stops — SL is set at entry and never modified. This guard exists to prevent accidental future changes.

---

## Position Tracking

```python
@dataclass
class TrackedPosition:
    position_id: str
    direction: str        # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    open_time_utc: datetime
    pattern: PatternType
    level: float

class PositionManager:
    def add(self, position: TrackedPosition) -> None: ...
    def remove(self, position_id: str) -> None: ...
    def get_open_positions(self) -> list[TrackedPosition]: ...
    def count(self) -> int: ...
```

On bot startup: fetch open positions from cTrader API and populate the tracker. Never assume it is empty after a restart.

---

## Position Close Handler

Subscribe to `ProtoOAPositionEvent` from the API stream. On position close:

1. Remove from `PositionManager`
2. Extract realised P&L from event
3. Call `RiskGates.record_trade_closed(pnl)`
4. Append to `closed_trades_today` in state
5. Write state file
6. Log:

```
[TRADE CLOSED] ID:789012 LONG | Entry:2720.45 SL:2708.00 TP:2745.00 | Close:2745.00 | P&L:+£62.40 | Pattern:BULLISH_PIN_BAR | Level:2720.00
```

---

## API Error Handling

| Error code | Action |
|---|---|
| `TRADING_DISABLED` | Log CRITICAL, set `HALTED_AUTH_FAILURE`, stop trading |
| `NOT_ENOUGH_MONEY` | Log ERROR, skip trade |
| `MARKET_CLOSED` | Log WARNING, skip trade |
| `POSITION_NOT_FOUND` | Log WARNING, remove from tracker |
| Any other error | Log full error code and message, skip trade |

Never retry a failed order placement. Log and wait for the next signal.

---

## Logging

Every placement attempt:
```
[EXECUTOR] Placing LONG | Lot:0.03 SL:2708.00 TP:2745.00 | Pattern:BULLISH_PIN_BAR Level:2720.00
[EXECUTOR] Order placed. Position ID: 789012
```

Every skip:
```
[EXECUTOR] Skipped. Reason: SL_BELOW_MIN_DISTANCE
```

Observe-only mode:
```
[EXECUTOR] OBSERVE_ONLY — would have placed LONG 0.03 lots. SL:2708.00 TP:2745.00
```
