# AI Analyst Module — Design Spec

**Date:** 2026-03-18
**Status:** Approved

---

## Problem

The XAUEX bot produces rich structured data (state.json, signal history, closed trades, risk counters) but has no mechanism to synthesise that data into human-readable analysis. The operator currently has to read raw JSON to understand what happened and why.

## Goal

Add scheduled, fully automated AI analysis scripts that run without any operator input, using the `claude` CLI (`claude -p`) rather than the Anthropic API. Outputs are written as JSON to `/var/lib/xauex/` for dashboard consumption.

---

## Scope

Four scripts, each independent:

| Script | Schedule | Model | Purpose |
|--------|----------|-------|---------|
| `morning_brief.py` | 07:45 Mon–Fri | `claude-sonnet-4-6` | Daily situational summary |
| `post_trade_journal.py` | Every 5 min Mon–Fri | `claude-sonnet-4-6` | Journal entry per closed trade |
| `setup_scorer.py` | Every 5 min Mon–Fri | `claude-sonnet-4-6` | Quality score for each new signal |
| `weekly_review.py` | 08:00 Monday | `claude-opus-4-6` | Strategic weekly analysis |

---

## Architecture

### Module Layout

```
analyst/
├── morning_brief.py
├── post_trade_journal.py
├── setup_scorer.py
└── weekly_review.py

ops/
└── analyst.cron          # crontab entries

/var/lib/xauex/           # all outputs
├── morning_brief.json
├── trade_journal.json    # append-only list of journal entries
├── setup_scores.json     # append-only list of scored signals
├── weekly_review.json
├── trade_journal_cursor.json   # tracks last processed trade count
└── scorer_cursor.json          # tracks last processed signal timestamp
```

### Shared Conventions

- Each script is a standalone Python module, runnable directly: `python3 analyst/morning_brief.py`
- All scripts log to `/var/log/xauex/analyst.log`
- Claude is called via `subprocess.run(["claude", "-p", prompt, "--model", MODEL])` capturing stdout
- Output files are written atomically (temp file + `os.replace`) matching existing bot patterns
- Scripts are safe to run when the bot is stopped (they read files, not live state)

---

## Data Flow

### morning_brief.py

**Input:** `state.json`
**News data:** Reads the existing ForexFactory cache file directly at `/var/lib/xauex/news_calendar_cache.json` (written by `NewsFilter` while the bot runs). Does NOT instantiate `NewsFilter` or `load_config()` — avoids the `aiohttp` async context requirement and the need for cTrader credentials in the cron environment. If the cache file is absent or older than 24 hours, the news section is omitted from the brief with a note.

**Prompt covers:**
- Current price and proximity to HTF levels
- Open positions (entry, SL, TP, unrealised PnL, pattern, level)
- Risk counters (consecutive losses today, weekly PnL, weekly halted)
- Last 5 signals (accepted and rejected)
- Today's high-impact news events with times

**Output:** `/var/lib/xauex/morning_brief.json`
```json
{
  "generated_at_utc": "2026-03-18T07:45:01Z",
  "model": "claude-sonnet-4-6",
  "brief": "..."
}
```

---

### post_trade_journal.py

**Trigger mechanism:** Polls `state.json` every 5 minutes. Reads `closed_trades_today` list; compares each trade's `position_id` against `trade_journal_cursor.json` which stores `{"journalled_ids": ["id1", "id2", ...]}` — the set of already-journalled trade IDs. Any trade whose `position_id` is not in this set is journalled. This is robust to bot restarts (which may reset `closed_trades_today` to an empty list mid-day) because IDs that were already journalled are never re-processed.

**Per new trade, prompt covers:**
- Entry price, direction, pattern type, HTF level traded
- SL distance (dollars), TP distance (dollars), lot size
- Actual outcome: profit/loss in dollars and pips
- RR achieved vs planned
- Session at time of entry
- Any news events within ±30 min of entry

**Output:** Appends to `/var/lib/xauex/trade_journal.json`
```json
[
  {
    "trade_id": "...",
    "journalled_at_utc": "...",
    "model": "claude-sonnet-4-6",
    "entry": { ...trade fields... },
    "journal": "..."
  }
]
```

---

### setup_scorer.py

