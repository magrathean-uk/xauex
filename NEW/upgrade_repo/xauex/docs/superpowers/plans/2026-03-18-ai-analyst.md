# AI Analyst Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four standalone Python scripts in `analyst/` that call `claude -p` on a cron schedule to produce automated morning briefs, per-trade journals, setup scores, and weekly strategic reviews — all written as JSON to `/var/lib/xauex/`.

**Architecture:** Each script is independent and runnable as `python3 analyst/<script>.py`. A shared `analyst/_utils.py` handles file I/O, cursor persistence, stale-state detection, and Claude invocation. Scripts read from `/var/lib/xauex/state.json` (and supporting files), call `claude -p` via `subprocess`, and write output atomically back to `/var/lib/xauex/`. Cron entries in `ops/analyst.cron` schedule execution.

**Tech Stack:** Python 3.11+ stdlib only (json, os, subprocess, shutil, datetime, logging, tempfile). No new dependencies. `claude` CLI must be on PATH for the cron user.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `analyst/__init__.py` | Create | Package marker |
| `analyst/_utils.py` | Create | Shared I/O: state reading, staleness check, cursor R/W, atomic JSON append, Claude subprocess call |
| `analyst/morning_brief.py` | Create | 07:45 brief: state + news cache → Claude Sonnet → morning_brief.json |
| `analyst/post_trade_journal.py` | Create | 5-min poll: new closed trades → Claude Sonnet → trade_journal.json |
| `analyst/setup_scorer.py` | Create | 5-min poll: new signals from signal_history → Claude Sonnet → setup_scores.json |
| `analyst/weekly_review.py` | Create | Monday 08:00: prior week trades + scores → Claude Opus → weekly_review.json |
| `ops/analyst.cron` | Create | Crontab entries for all four scripts |
| `tests/test_analyst_utils.py` | Create | Unit tests for _utils |
| `tests/test_analyst_morning_brief.py` | Create | Unit tests for morning_brief |
| `tests/test_analyst_journal.py` | Create | Unit tests for post_trade_journal |
| `tests/test_analyst_scorer.py` | Create | Unit tests for setup_scorer |
| `tests/test_analyst_weekly.py` | Create | Unit tests for weekly_review |

---

## Reference: Key Data Shapes

**state.json top-level keys** (from `bot/state/writer.py`):
```
meta.bot_status, meta.last_updated_utc
account.{balance, equity, ...}
risk.{consecutive_losses_today, weekly_pnl, weekly_halted, daily_halted, ...}
levels.{weekly, monthly, daily}.{open, high, low, close}
open_positions[].{position_id, direction, entry_price, stop_loss, take_profit, lot_size, unrealised_pnl, pattern, level, open_time_utc}
closed_trades_today[].{position_id, direction, entry_price, close_price, stop_loss, take_profit, lot_size, pnl, pattern, level, close_time_utc}
signal_history[].{time_utc, pattern, level_checked, gate_result, action, ...}
shadow_signal_history[].{...same...}
trend.{d1_ema, h1_ema, bias}
```

**news_calendar_cache.json** (from `bot/filters/news.py`):
```json
{
  "last_refresh_date": "2026-03-18",
  "events": [{"title": "...", "currency": "USD", "impact": "HIGH", "time_utc": "..."}]
}
```

**trade cursor** (`trade_journal_cursor.json`):
```json
{"journalled_ids": ["pos123", "pos456"]}
```

**scorer cursor** (`scorer_cursor.json`):
```json
{"last_signal_ts": "2026-03-18T09:00:00Z"}
```

---

## Task 1: Shared Utilities (`analyst/_utils.py`)

**Files:**
- Create: `analyst/__init__.py`
- Create: `analyst/_utils.py`
- Create: `tests/test_analyst_utils.py`

### Steps

- [ ] **Step 1: Write failing tests for `_utils.py`**

Create `tests/test_analyst_utils.py`:

```python
"""Tests for analyst._utils shared utilities."""
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from analyst._utils import (
    read_json_file,
    is_state_stale,
    atomic_write_json,
    append_to_json_list,
    load_cursor,
    save_cursor,
    find_claude,
)


def test_read_json_file_missing_returns_none(tmp_path):
    result = read_json_file(str(tmp_path / "missing.json"))
    assert result is None


def test_read_json_file_returns_parsed(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"key": "value"}')
    assert read_json_file(str(p)) == {"key": "value"}


def test_read_json_file_corrupted_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json{{{")
    assert read_json_file(str(p)) is None


def test_is_state_stale_missing():
    assert is_state_stale(None, max_age_seconds=60) is True


def test_is_state_stale_fresh():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {"meta": {"last_updated_utc": now}}
    assert is_state_stale(state, max_age_seconds=60) is False


def test_is_state_stale_old():
    state = {"meta": {"last_updated_utc": "2020-01-01T00:00:00Z"}}
    assert is_state_stale(state, max_age_seconds=60) is True


def test_atomic_write_json(tmp_path):
    p = str(tmp_path / "out.json")
    atomic_write_json(p, {"hello": "world"})
    assert json.loads(open(p).read()) == {"hello": "world"}


def test_atomic_write_json_is_atomic(tmp_path):
    """Write should replace file atomically (no partial read window)."""
    p = str(tmp_path / "out.json")
    atomic_write_json(p, {"v": 1})
    atomic_write_json(p, {"v": 2})
    assert json.loads(open(p).read()) == {"v": 2}


def test_append_to_json_list_new_file(tmp_path):
    p = str(tmp_path / "list.json")
    append_to_json_list(p, {"a": 1})
    assert json.loads(open(p).read()) == [{"a": 1}]


def test_append_to_json_list_existing(tmp_path):
    p = str(tmp_path / "list.json")
    append_to_json_list(p, {"a": 1})
    append_to_json_list(p, {"b": 2})
    assert json.loads(open(p).read()) == [{"a": 1}, {"b": 2}]


def test_load_cursor_missing_returns_default(tmp_path):
    p = str(tmp_path / "cursor.json")
    result = load_cursor(p, default={"ids": []})
    assert result == {"ids": []}


def test_load_cursor_existing(tmp_path):
    p = str(tmp_path / "cursor.json")
    atomic_write_json(p, {"ids": ["a", "b"]})
    assert load_cursor(p, default={"ids": []}) == {"ids": ["a", "b"]}


def test_load_cursor_corrupted_returns_default(tmp_path):
    p = str(tmp_path / "cursor.json")
    open(p, "w").write("not json")
    result = load_cursor(p, default={"ids": []})
    assert result == {"ids": []}


def test_save_cursor(tmp_path):
    p = str(tmp_path / "cursor.json")
    save_cursor(p, {"last_ts": "2026-01-01T00:00:00Z"})
    assert load_cursor(p, default={}) == {"last_ts": "2026-01-01T00:00:00Z"}


def test_find_claude_found():
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        from analyst._utils import find_claude
        assert find_claude() == "/usr/local/bin/claude"


def test_find_claude_missing():
    with patch("shutil.which", return_value=None):
        from analyst._utils import find_claude
        assert find_claude() is None
```

