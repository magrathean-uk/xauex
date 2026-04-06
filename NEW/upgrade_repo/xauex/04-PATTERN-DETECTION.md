# 04 — XAUEX: Pattern Detection

## Overview

Pattern detection runs on the last two fully closed H1 candles after price confirms proximity to a mapped HTF level. All inputs are confirmed closed OHLC fetched from the cTrader API — never from in-memory tick accumulation.

---

## Input Contract

- `prev`: candle at index -3 in H1 history (bar before the signal candle)
- `signal`: candle at index -2 in H1 history (most recently closed bar)
- `level`: the HTF level price is near (from LevelManager)

All OHLC values are floats in USD.

---

## Pattern 1: Pin Bar

### Thresholds (expose in config)

| Parameter | Default | Meaning |
|---|---|---|
| `PIN_MAX_BODY_RATIO` | 0.30 | Body ≤ 30% of total range |
| `PIN_MIN_WICK_RATIO` | 0.60 | Dominant wick ≥ 60% of total range |

### Logic

```
range = high - low
if range == 0: return NONE

body        = abs(close - open)
upper_wick  = high - max(open, close)
lower_wick  = min(open, close) - low

if body / range > PIN_MAX_BODY_RATIO: return NONE

if lower_wick / range >= PIN_MIN_WICK_RATIO:
    return BULLISH_PIN_BAR   # long lower wick = bullish rejection

if upper_wick / range >= PIN_MIN_WICK_RATIO:
    return BEARISH_PIN_BAR   # long upper wick = bearish rejection

return NONE
```

### Direction
- `BULLISH_PIN_BAR` → LONG
- `BEARISH_PIN_BAR` → SHORT

---

## Pattern 2: Engulfing Candle

### Logic

```
prev_body_low  = min(prev.open, prev.close)
prev_body_high = max(prev.open, prev.close)
sig_body_low   = min(signal.open, signal.close)
sig_body_high  = max(signal.open, signal.close)

if prev is bearish AND signal is bullish:
    if sig_body_low <= prev_body_low AND sig_body_high >= prev_body_high:
        return BULLISH_ENGULFING

if prev is bullish AND signal is bearish:
    if sig_body_high >= prev_body_high AND sig_body_low <= prev_body_low:
        return BEARISH_ENGULFING

return NONE
```

Body-only comparison. Wicks are irrelevant.

### Direction
- `BULLISH_ENGULFING` → LONG
- `BEARISH_ENGULFING` → SHORT

---

## Pattern 3: Inside Bar

### Logic

```
if signal.high < prev.high AND signal.low > prev.low:
    return INSIDE_BAR
```

Strict less-than: if signal.high == prev.high it is not an inside bar.

### Entry Mechanism

Inside bar does not generate an immediate market order. It sets a pending breakout state. The executor places two stop orders:
- BUY_STOP at `prev.high + spread_buffer`
- SELL_STOP at `prev.low - spread_buffer`

`spread_buffer` = current live spread at time of order placement. Fallback: $1.00.

When one fills, the other is cancelled immediately. If neither fills within 2 H1 candles, both are cancelled.

The `mother_bar_high` and `mother_bar_low` (from `prev`) are returned in `PatternResult` for the executor.

---

## Pattern Priority

If multiple patterns qualify on the same candle, apply in this order:
1. Engulfing (strongest)
2. Pin Bar
3. Inside Bar

Return only the highest-priority match.

---

## Level Alignment Check

Before returning any pattern, verify the candle actually interacted with the level:

- **Pin Bar:** tip of dominant wick within `LEVEL_PROXIMITY_DOLLARS` of the level
- **Engulfing:** signal candle body spans across or touches the level
- **Inside Bar:** `prev` candle high or low within `LEVEL_PROXIMITY_DOLLARS` of the level

If alignment fails → return `NONE` regardless of candle shape. A valid-shaped candle in open space is not a signal.

---

## Data Classes

```python
from enum import Enum
from dataclasses import dataclass
from datetime import datetime

class PatternType(Enum):
    NONE             = 0
    BULLISH_PIN_BAR  = 1
    BEARISH_PIN_BAR  = 2
    BULLISH_ENGULFING = 3
    BEARISH_ENGULFING = 4
    INSIDE_BAR       = 5

@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    open_time: datetime

@dataclass
class PatternResult:
    pattern: PatternType
    signal_candle: Candle
    prev_candle: Candle
    level: float
    direction: int        # +1 long, -1 short, 0 inside bar pending
    mother_bar_high: float
    mother_bar_low: float
```

---

## PatternDetector Interface

```python
class PatternDetector:
    def __init__(self, config: Config): ...

    def detect(self, prev: Candle, signal: Candle, level: float) -> PatternResult:
        """
        Classify signal candle relative to prev and the HTF level.
        Returns PatternResult with pattern=NONE if no valid pattern found.
        Never raises — returns NONE on any degenerate input (zero range, doji).
        """
```

---

## Test Cases for `tests/test_patterns.py`

- Body 25% of range, lower wick 65% → `BULLISH_PIN_BAR`
- Body 25% of range, upper wick 65% → `BEARISH_PIN_BAR`
- Body 40% of range → `NONE` (fails body ratio)
- Prev bearish, signal bullish body fully contains prev body → `BULLISH_ENGULFING`
- Prev bullish, signal bearish body fully contains prev body → `BEARISH_ENGULFING`
- Partial body overlap only → `NONE`
- Signal range strictly inside prev range → `INSIDE_BAR`
- Signal high equals prev high (not strictly inside) → `NONE`
- Valid pin bar shape but wick tip $10 from nearest level → `NONE` (alignment fail)
- Zero-range candle (doji with range=0) → `NONE`, no crash
- Engulfing takes priority over pin bar when both conditions met
