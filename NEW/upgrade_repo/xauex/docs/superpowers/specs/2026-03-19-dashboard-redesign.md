# Dashboard Redesign — Design Spec

**Date:** 2026-03-19
**Status:** Approved

---

## Problem

The XAUEX dashboard is a single-screen Textual TUI that shows live bot state, but has no visibility into the AI analyst outputs (morning brief, trade journal, setup scores, weekly review). Viewing analyst results requires reading raw JSON files manually.

## Goal

Add tab-based navigation to `dashboard.py` so all four analyst outputs are accessible without leaving the terminal. Keep the trading view intact; add an Analyst tab and a Journal tab.

---

## Scope

Modify `dashboard.py` only. No new files. No changes to analyst scripts, state writer, or cron jobs.

---

## Architecture

### Tab Layout

```
┌─ XAUEX ─────────────────────────────────────────────────────────┐
│  [1] Trading  [2] Analyst  [3] Journal                          │
├─────────────────────────────────────────────────────────────────┤
│  (active tab content)                                           │
└─────────────────────────────────────────────────────────────────┘
```

Use Textual's `TabbedContent` + `TabPane` widgets. Each tab is a `TabPane` with its own widget tree.

### Keyboard Bindings

| Key | Action |
|-----|--------|
| `1` | Switch to Trading tab |
| `2` | Switch to Analyst tab |
| `3` | Switch to Journal tab |
| `k` | Kill switch (modal, any tab) |
| `q` | Quit |

---

## Tab 1: Trading

**Identical to the current single-screen layout.** No functional changes:
- Status bar: bot status + last updated timestamp
- Top row (4 panels): Account, Trend, HTF Levels, Runtime
- Positions panel
- Last Signal panel
- Price chart (60×8 ASCII)
- Recent Activity (signal history, shadow history, closed trades)
- Risk strip (losses today, daily PnL, weekly PnL, observe mode)

---

## Tab 2: Analyst

**Three stacked sections in a `ScrollableContainer`:**

### Morning Brief
- Source: `/var/lib/xauex/morning_brief.json`
- Fields: `generated_at_utc`, `model`, `stale_state`, `brief`
- Display: timestamp + model on header line, full `brief` text below
- If `stale_state` is true: show a `[STALE DATA]` warning before the brief text
- If file missing or unreadable: show `Morning Brief: no data yet`

### Latest Setup Score
- Source: `/var/lib/xauex/setup_scores.json` — last entry in the list
- Fields: `signal_ts`, `scored_at_utc`, `model`, `score`, `max_score`, `breakdown`, `signal`
- Display: signal timestamp, score (`7/10`), full `breakdown` text, then signal direction + pattern + level
- If file missing/empty: show `Setup Scores: no data yet`

### Weekly Review
- Source: `/var/lib/xauex/weekly_review.json`
- Fields: `week_ending`, `generated_at_utc`, `model`, `trades_reviewed`, `setups_reviewed`, `review`
- Display: week dates + counts on header, full `review` text below
- If file missing: show `Weekly Review: no data yet`

---

## Tab 3: Journal

**Scrollable list of trade journal entries:**

- Source: `/var/lib/xauex/trade_journal.json` — full list, newest first (reverse order)
- Per entry: trade ID, direction, entry price, outcome PnL, `journalled_at_utc`, then full `journal` text
- If file missing/empty: show `Trade Journal: no entries yet`
- Show up to 20 most recent entries (cap to avoid excessive rendering)

---

## Data Loading

The existing `_refresh_state` 2s interval is extended to also load analyst JSON files:

```python
def _refresh_state(self) -> None:
    # existing: load state.json → _update_trading_tab(state)
    # new: load analyst JSONs → _update_analyst_tab(brief, scores, review)
    #                          → _update_journal_tab(journal)
```

Each analyst file read is wrapped in try/except — a missing or corrupt file yields `None`, which the rendering method treats as "no data yet". No retry, no error propagation.

---

## CSS

Add `TabbedContent`-specific sizing. All existing panel CSS rules are unchanged and scoped inside the Trading tab pane.

Tab bar height: 3 lines (Textual default for `TabbedContent`).

---

## Analyst File Paths

Configurable via environment variables with defaults:

| Variable | Default |
|----------|---------|
| `MORNING_BRIEF_PATH` | `/var/lib/xauex/morning_brief.json` |
| `TRADE_JOURNAL_PATH` | `/var/lib/xauex/trade_journal.json` |
| `SETUP_SCORES_PATH` | `/var/lib/xauex/setup_scores.json` |
| `WEEKLY_REVIEW_PATH` | `/var/lib/xauex/weekly_review.json` |

---

## Error Handling

- Any analyst JSON file missing or unreadable → "no data yet" message in that section
- `state.json` missing → status bar shows "WAITING FOR BOT...", trading tab shows empty panels (unchanged from current behaviour)
- `TabbedContent` tab switch is instant (no data reload triggered; next 2s interval picks it up)

---

## Out of Scope

- Real-time push updates when analyst files change (2s poll is sufficient)
- Analyst output editing or interaction
- Filtering or searching journal entries
- Score trend charts