- [ ] **Step 2: Run tests — expect all failures**

```bash
python3 -m pytest tests/test_analyst_utils.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'analyst'`

- [ ] **Step 3: Create package marker**

Create `analyst/__init__.py` (empty):
```python
```

- [ ] **Step 4: Implement `analyst/_utils.py`**

```python
"""Shared utilities for analyst scripts."""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

STATE_FILE = os.environ.get("XAUEX_STATE_FILE", "/var/lib/xauex/state.json")
LOG_FILE = os.environ.get("XAUEX_ANALYST_LOG", "/var/log/xauex/analyst.log")


def read_json_file(path: str) -> Optional[dict]:
    """Read and parse a JSON file. Returns None on missing or parse error."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        logger.warning("JSON parse error in %s: %s", path, e)
        return None


def is_state_stale(state: Optional[dict], max_age_seconds: int) -> bool:
    """Return True if state is None or last_updated_utc is older than max_age_seconds."""
    if state is None:
        return True
    try:
        ts_str = state["meta"]["last_updated_utc"]
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age > max_age_seconds
    except (KeyError, ValueError):
        return True


def atomic_write_json(path: str, data: Any) -> None:
    """Write data as JSON atomically using temp-file + os.replace."""
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_to_json_list(path: str, entry: Any) -> None:
    """Append entry to a JSON array file atomically. Creates file if missing."""
    existing = read_json_file(path)
    if not isinstance(existing, list):
        existing = []
    existing.append(entry)
    atomic_write_json(path, existing)


def load_cursor(path: str, default: Any) -> Any:
    """Load cursor file. Returns default on missing or parse error."""
    data = read_json_file(path)
    if data is None:
        logger.debug("Cursor not found at %s, using default.", path)
        return default
    return data


def save_cursor(path: str, data: Any) -> None:
    """Save cursor data atomically."""
    atomic_write_json(path, data)


def find_claude() -> Optional[str]:
    """Return path to claude binary, or None if not on PATH."""
    return shutil.which("claude")


def call_claude(prompt: str, model: str) -> str:
    """
    Call `claude -p <prompt> --model <model>` and return stdout text.
    Raises RuntimeError on non-zero exit or binary not found.
    """
    binary = find_claude()
    if binary is None:
        raise RuntimeError(
            "claude binary not found on PATH. "
            "Ensure claude CLI is installed and accessible to the cron user."
        )
    result = subprocess.run(
        [binary, "-p", prompt, "--model", model],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude exited with code {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def utcnow_str() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 5: Run tests — expect pass**

```bash
python3 -m pytest tests/test_analyst_utils.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add analyst/__init__.py analyst/_utils.py tests/test_analyst_utils.py
git commit -m "feat: add analyst/_utils shared I/O and Claude subprocess utilities"
```

---

## Task 2: Morning Brief (`analyst/morning_brief.py`)

**Files:**
- Create: `analyst/morning_brief.py`
- Create: `tests/test_analyst_morning_brief.py`

**Output:** `/var/lib/xauex/morning_brief.json`

### Steps

- [ ] **Step 1: Write failing tests**

Create `tests/test_analyst_morning_brief.py`:

```python
"""Tests for analyst.morning_brief."""
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from analyst.morning_brief import (
    build_news_section,
    build_prompt,
    run,
)

FRESH_STATE = {
    "meta": {
        "last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bot_status": "RUNNING",
    },
    "account": {"balance": 10000.0, "equity": 10050.0},
    "risk": {
        "consecutive_losses_today": 1,
        "weekly_pnl": -150.0,
        "weekly_halted": False,
        "daily_halted": False,
    },
    "levels": {
        "weekly": {"open": 3300.0, "high": 3310.0, "low": 3290.0, "close": 3305.0},
        "monthly": {"open": 3200.0, "high": 3350.0, "low": 3180.0, "close": 3305.0},
        "daily": {"open": 3302.0, "high": 3308.0, "low": 3298.0, "close": 3305.0},
    },
    "open_positions": [
        {
            "position_id": "p1",
            "direction": "LONG",
            "entry_price": 3301.0,
            "stop_loss": 3291.0,
            "take_profit": 3315.0,
            "lot_size": 0.01,
            "unrealised_pnl": 40.0,
            "pattern": "PINBAR",
            "level": 3300.0,
            "open_time_utc": "2026-03-18T09:00:00Z",
        }
    ],
    "signal_history": [
        {"time_utc": "2026-03-18T09:00:00Z", "pattern": "PINBAR", "action": "TRADE_PLACED", "gate_result": "OK", "level_checked": 3300.0},
        {"time_utc": "2026-03-18T08:00:00Z", "pattern": "ENGULFING", "action": "SKIPPED", "gate_result": "NEWS_BLOCK", "level_checked": 3310.0},
    ],
    "observe_only": False,
}

FRESH_NEWS_CACHE = {
    "last_refresh_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    "events": [
        {"title": "USD CPI", "currency": "USD", "impact": "HIGH", "time_utc": "2026-03-18T13:30:00+00:00"},
        {"title": "EUR GDP", "currency": "EUR", "impact": "HIGH", "time_utc": "2026-03-18T10:00:00+00:00"},
    ],
}


def test_build_news_section_filters_usd_high(tmp_path):
    cache_path = str(tmp_path / "news_calendar_cache.json")
    import json; open(cache_path, "w").write(json.dumps(FRESH_NEWS_CACHE))
    section = build_news_section(cache_path)
    assert "USD CPI" in section
    assert "EUR GDP" not in section   # EUR filtered out


