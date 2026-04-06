# 08 — XAUEX: Terminal Dashboard

## Overview

`dashboard.py` is a standalone Textual TUI that runs in a MATE terminal window and displays live bot state. It reads `/var/lib/xauex/state.json` every 2 seconds. It has no broker connection and no connection to the bot process — pure read-only state file consumer, plus one-way command file write for kill switch.

Run separately from the bot:
```bash
cd /opt/xauex && source .venv/bin/activate && python dashboard.py
```

Leave this terminal open on the MATE desktop. It updates in place.

---

## Layout

```
╔══════════════════════════════════════════════════════════════════╗
║  XAUEX  │  ● RUNNING  │  2026-03-10 09:15:32 UTC                ║
╠══════════════════════╦═══════════════════════════════════════════╣
║  ACCOUNT             ║  HTF LEVELS                               ║
║  Balance:  £3,062.40 ║  MN  O:2680  H:2750  L:2610  C:2740      ║
║  Equity:   £3,048.90 ║  WK  O:2720  H:2745  L:2700  C:2735      ║
║  Open P&L: -£13.50   ║  Refreshed: 2026-03-09 21:00 UTC          ║
╠══════════════════════╩═══════════════════════════════════════════╣
║  OPEN POSITIONS                                                   ║
║  #789012  LONG  Entry:2720.45  SL:2708.00  TP:2745.00            ║
║           Lot:0.03  Pattern:BULLISH_PIN_BAR  P&L: -£13.50        ║
╠═══════════════════════════════════════════════════════════════════╣
║  LAST SIGNAL  09:00 UTC  BULLISH_PIN_BAR @ 2720.00 (WK_O) → EXECUTED ║
╠═══════════════════════════════════════════════════════════════════╣
║  PRICE  (last 20 H1 closes, * = long entry, v = short entry)     ║
║                                                                   ║
║  2745 ┤                                              ─            ║
║  2738 ┤                              ─             ╱              ║
║  2731 ┤          ─        ─         ╱ ╲           ╱              ║
║  2724 ┤         ╱ ╲      ╱ ╲       ╱   ╲         ╱               ║
║  2717 ┤        ╱   ╲    ╱   ─     ╱     ─       ╱                ║
║  2720 ┤   ─   ╱     ╲  ╱    ╲  *╱        ╲    ╱                  ║
║  2710 ┤  ╱ ╲ ╱       ╲╱      ╲╱            ╲  ╱                  ║
║       └──────────────────────────────────────────────────────     ║
╠═══════════════════════════════════════════════════════════════════╣
║  TODAY'S TRADES                                                   ║
║  08:15  LONG  2720.45 → 2745.00  +£62.40  WIN   BULLISH_PIN_BAR  ║
║  09:00  LONG  2720.45  OPEN               -£13.50                 ║
╠═══════════════════════════════════════════════════════════════════╣
║  RISK  Losses today: 0/3   Weekly P&L: +£32.40   Observe: YES    ║
║  [K] Kill switch   [Q] Quit                                       ║
╚═══════════════════════════════════════════════════════════════════╝
```

---

## Status Bar Colours

| `bot_status` | Colour |
|---|---|
| `RUNNING` | Green |
| `RECONNECTING` | Yellow |
| `OBSERVE_ONLY` | Cyan |
| `HALTED_WEEKLY_DRAWDOWN` | Red |
| `HALTED_DAILY_LOSSES` | Red |
| `HALTED_AUTH_FAILURE` | Red |
| `HALTED_KILL_SWITCH` | Red |
| `HALTED_NEWS_FEED` | Red |
| `SHUTDOWN` | Grey |
| File missing / parse error | `WAITING FOR BOT...` in yellow |

---

## Refresh Cycle

Poll `state.json` every 2 seconds using Textual's `set_interval`. On each poll:
- Attempt to read and parse `state.json`
- On success: update all widgets
- On failure (missing file, JSON parse error): retain previous display, show `WAITING FOR BOT...` in status bar

