# 05 — XAUEX: Risk Module

## Overview

Two components: lot sizing (`bot/risk/sizing.py`) and trading gates (`bot/risk/gates.py`). Both must pass before any order is placed. No override mechanism exists.

---

## Lot Sizing (`bot/risk/sizing.py`)

### Formula

```
risk_amount = account_balance × (RISK_PERCENT / 100)
sl_distance = abs(entry_price - stop_loss_price)

# XAUUSD: 1 standard lot = 100 troy oz
# P&L per lot per $1 move = $100
lot_size = risk_amount / (sl_distance × 100)
```

The `× 100` multiplier assumes 1 lot = 100 oz. Verify against the cTrader symbol spec on first connection using `GetSymbolByIdReq` — use the API-returned `lotSize` to derive this, not a hardcoded constant.

### Rounding

```python
# Floor to nearest volume step — never round up
lot_size = math.floor(lot_size / volume_step) * volume_step

# Skip if below minimum — do NOT round up to minimum
if lot_size < volume_min:
    return None   # skip this trade

lot_size = min(lot_size, volume_max)
return lot_size
```

Rounding up to minimum lot is strictly prohibited. It inflates actual risk above 1%. Return `None` and skip the trade.

### Pre-Calculation Validation

```python
if sl_distance < SL_MIN_DOLLARS:          return None  # stop too tight
if sl_distance > SL_MAX_DOLLARS:          return None  # stop too wide
if sl_distance < current_spread * 3.0:    return None  # inside 3× spread
```

### Interface

```python
def calculate_lot_size(
    account_balance: float,
    entry_price: float,
    stop_loss_price: float,
    symbol_spec: SymbolSpec,
    current_spread_usd: float,
    config: Config,
) -> float | None:
    """Returns lot size or None (skip trade). Caller must log the skip reason."""
```

---

## Trading Gates (`bot/risk/gates.py`)

All gates are checked in sequence. First failure stops evaluation and returns the reason code.

### Gate 1: Weekly Drawdown

```python
weekly_drawdown_pct = abs(weekly_pnl) / week_start_balance * 100
if weekly_pnl < 0 and weekly_drawdown_pct >= WEEKLY_STOP_PCT:  # 5.0
    return False, "WEEKLY_DRAWDOWN_LIMIT"
```

`week_start_balance` = account balance recorded at Monday 00:00 UTC. Stored in state file. Reset each Monday.

Weekly gate does not reset mid-week. Once triggered it stays closed until Monday 00:00 UTC regardless of subsequent wins.

### Gate 2: Daily Consecutive Losses

```python
if consecutive_losses_today >= MAX_CONSECUTIVE_LOSSES:  # 3
    return False, "DAILY_CONSECUTIVE_LOSS_LIMIT"
```

Counter resets to 0 at 00:00 UTC each day and resets to 0 on any winning trade. Does not roll over between days.

### Gate 3: Position Cap

```python
if open_position_count >= MAX_OPEN_TRADES:  # 2
    return False, "MAX_POSITIONS_REACHED"
```

Position count fetched live from `PositionManager`, not inferred.

### Interface

```python
class RiskGates:
    def can_trade(self) -> tuple[bool, str]:
        """Returns (True, 'OK') or (False, reason_code)."""

    def record_trade_closed(self, pnl: float) -> None:
        """Update consecutive loss counter and weekly P&L. Call on every position close."""

    def record_week_start(self, balance: float) -> None:
        """Snapshot week-start balance. Call at Monday 00:00 UTC."""
```

---

## State Persistence

Risk state written to `state.json` on every change. Read on startup:

```json
{
  "risk": {
    "consecutive_losses_today": 1,
    "losses_date_utc": "2026-03-10",
    "weekly_pnl": -30.00,
    "week_start_balance": 3000.00,
    "week_start_date_utc": "2026-03-09",
    "weekly_halted": false,
    "daily_halted": false
  }
}
```

On startup: if `losses_date_utc` != today UTC, reset `consecutive_losses_today` to 0. If `week_start_date_utc` is from a previous week, snapshot current balance as new week start.

---

## Test Cases for `tests/test_risk.py`

**Lot sizing:**
- £3,000 balance, 1% risk, $12 SL → correct lot value
- Computed lot below `volume_min` → returns `None`
- SL distance < $10 → returns `None`
- SL distance < 3× spread → returns `None`
- Lot correctly floored to volume step (never rounded up)

**Gates:**
- 3rd consecutive loss → gate closed
- Win after 2 losses → consecutive counter resets to 0
- Counter resets at new UTC day
- Weekly P&L at -4.9% → gate open
- Weekly P&L at -5.0% → gate closed
- Weekly gate remains closed after subsequent win
- 2 open positions → position cap gate closed
- 1 open position → gate open
- Serialise risk state → deserialise → identical values