def test_build_news_section_missing_cache(tmp_path):
    section = build_news_section(str(tmp_path / "missing.json"))
    assert "unavailable" in section.lower() or "no news" in section.lower()


def test_build_news_section_stale_cache(tmp_path):
    cache_path = str(tmp_path / "news_calendar_cache.json")
    stale = {"last_refresh_date": "2020-01-01", "events": []}
    open(cache_path, "w").write(json.dumps(stale))
    section = build_news_section(cache_path)
    assert "stale" in section.lower() or "unavailable" in section.lower()


def test_build_prompt_contains_key_fields():
    prompt = build_prompt(FRESH_STATE, news_section="USD CPI at 13:30")
    assert "3301" in prompt          # entry price
    assert "PINBAR" in prompt
    assert "USD CPI" in prompt
    assert "consecutive_losses" in prompt.lower() or "consecutive" in prompt.lower()


def test_build_prompt_stale_warning():
    prompt = build_prompt(FRESH_STATE, news_section="n/a", stale=True)
    assert "stale" in prompt.lower() or "warning" in prompt.lower()


def test_run_writes_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news_calendar_cache.json")
    output_path = str(tmp_path / "morning_brief.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(news_path, "w").write(json.dumps(FRESH_NEWS_CACHE))

    with patch("analyst.morning_brief.call_claude", return_value="Markets look calm."):
        run(state_path=state_path, news_cache_path=news_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert result["brief"] == "Markets look calm."
    assert result["model"] == "claude-sonnet-4-6"
    assert "generated_at_utc" in result


def test_run_stale_state_still_writes_with_warning(tmp_path):
    state = dict(FRESH_STATE)
    state["meta"] = dict(state["meta"])
    state["meta"]["last_updated_utc"] = "2020-01-01T00:00:00Z"
    state_path = str(tmp_path / "state.json")
    news_path = str(tmp_path / "news_calendar_cache.json")
    output_path = str(tmp_path / "morning_brief.json")
    open(state_path, "w").write(json.dumps(state))
    open(news_path, "w").write(json.dumps(FRESH_NEWS_CACHE))

    with patch("analyst.morning_brief.call_claude", return_value="Stale data detected."):
        run(state_path=state_path, news_cache_path=news_path, output_path=output_path)

    result = json.loads(open(output_path).read())
    assert "stale" in result.get("brief", "").lower() or "stale" in result.get("warning", "").lower()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_analyst_morning_brief.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'build_news_section' from 'analyst.morning_brief'`

- [ ] **Step 3: Implement `analyst/morning_brief.py`**

```python
"""Morning brief — runs at 07:45 Mon-Fri via cron."""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from analyst._utils import (
    atomic_write_json,
    call_claude,
    is_state_stale,
    read_json_file,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
STATE_PATH = os.environ.get("XAUEX_STATE_FILE", "/var/lib/xauex/state.json")
NEWS_CACHE_PATH = os.environ.get(
    "XAUEX_NEWS_CACHE", "/var/lib/xauex/news_calendar_cache.json"
)
OUTPUT_PATH = os.environ.get(
    "XAUEX_BRIEF_OUTPUT", "/var/lib/xauex/morning_brief.json"
)
STALE_SECONDS = 30 * 60  # 30 minutes
NEWS_STALE_HOURS = 24


def build_news_section(news_cache_path: str) -> str:
    """Read news_calendar_cache.json and return a text section of today's high-impact USD events."""
    cache = read_json_file(news_cache_path)
    if cache is None:
        return "News calendar: unavailable (cache file missing)."

    refresh_date = cache.get("last_refresh_date", "")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        from datetime import timedelta
        refresh_dt = datetime.strptime(refresh_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - refresh_dt).total_seconds() / 3600
        if age_hours > NEWS_STALE_HOURS:
            return f"News calendar: stale (last refreshed {refresh_date})."
    except ValueError:
        return "News calendar: stale (refresh date unparseable)."

    events = cache.get("events", [])
    usd_high = [e for e in events if e.get("currency") == "USD" and e.get("impact", "").upper() == "HIGH"]
    if not usd_high:
        return "No high-impact USD events today."

    lines = ["High-impact USD events today:"]
    for e in sorted(usd_high, key=lambda x: x.get("time_utc", "")):
        lines.append(f"  - {e['title']} at {e['time_utc']}")
    return "\n".join(lines)


def build_prompt(state: dict, news_section: str, stale: bool = False) -> str:
    """Build the Claude prompt for the morning brief."""
    meta = state.get("meta", {})
    account = state.get("account", {})
    risk = state.get("risk", {})
    levels = state.get("levels", {})
    positions = state.get("open_positions", [])
    signals = state.get("signal_history", [])[:5]

    stale_warning = (
        "\n⚠️ WARNING: Bot state data is STALE (>30 minutes old). Bot may be stopped.\n"
        if stale else ""
    )

    positions_text = "None" if not positions else "\n".join(
        f"  - {p['direction']} {p['lot_size']} lots @ {p['entry_price']} "
        f"SL={p['stop_loss']} TP={p['take_profit']} PnL={p.get('unrealised_pnl', 0):.2f} "
        f"Pattern={p['pattern']} Level={p['level']}"
        for p in positions
    )

    signals_text = "None" if not signals else "\n".join(
        f"  - [{s['time_utc']}] {s.get('pattern','?')} @ {s.get('level_checked','?')} "
        f"→ {s.get('action','?')} ({s.get('gate_result','?')})"
        for s in signals
    )

    wk = levels.get("weekly", {})
    mn = levels.get("monthly", {})

    return f"""You are a trading operations analyst for an automated XAUUSD bot. Produce a concise morning brief covering the key points an operator needs before the London session opens.{stale_warning}

BOT STATUS: {meta.get('bot_status', 'UNKNOWN')} (observe_only={state.get('observe_only', True)})
ACCOUNT: balance={account.get('balance', '?')} equity={account.get('equity', '?')}
RISK: consecutive_losses_today={risk.get('consecutive_losses_today', 0)} weekly_pnl={risk.get('weekly_pnl', 0):.2f} weekly_halted={risk.get('weekly_halted', False)} daily_halted={risk.get('daily_halted', False)}

HTF LEVELS:
  Weekly: O={wk.get('open')} H={wk.get('high')} L={wk.get('low')} C={wk.get('close')}
  Monthly: O={mn.get('open')} H={mn.get('high')} L={mn.get('low')} C={mn.get('close')}

OPEN POSITIONS:
{positions_text}

LAST 5 SIGNALS:
{signals_text}

{news_section}

Produce a short morning brief (5-10 sentences) covering: current positioning, key levels to watch, risk status, and any upcoming news events that could affect XAUUSD today. Be direct and operational."""


def run(
    state_path: str = STATE_PATH,
    news_cache_path: str = NEWS_CACHE_PATH,
    output_path: str = OUTPUT_PATH,
) -> None:
    state = read_json_file(state_path)
    stale = is_state_stale(state, max_age_seconds=STALE_SECONDS)

    if state is None:
        logger.warning("[BRIEF] state.json missing at %s — generating stale-warning brief.", state_path)
        state = {"meta": {"bot_status": "UNKNOWN", "last_updated_utc": "N/A"}, "account": {}, "risk": {}, "levels": {}, "open_positions": [], "signal_history": [], "observe_only": True}
        stale = True

    news_section = build_news_section(news_cache_path)
    prompt = build_prompt(state, news_section, stale=stale)

    logger.info("[BRIEF] Calling Claude (%s)...", MODEL)
    brief_text = call_claude(prompt, MODEL)

    result = {
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "stale_state": stale,
        "brief": brief_text,
    }
    atomic_write_json(output_path, result)
    logger.info("[BRIEF] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[BRIEF] Failed: %s", e)
        sys.exit(1)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_analyst_morning_brief.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add analyst/morning_brief.py tests/test_analyst_morning_brief.py
git commit -m "feat: add analyst/morning_brief — daily 07:45 situational summary via Claude Sonnet"
```

---

## Task 3: Post-Trade Journal (`analyst/post_trade_journal.py`)

**Files:**
- Create: `analyst/post_trade_journal.py`
- Create: `tests/test_analyst_journal.py`

**Output:** Appends to `/var/lib/xauex/trade_journal.json`. Cursor: `/var/lib/xauex/trade_journal_cursor.json`.

### Steps

- [ ] **Step 1: Write failing tests**

Create `tests/test_analyst_journal.py`:

```python
"""Tests for analyst.post_trade_journal."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from analyst.post_trade_journal import (
    find_new_trades,
    build_trade_prompt,
    run,
)

TRADE_A = {
    "position_id": "pos001",
    "direction": "LONG",
    "entry_price": 3301.0,
    "close_price": 3315.0,
    "stop_loss": 3291.0,
    "take_profit": 3315.0,
    "lot_size": 0.01,
    "pnl": 140.0,
    "pattern": "PINBAR",
    "level": 3300.0,
    "close_time_utc": "2026-03-18T11:00:00Z",
}

TRADE_B = {
    "position_id": "pos002",
    "direction": "SHORT",
    "entry_price": 3310.0,
    "close_price": 3300.0,
    "stop_loss": 3320.0,
    "take_profit": 3296.0,
    "lot_size": 0.01,
    "pnl": 100.0,
    "pattern": "ENGULFING",
    "level": 3310.0,
    "close_time_utc": "2026-03-18T14:00:00Z",
}

FRESH_STATE = {
    "meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    "closed_trades_today": [TRADE_A, TRADE_B],
}


def test_find_new_trades_all_new():
    cursor = {"journalled_ids": []}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert len(new) == 2


def test_find_new_trades_some_already_journalled():
    cursor = {"journalled_ids": ["pos001"]}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert len(new) == 1
    assert new[0]["position_id"] == "pos002"


def test_find_new_trades_all_journalled():
    cursor = {"journalled_ids": ["pos001", "pos002"]}
    new = find_new_trades([TRADE_A, TRADE_B], cursor)
    assert new == []


def test_build_trade_prompt_contains_key_fields():
    prompt = build_trade_prompt(TRADE_A)
    assert "3301" in prompt      # entry price
    assert "PINBAR" in prompt
    assert "LONG" in prompt
    assert "140" in prompt       # pnl


def test_run_journals_new_trades(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))

    with patch("analyst.post_trade_journal.call_claude", return_value="Clean pinbar entry."):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    journal = json.loads(open(journal_path).read())
    assert len(journal) == 2
    assert journal[0]["trade_id"] == "pos001"
    assert journal[0]["journal"] == "Clean pinbar entry."


def test_run_skips_already_journalled(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(cursor_path, "w").write(json.dumps({"journalled_ids": ["pos001", "pos002"]}))

    with patch("analyst.post_trade_journal.call_claude", return_value="Should not be called") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)
        mock_call.assert_not_called()


def test_run_stale_state_skips(tmp_path):
    state = {
        "meta": {"last_updated_utc": "2020-01-01T00:00:00Z"},
        "closed_trades_today": [TRADE_A],
    }
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(state))

    with patch("analyst.post_trade_journal.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)
        mock_call.assert_not_called()

    assert not os.path.exists(journal_path)


def test_run_survives_bot_restart_cursor(tmp_path):
    """Cursor uses IDs not counts — survives bot restart (closed_trades_today reset)."""
    # First run: journal pos001
    state1 = {"meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closed_trades_today": [TRADE_A]}
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    journal_path = str(tmp_path / "trade_journal.json")
    open(state_path, "w").write(json.dumps(state1))
    with patch("analyst.post_trade_journal.call_claude", return_value="entry 1"):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    # Bot restarts — closed_trades_today reset to empty, then pos002 closes
    state2 = {"meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}, "closed_trades_today": [TRADE_B]}
    open(state_path, "w").write(json.dumps(state2))
    with patch("analyst.post_trade_journal.call_claude", return_value="entry 2"):
        run(state_path=state_path, cursor_path=cursor_path, journal_path=journal_path)

    journal = json.loads(open(journal_path).read())
    assert len(journal) == 2
    ids = [j["trade_id"] for j in journal]
    assert "pos001" in ids and "pos002" in ids
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_analyst_journal.py -v 2>&1 | head -20
```

Expected: `ImportError`

- [ ] **Step 3: Implement `analyst/post_trade_journal.py`**

```python
"""Post-trade journal — polls every 5 min Mon-Fri via cron."""

import logging
import os
import sys
from typing import Any, Dict, List

from analyst._utils import (
    append_to_json_list,
    call_claude,
    is_state_stale,
    load_cursor,
    read_json_file,
    save_cursor,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
STALE_SECONDS = 10 * 60  # 10 minutes

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", "/var/lib/xauex/state.json")
CURSOR_PATH = os.environ.get("XAUEX_JOURNAL_CURSOR", "/var/lib/xauex/trade_journal_cursor.json")
JOURNAL_PATH = os.environ.get("XAUEX_JOURNAL_OUTPUT", "/var/lib/xauex/trade_journal.json")

_DEFAULT_CURSOR = {"journalled_ids": []}


def find_new_trades(trades: List[Dict], cursor: Dict) -> List[Dict]:
    """Return trades whose position_id is not in cursor['journalled_ids']."""
    seen = set(cursor.get("journalled_ids", []))
    return [t for t in trades if t.get("position_id") not in seen]


def build_trade_prompt(trade: Dict) -> str:
    """Build Claude prompt for a single closed trade."""
    direction = trade["direction"]
    entry = trade["entry_price"]
    close = trade["close_price"]
    sl = trade["stop_loss"]
    tp = trade["take_profit"]
    lots = trade["lot_size"]
    pnl = trade["pnl"]
    pattern = trade["pattern"]
    level = trade["level"]
    close_time = trade.get("close_time_utc", "unknown")

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    rr_planned = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    pnl_per_lot = round(pnl / lots, 2) if lots > 0 else 0
    outcome = "WIN" if pnl >= 0 else "LOSS"

    return f"""You are a trading journal assistant for an automated XAUUSD bot. Write a concise post-trade journal entry (3-5 sentences) for the following trade.

TRADE SUMMARY:
  Outcome: {outcome}
  Direction: {direction}
  Pattern: {pattern}
  HTF Level: {level}
  Entry: {entry}  Close: {close}  Close time: {close_time}
  Stop Loss: {sl} (distance: {sl_dist:.2f} USD)
  Take Profit: {tp} (distance: {tp_dist:.2f} USD)
  Planned RR: {rr_planned}
  Lot size: {lots}
  P&L: {pnl:.2f} USD ({pnl_per_lot:.2f} USD/lot)

Write a journal entry covering: what the setup looked like, whether execution followed the rules, and what can be learned from this trade."""


def run(
    state_path: str = STATE_PATH,
    cursor_path: str = CURSOR_PATH,
    journal_path: str = JOURNAL_PATH,
) -> None:
    state = read_json_file(state_path)
    if is_state_stale(state, max_age_seconds=STALE_SECONDS):
        logger.warning("[JOURNAL] state.json stale or missing — skipping.")
        return

    trades = state.get("closed_trades_today", [])
    if not trades:
        logger.info("[JOURNAL] No closed trades today.")
        return

    cursor = load_cursor(cursor_path, default=_DEFAULT_CURSOR)
    new_trades = find_new_trades(trades, cursor)
    if not new_trades:
        logger.info("[JOURNAL] No new trades to journal.")
        return

    journalled_ids = list(cursor.get("journalled_ids", []))

    for trade in new_trades:
        trade_id = trade["position_id"]
        logger.info("[JOURNAL] Journalling trade %s...", trade_id)
        prompt = build_trade_prompt(trade)
        entry_text = call_claude(prompt, MODEL)

        entry = {
            "trade_id": trade_id,
            "journalled_at_utc": utcnow_str(),
            "model": MODEL,
            "entry": trade,
            "journal": entry_text,
        }
        append_to_json_list(journal_path, entry)
        journalled_ids.append(trade_id)
        save_cursor(cursor_path, {"journalled_ids": journalled_ids})
        logger.info("[JOURNAL] Journalled trade %s.", trade_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[JOURNAL] Failed: %s", e)
        sys.exit(1)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_analyst_journal.py -v
```

Expected: all green.

- [ ] **Step 5: Fix missing import in test file**

The test uses `os.path.exists` — add `import os` at the top of `tests/test_analyst_journal.py` if it's missing.

- [ ] **Step 6: Commit**

```bash
git add analyst/post_trade_journal.py tests/test_analyst_journal.py
git commit -m "feat: add analyst/post_trade_journal — per-trade journal entries via Claude Sonnet"
```

---

## Task 4: Setup Scorer (`analyst/setup_scorer.py`)

**Files:**
- Create: `analyst/setup_scorer.py`
- Create: `tests/test_analyst_scorer.py`

**Output:** Appends to `/var/lib/xauex/setup_scores.json`. Cursor: `/var/lib/xauex/scorer_cursor.json`.

### Steps

- [ ] **Step 1: Write failing tests**

Create `tests/test_analyst_scorer.py`:

```python
"""Tests for analyst.setup_scorer."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from analyst.setup_scorer import (
    find_new_signals,
    build_score_prompt,
    run,
)

NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

SIG_A = {
    "time_utc": "2026-03-18T09:00:00Z",
    "pattern": "PINBAR",
    "level_checked": 3300.0,
    "gate_result": "OK",
    "action": "TRADE_PLACED",
    "strategy_mode": "LEGACY_LEVELS",
}
SIG_B = {
    "time_utc": "2026-03-18T10:00:00Z",
    "pattern": "ENGULFING",
    "level_checked": 3310.0,
    "gate_result": "NEWS_BLOCK",
    "action": "SKIPPED",
    "strategy_mode": "LEGACY_LEVELS",
}

FRESH_STATE = {
    "meta": {"last_updated_utc": NOW},
    "signal_history": [SIG_B, SIG_A],  # most recent first (deque order)
    "trend": {"bias": "BULLISH"},
}


def test_find_new_signals_no_cursor():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": None})
    assert len(new) == 2


def test_find_new_signals_cursor_after_A():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": "2026-03-18T09:30:00Z"})
    assert len(new) == 1
    assert new[0]["time_utc"] == "2026-03-18T10:00:00Z"


def test_find_new_signals_all_seen():
    new = find_new_signals([SIG_A, SIG_B], cursor={"last_signal_ts": "2026-03-18T10:00:00Z"})
    assert new == []


def test_build_score_prompt_contains_rubric_dimensions():
    prompt = build_score_prompt(SIG_A, trend={"bias": "BULLISH"})
    assert "PINBAR" in prompt
    assert "3300" in prompt
    assert "score" in prompt.lower()


def test_run_scores_new_signals(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))

    claude_response = "Score: 7/10. Clean pinbar at weekly level, good session."
    with patch("analyst.setup_scorer.call_claude", return_value=claude_response):
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)

    scores = json.loads(open(scores_path).read())
    assert len(scores) == 2
    assert scores[0]["signal"]["time_utc"] in ["2026-03-18T09:00:00Z", "2026-03-18T10:00:00Z"]