**Atomic read:** the bot writes via temp file + `os.replace()`. Wrap every read in try/except to handle the brief moment the file may be absent.

---

## ASCII Price Chart

The bot writes `recent_h1_closes` (list of last 20 H1 close prices as floats) and `trade_entries_on_chart` (list of `{bar_index, direction, price}`) to the state file.

Chart rendering:
- Fixed width: 60 chars
- Fixed height: 8 rows
- Y-axis: auto-scaled to min/max of the 20 values, 5 price labels
- Long entry marker: `*` (green)
- Short entry marker: `v` (red)
- HTF levels that fall within the chart Y-range drawn as dotted horizontal lines `·`

---

## Kill Switch

Key binding: `K` → confirmation prompt: `Halt trading? [Y/N]`

On `Y`: write `{"kill_switch": true}` to `/var/lib/xauex/cmd.json`. The bot polls this file every 10 seconds.

On `N`: dismiss prompt.

To resume after kill switch: press `K` again, confirm → writes `{"kill_switch": false}`.

---

## state.json Full Schema

The bot (`bot/state/writer.py`) must write this exact schema. The dashboard consumes it.

```json
{
  "meta": {
    "version": 1,
    "last_updated_utc": "2026-03-10T09:15:32Z",
    "bot_status": "RUNNING"
  },
  "account": {
    "balance": 3062.40,
    "equity": 3048.90,
    "currency": "GBP",
    "open_pnl": -13.50
  },
  "risk": {
    "consecutive_losses_today": 0,
    "losses_date_utc": "2026-03-10",
    "weekly_pnl": 32.40,
    "week_start_balance": 3000.00,
    "week_start_date_utc": "2026-03-09",
    "weekly_halted": false,
    "daily_halted": false
  },
  "levels": {
    "last_refresh_utc": "2026-03-09T21:00:00Z",
    "monthly": {
      "open": 2680.00, "high": 2750.50, "low": 2610.20, "close": 2740.10
    },
    "weekly": {
      "open": 2720.00, "high": 2745.00, "low": 2700.00, "close": 2735.00
    }
  },
  "open_positions": [
    {
      "position_id": "789012",
      "direction": "LONG",
      "entry_price": 2720.45,
      "stop_loss": 2708.00,
      "take_profit": 2745.00,
      "lot_size": 0.03,
      "open_time_utc": "2026-03-10T09:00:00Z",
      "unrealised_pnl": -13.50,
      "pattern": "BULLISH_PIN_BAR",
      "level": 2720.00
    }
  ],
  "closed_trades_today": [
    {
      "position_id": "789011",
      "direction": "LONG",
      "entry_price": 2720.45,
      "close_price": 2745.00,
      "open_time_utc": "2026-03-10T08:15:00Z",
      "close_time_utc": "2026-03-10T09:00:00Z",
      "pnl": 62.40,
      "pattern": "BULLISH_PIN_BAR",
      "level": 2720.00
    }
  ],
  "recent_h1_closes": [
    2710.0, 2712.5, 2715.0, 2718.0, 2720.0,
    2722.0, 2719.0, 2721.0, 2723.0, 2725.0,
    2728.0, 2726.0, 2724.0, 2720.45, 2718.0,
    2721.0, 2725.0, 2730.0, 2735.0, 2731.45
  ],
  "trade_entries_on_chart": [
    {"bar_index": 13, "direction": "LONG", "price": 2720.45}
  ],
  "last_signal": {
    "time_utc": "2026-03-10T09:00:00Z",
    "pattern": "BULLISH_PIN_BAR",
    "level_checked": 2720.00,
    "gate_result": "OK",
    "action": "EXECUTED"
  },
  "last_error": null,
  "observe_only": true
}
```

---

## Dependencies

```
textual>=0.50.0
```

Already in `requirements.txt`. No other additional dependencies.