**Trigger mechanism:** Polls `state.json` every 5 minutes. Reads `signal_history` (the full list, not `last_signal`) to handle multiple signals firing between cron runs. Compares each signal's `time_utc` field against `scorer_cursor.json` which stores `{"last_signal_ts": "2026-03-18T09:00:00Z"}` — the ISO timestamp of the most recently scored signal. All signals with a `time_utc` strictly after the cursor are scored. Signal dicts in `signal_history` contain a `time_utc` key (ISO UTC string) as confirmed in `main.py:_make_signal_record`. Note: `signal_history` has a maximum depth of 12 entries (deque `maxlen=12`); in the highly unlikely event that more than 12 signals fire between two cron runs, the oldest signals in that window may be missed.

**Prompt:** Applies a fixed rubric to the signal:
- HTF level quality (weekly vs monthly, how many times tested)
- Pattern quality (clean rejection vs marginal)
- Session alignment (London open vs mid-session)
- Trend alignment (D1/H1 EMA bias matches direction)
- News proximity (clean window vs marginal)
- Risk efficiency (SL distance vs ATR)

**Output:** Appends to `/var/lib/xauex/setup_scores.json`
```json
[
  {
    "signal_ts": "...",
    "scored_at_utc": "...",
    "model": "claude-sonnet-4-6",
    "signal": { ...signal fields... },
    "score": 7,
    "max_score": 10,
    "breakdown": "..."
  }
]
```

---

### weekly_review.py

**Input:** `state.json` (full `signal_history`, `shadow_signal_history`, `risk`) + entries from `trade_journal.json` and `setup_scores.json` filtered to the **previous completed week** (last Monday 00:00 UTC through last Sunday 23:59 UTC, determined from `journalled_at_utc` / `scored_at_utc` fields). Running at Monday 08:00 UTC, the current week is only 8 hours old and has no meaningful data; the review is always over the most recently completed Mon–Sun period. This prevents unbounded context growth as the append-only files accumulate over months.

**Prompt asks Opus to reason over:**
- Win rate and RR by pattern type, session, level type
- Signals taken vs signals skipped (and why skipped)
- Primary strategy vs shadow strategy signal divergence
- Risk efficiency: average SL distance vs average profit
- Anomalies: unusually good or bad setups, patterns in losses
- One or two concrete parameter or behaviour changes to consider

**Output:** `/var/lib/xauex/weekly_review.json`
```json
{
  "week_ending": "2026-03-22",
  "generated_at_utc": "...",
  "model": "claude-opus-4-6",
  "review": "..."
}
```

---

## Error Handling

- **state.json missing or stale:**
  - Polling scripts (`post_trade_journal`, `setup_scorer`): stale >10 min → log warning, exit 0
  - `morning_brief`: stale >30 min → include a prominent stale-data warning in the brief output rather than skipping; the operator should know the bot may be down
- **claude binary not found:** use `shutil.which("claude")` at startup; if None, log error and exit 1. The binary must be on `PATH` for the cron user (typically installed at `~/.local/bin/claude` or `/usr/local/bin/claude`)
- **Claude invocation:** `subprocess.run(["claude", "-p", prompt, "--model", MODEL], capture_output=True, text=True)`. Claude returns plain text. The script wraps the response in the output JSON envelope (with timestamp, model, etc.) before writing to file.
- **Claude non-zero exit:** log stderr, exit 1
- **Cursor file corrupted:** reset cursor, log warning, reprocess from scratch
- **news_calendar_cache.json absent or stale >24h** (morning brief): omit news section, note in brief
- No retries — cron cadence handles retry naturally

---

## Cron Schedule

File: `ops/analyst.cron`

```cron
# XAUEX AI Analyst — install with: crontab ops/analyst.cron
45 7  * * 1-5  cd /home/bolyki/xauex && python3 analyst/morning_brief.py >> /var/log/xauex/analyst.log 2>&1
*/5  * * * 1-5  cd /home/bolyki/xauex && python3 analyst/post_trade_journal.py >> /var/log/xauex/analyst.log 2>&1
*/5  * * * 1-5  cd /home/bolyki/xauex && python3 analyst/setup_scorer.py >> /var/log/xauex/analyst.log 2>&1
0  8  * * 1     cd /home/bolyki/xauex && python3 analyst/weekly_review.py >> /var/log/xauex/analyst.log 2>&1
```

---

## Out of Scope

- Dashboard display of analyst outputs (separate task)
- Analyst outputs feeding back into bot trading decisions
- Manual macro context injection
- External news sources beyond ForexFactory (already integrated)