def test_run_skips_already_scored(tmp_path):
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(cursor_path, "w").write(json.dumps({"last_signal_ts": "2026-03-18T10:00:00Z"}))

    with patch("analyst.setup_scorer.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)
        mock_call.assert_not_called()


def test_run_stale_state_skips(tmp_path):
    state = {"meta": {"last_updated_utc": "2020-01-01T00:00:00Z"}, "signal_history": [SIG_A]}
    state_path = str(tmp_path / "state.json")
    cursor_path = str(tmp_path / "cursor.json")
    scores_path = str(tmp_path / "setup_scores.json")
    open(state_path, "w").write(json.dumps(state))

    with patch("analyst.setup_scorer.call_claude") as mock_call:
        run(state_path=state_path, cursor_path=cursor_path, scores_path=scores_path)
        mock_call.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_analyst_scorer.py -v 2>&1 | head -20
```

Expected: `ImportError`

- [ ] **Step 3: Implement `analyst/setup_scorer.py`**

```python
"""Setup scorer — polls every 5 min Mon-Fri via cron."""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from analyst._utils import (
    append_to_json_list,
    call_claude,
    is_state_stale,
    load_cursor,
    read_json_file,
    save_cursor,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
STALE_SECONDS = 10 * 60

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", "/var/lib/xauex/state.json")
CURSOR_PATH = os.environ.get("XAUEX_SCORER_CURSOR", "/var/lib/xauex/scorer_cursor.json")
SCORES_PATH = os.environ.get("XAUEX_SCORES_OUTPUT", "/var/lib/xauex/setup_scores.json")

_DEFAULT_CURSOR = {"last_signal_ts": None}


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_new_signals(signals: List[Dict], cursor: Dict) -> List[Dict]:
    """Return signals with time_utc strictly after cursor['last_signal_ts']."""
    last_ts = _parse_ts(cursor.get("last_signal_ts"))
    result = []
    for s in signals:
        sig_ts = _parse_ts(s.get("time_utc"))
        if sig_ts is None:
            continue
        if last_ts is None or sig_ts > last_ts:
            result.append(s)
    return sorted(result, key=lambda x: x.get("time_utc", ""))


def build_score_prompt(signal: Dict, trend: Optional[Dict] = None) -> str:
    """Build Claude prompt for scoring a single setup."""
    pattern = signal.get("pattern", "UNKNOWN")
    level = signal.get("level_checked", "?")
    gate = signal.get("gate_result", "?")
    action = signal.get("action", "?")
    ts = signal.get("time_utc", "?")
    strategy = signal.get("strategy_mode", "?")
    trend_bias = (trend or {}).get("bias", "UNKNOWN")

    return f"""You are a trading setup quality analyst for an automated XAUUSD bot. Score this trade setup using the rubric below.

SETUP:
  Time: {ts}
  Pattern: {pattern}
  HTF Level: {level}
  Gate result: {gate}
  Action taken: {action}
  Strategy: {strategy}
  Trend bias (D1/H1 EMA): {trend_bias}

RUBRIC (score each dimension 0-2, max total 10):
  1. Pattern quality — clean textbook {pattern} vs marginal/borderline
  2. HTF level quality — major weekly/monthly confluence vs minor level
  3. Session alignment — London or NY open vs mid-session
  4. Trend alignment — pattern direction matches {trend_bias} bias
  5. Gate clarity — clean pass (OK) vs marginal or blocked

Respond in this format:
Score: X/10
Breakdown: [dimension-by-dimension brief explanation]
Summary: [one sentence overall assessment]"""


def run(
    state_path: str = STATE_PATH,
    cursor_path: str = CURSOR_PATH,
    scores_path: str = SCORES_PATH,
) -> None:
    state = read_json_file(state_path)
    if is_state_stale(state, max_age_seconds=STALE_SECONDS):
        logger.warning("[SCORER] state.json stale or missing — skipping.")
        return

    signals = state.get("signal_history", [])
    if not signals:
        logger.info("[SCORER] No signals in history.")
        return

    cursor = load_cursor(cursor_path, default=_DEFAULT_CURSOR)
    new_signals = find_new_signals(signals, cursor)
    if not new_signals:
        logger.info("[SCORER] No new signals to score.")
        return

    trend = state.get("trend", {})
    latest_ts = cursor.get("last_signal_ts")

    for signal in new_signals:
        ts = signal.get("time_utc", "")
        logger.info("[SCORER] Scoring signal at %s...", ts)
        prompt = build_score_prompt(signal, trend=trend)
        score_text = call_claude(prompt, MODEL)

        entry = {
            "signal_ts": ts,
            "scored_at_utc": utcnow_str(),
            "model": MODEL,
            "signal": signal,
            "breakdown": score_text,
        }
        append_to_json_list(scores_path, entry)

        if not latest_ts or ts > latest_ts:
            latest_ts = ts

    save_cursor(cursor_path, {"last_signal_ts": latest_ts})
    logger.info("[SCORER] Scored %d new signal(s).", len(new_signals))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[SCORER] Failed: %s", e)
        sys.exit(1)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_analyst_scorer.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add analyst/setup_scorer.py tests/test_analyst_scorer.py
git commit -m "feat: add analyst/setup_scorer — signal quality scoring via Claude Sonnet"
```

---

## Task 5: Weekly Review (`analyst/weekly_review.py`)

**Files:**
- Create: `analyst/weekly_review.py`
- Create: `tests/test_analyst_weekly.py`

**Output:** `/var/lib/xauex/weekly_review.json`

### Steps

- [ ] **Step 1: Write failing tests**

Create `tests/test_analyst_weekly.py`:

```python
"""Tests for analyst.weekly_review."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from analyst.weekly_review import (
    get_previous_week_bounds,
    filter_to_week,
    build_review_prompt,
    run,
)

# Monday and Sunday of a known week
MON = datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
SUN = datetime(2026, 3, 15, 23, 59, 59, tzinfo=timezone.utc)

JOURNAL_ENTRIES = [
    {"trade_id": "p1", "journalled_at_utc": "2026-03-10T11:00:00Z", "journal": "Clean trade.", "entry": {"pnl": 140.0}},
    {"trade_id": "p2", "journalled_at_utc": "2026-03-11T14:00:00Z", "journal": "Loss on news.", "entry": {"pnl": -80.0}},
    {"trade_id": "p3", "journalled_at_utc": "2026-03-17T10:00:00Z", "journal": "This week.", "entry": {"pnl": 50.0}},  # outside week
]

SCORE_ENTRIES = [
    {"signal_ts": "2026-03-10T09:00:00Z", "scored_at_utc": "2026-03-10T09:05:00Z", "breakdown": "Score: 8/10"},
    {"signal_ts": "2026-03-17T09:00:00Z", "scored_at_utc": "2026-03-17T09:05:00Z", "breakdown": "Score: 6/10"},  # outside week
]

FRESH_STATE = {
    "meta": {"last_updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
    "risk": {"weekly_pnl": 60.0, "consecutive_losses_today": 0},
    "signal_history": [],
    "shadow_signal_history": [],
}


def test_get_previous_week_bounds():
    # Run on Monday 2026-03-16: previous week is Mon 2026-03-09 to Sun 2026-03-15
    run_at = datetime(2026, 3, 16, 8, 0, 0, tzinfo=timezone.utc)
    start, end = get_previous_week_bounds(run_at)
    assert start == datetime(2026, 3, 9, 0, 0, 0, tzinfo=timezone.utc)
    assert end.date().isoformat() == "2026-03-15"


def test_filter_to_week_journal():
    filtered = filter_to_week(JOURNAL_ENTRIES, "journalled_at_utc", MON, SUN)
    assert len(filtered) == 2
    ids = [e["trade_id"] for e in filtered]
    assert "p1" in ids and "p2" in ids
    assert "p3" not in ids


def test_filter_to_week_scores():
    filtered = filter_to_week(SCORE_ENTRIES, "scored_at_utc", MON, SUN)
    assert len(filtered) == 1
    assert filtered[0]["signal_ts"] == "2026-03-10T09:00:00Z"


def test_build_review_prompt_contains_key_fields():
    journal = filter_to_week(JOURNAL_ENTRIES, "journalled_at_utc", MON, SUN)
    scores = filter_to_week(SCORE_ENTRIES, "scored_at_utc", MON, SUN)
    prompt = build_review_prompt(FRESH_STATE, journal, scores, MON, SUN)
    assert "2026-03-09" in prompt
    assert "2026-03-15" in prompt
    assert "Clean trade" in prompt
    assert "8/10" in prompt


def test_run_writes_output(tmp_path):
    state_path = str(tmp_path / "state.json")
    journal_path = str(tmp_path / "trade_journal.json")
    scores_path = str(tmp_path / "setup_scores.json")
    output_path = str(tmp_path / "weekly_review.json")
    open(state_path, "w").write(json.dumps(FRESH_STATE))
    open(journal_path, "w").write(json.dumps(JOURNAL_ENTRIES))
    open(scores_path, "w").write(json.dumps(SCORE_ENTRIES))

    run_at = datetime(2026, 3, 16, 8, 0, 0, tzinfo=timezone.utc)
    with patch("analyst.weekly_review.call_claude", return_value="Week was profitable."):
        run(
            state_path=state_path,
            journal_path=journal_path,
            scores_path=scores_path,
            output_path=output_path,
            _run_at=run_at,
        )

    result = json.loads(open(output_path).read())
    assert result["review"] == "Week was profitable."
    assert result["model"] == "claude-opus-4-6"
    assert result["week_ending"] == "2026-03-15"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
python3 -m pytest tests/test_analyst_weekly.py -v 2>&1 | head -20
```

Expected: `ImportError`

- [ ] **Step 3: Implement `analyst/weekly_review.py`**

```python
"""Weekly review — runs at 08:00 Monday via cron."""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from analyst._utils import (
    atomic_write_json,
    call_claude,
    is_state_stale,
    read_json_file,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = "claude-opus-4-6"
STALE_SECONDS = 60 * 60  # 1 hour (weekly review is less time-sensitive)

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", "/var/lib/xauex/state.json")
JOURNAL_PATH = os.environ.get("XAUEX_JOURNAL_OUTPUT", "/var/lib/xauex/trade_journal.json")
SCORES_PATH = os.environ.get("XAUEX_SCORES_OUTPUT", "/var/lib/xauex/setup_scores.json")
OUTPUT_PATH = os.environ.get("XAUEX_WEEKLY_OUTPUT", "/var/lib/xauex/weekly_review.json")


def get_previous_week_bounds(run_at: datetime) -> Tuple[datetime, datetime]:
    """Return (Monday 00:00 UTC, Sunday 23:59:59 UTC) of the week prior to run_at."""
    # run_at is Monday; go back 7 days to get last Monday
    last_monday = run_at - timedelta(days=7)
    week_start = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


def filter_to_week(entries: List[Dict], ts_key: str, start: datetime, end: datetime) -> List[Dict]:
    """Return entries whose ts_key falls within [start, end]."""
    result = []
    for entry in entries:
        ts_str = entry.get(ts_key, "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if start <= ts <= end:
                result.append(entry)
        except (ValueError, AttributeError):
            continue
    return result


def build_review_prompt(
    state: Dict,
    journal: List[Dict],
    scores: List[Dict],
    week_start: datetime,
    week_end: datetime,
) -> str:
    """Build the Claude Opus prompt for the weekly review."""
    risk = state.get("risk", {})
    signals = state.get("signal_history", [])
    shadow = state.get("shadow_signal_history", [])

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    journal_text = "No trades this week." if not journal else "\n".join(
        f"  [{e.get('trade_id')}] PnL={e.get('entry', {}).get('pnl', '?'):.2f} | {e.get('journal', '')[:120]}"
        for e in journal
    )

    scores_text = "No scored setups this week." if not scores else "\n".join(
        f"  [{e.get('signal_ts')}] {e.get('breakdown', '')[:100]}"
        for e in scores
    )

    signals_text = f"{len(signals)} signals in history (last 12 shown in state)"
    shadow_text = f"{len(shadow)} shadow signals in history"

    return f"""You are a senior trading analyst reviewing an automated XAUUSD bot's performance for the week of {week_start_str} to {week_end_str}.

RISK SUMMARY:
  Weekly PnL: {risk.get('weekly_pnl', 'N/A')}
  Weekly halted: {risk.get('weekly_halted', False)}
  Consecutive losses (end of week): {risk.get('consecutive_losses_today', 0)}

TRADE JOURNAL ({len(journal)} trades):
{journal_text}

SETUP SCORES ({len(scores)} setups):
{scores_text}

SIGNAL ACTIVITY:
  Primary strategy: {signals_text}
  Shadow strategy: {shadow_text}

Provide a strategic weekly review covering:
1. Overall performance: win rate, RR quality, patterns in outcomes
2. Setup quality: were high-score setups more profitable? Any low-score trades that worked (luck)?
3. Strategy divergence: did primary and shadow strategies agree or disagree? What does that suggest?
4. Risk management: were drawdown limits ever near? Any rule violations?
5. Recommendations: 1-2 concrete, specific parameter or behaviour changes to consider next week (or "no changes recommended" if performance was solid)

Be analytical and direct. Focus on actionable insights, not platitudes."""


def run(
    state_path: str = STATE_PATH,
    journal_path: str = JOURNAL_PATH,
    scores_path: str = SCORES_PATH,
    output_path: str = OUTPUT_PATH,
    _run_at: Optional[datetime] = None,
) -> None:
    run_at = _run_at or datetime.now(timezone.utc)
    week_start, week_end = get_previous_week_bounds(run_at)

    state = read_json_file(state_path)
    if is_state_stale(state, max_age_seconds=STALE_SECONDS):
        logger.warning("[WEEKLY] state.json stale or missing.")
        state = state or {}

    journal_all = read_json_file(journal_path) or []
    scores_all = read_json_file(scores_path) or []

    journal = filter_to_week(journal_all, "journalled_at_utc", week_start, week_end)
    scores = filter_to_week(scores_all, "scored_at_utc", week_start, week_end)

    logger.info(
        "[WEEKLY] Week %s–%s: %d trades, %d scored setups.",
        week_start.strftime("%Y-%m-%d"),
        week_end.strftime("%Y-%m-%d"),
        len(journal),
        len(scores),
    )

    prompt = build_review_prompt(state, journal, scores, week_start, week_end)
    logger.info("[WEEKLY] Calling Claude Opus...")
    review_text = call_claude(prompt, MODEL)

    result = {
        "week_ending": week_end.strftime("%Y-%m-%d"),
        "week_starting": week_start.strftime("%Y-%m-%d"),
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "trades_reviewed": len(journal),
        "setups_reviewed": len(scores),
        "review": review_text,
    }
    atomic_write_json(output_path, result)
    logger.info("[WEEKLY] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[WEEKLY] Failed: %s", e)
        sys.exit(1)
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python3 -m pytest tests/test_analyst_weekly.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add analyst/weekly_review.py tests/test_analyst_weekly.py
git commit -m "feat: add analyst/weekly_review — Monday strategic analysis via Claude Opus"
```

---

## Task 6: Cron Schedule (`ops/analyst.cron`)

**Files:**
- Create: `ops/analyst.cron`

### Steps

- [ ] **Step 1: Create cron file**

Create `ops/analyst.cron`:

```cron
# XAUEX AI Analyst cron jobs
# Install with: crontab ops/analyst.cron
# Requires: claude CLI on PATH for cron user
# Logs to: /var/log/xauex/analyst.log
#
# NOTE: If you already have other crontab entries, merge manually.
# This file replaces the entire crontab if installed with `crontab ops/analyst.cron`.

# Morning brief — 07:45 Mon-Fri
45 7 * * 1-5 cd /home/bolyki/xauex && python3 analyst/morning_brief.py >> /var/log/xauex/analyst.log 2>&1

# Post-trade journal — every 5 min Mon-Fri
*/5 * * * 1-5 cd /home/bolyki/xauex && python3 analyst/post_trade_journal.py >> /var/log/xauex/analyst.log 2>&1

# Setup scorer — every 5 min Mon-Fri
*/5 * * * 1-5 cd /home/bolyki/xauex && python3 analyst/setup_scorer.py >> /var/log/xauex/analyst.log 2>&1

# Weekly review — 08:00 Monday
0 8 * * 1 cd /home/bolyki/xauex && python3 analyst/weekly_review.py >> /var/log/xauex/analyst.log 2>&1
```

- [ ] **Step 2: Run full test suite**

```bash
python3 -m pytest tests/ -v
```

Expected: all tests pass including all new `test_analyst_*` files.

- [ ] **Step 3: Commit**

```bash
git add ops/analyst.cron
git commit -m "feat: add ops/analyst.cron — cron schedule for all AI analyst scripts"
```

---

## Task 7: Smoke Test (Manual)

Run each script once with mocked claude to confirm no import errors or path issues.

- [ ] **Step 1: Test imports work**

```bash
python3 -c "from analyst._utils import find_claude, call_claude; print('utils OK')"
python3 -c "from analyst.morning_brief import run; print('brief OK')"
python3 -c "from analyst.post_trade_journal import run; print('journal OK')"
python3 -c "from analyst.setup_scorer import run; print('scorer OK')"
python3 -c "from analyst.weekly_review import run; print('weekly OK')"
```

Expected: each line prints `OK`.

- [ ] **Step 2: Check claude binary**

```bash
which claude || echo "claude not on PATH — check installation"
```

If missing, install Claude Code CLI and ensure it's on PATH for the cron user.

- [ ] **Step 3: Commit**

No code changes. Final commit message:

```bash
git commit --allow-empty -m "chore: verify AI analyst module smoke tests pass"
```

---

## Installation

After all tasks are complete, install the cron jobs:

```bash
# WARNING: This replaces your entire crontab. If you have existing entries, merge manually.
# View current crontab first:
crontab -l

# Install (or merge into existing):
crontab ops/analyst.cron
```

Check logs after the first run:

```bash
tail -f /var/log/xauex/analyst.log
```
