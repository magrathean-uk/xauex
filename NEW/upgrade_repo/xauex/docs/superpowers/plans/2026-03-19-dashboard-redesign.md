# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add tab navigation to `dashboard.py` so the Trading view is unchanged on tab 1, analyst outputs (morning brief, setup score, weekly review) appear on tab 2, and the trade journal appears on tab 3.

**Architecture:** All changes are confined to `dashboard.py`. Five pure helper functions are extracted for data loading and content formatting; the `compose()` method is refactored to use Textual's `TabbedContent`; `_refresh_state` is extended to load analyst JSON files and update the new panels every 2 seconds.

**Tech Stack:** Python 3.11+, Textual 8.x (`TabbedContent`, `TabPane`, `ScrollableContainer`), pytest

---

## File Map

| File | Change |
|------|--------|
| `dashboard.py` | Modify — add imports, path constants, 5 helper functions, tab bindings, refactor `compose()`, extend `_refresh_state`, update CSS |
| `tests/test_dashboard.py` | Create — unit tests for the 5 helper functions |

---

## Task 1: Helper functions — data loading and content formatting

These are pure (or near-pure) functions that can be tested independently of the Textual framework.

**Files:**
- Modify: `dashboard.py` — add 5 functions after the `_render_chart` block
- Create: `tests/test_dashboard.py`

---

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_dashboard.py`:

```python
import json
import pytest
from dashboard import (
    _load_json_safe,
    _format_morning_brief,
    _format_setup_score,
    _format_weekly_review,
    _format_journal_entries,
)


# ── _load_json_safe ──────────────────────────────────────────────

def test_load_json_safe_returns_data(tmp_path):
    p = tmp_path / "data.json"
    p.write_text('{"key": "value"}')
    assert _load_json_safe(str(p)) == {"key": "value"}

def test_load_json_safe_missing_file_returns_none():
    assert _load_json_safe("/nonexistent/path/file.json") is None

def test_load_json_safe_corrupt_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not valid json {{")
    assert _load_json_safe(str(p)) is None


# ── _format_morning_brief ────────────────────────────────────────

def test_format_morning_brief_none():
    out = _format_morning_brief(None)
    assert "no data" in out.lower()

def test_format_morning_brief_normal():
    data = {
        "generated_at_utc": "2026-03-19T07:45:01Z",
        "model": "claude-sonnet-4-6",
        "stale_state": False,
        "brief": "Gold is testing support at W-Low.",
    }
    out = _format_morning_brief(data)
    assert "2026-03-19T07:45:01Z" in out
    assert "claude-sonnet-4-6" in out
    assert "Gold is testing support at W-Low." in out
    assert "STALE" not in out

def test_format_morning_brief_stale():
    data = {
        "generated_at_utc": "2026-03-19T07:45:01Z",
        "model": "claude-sonnet-4-6",
        "stale_state": True,
        "brief": "Some text.",
    }
    out = _format_morning_brief(data)
    assert "STALE" in out


# ── _format_setup_score ──────────────────────────────────────────

def test_format_setup_score_none():
    out = _format_setup_score(None)
    assert "no data" in out.lower()

def test_format_setup_score_empty_list():
    out = _format_setup_score([])
    assert "no data" in out.lower()

def test_format_setup_score_uses_last_entry():
    scores = [
        {"signal_ts": "2026-03-19T06:00:00Z", "score": 5, "max_score": 10,
         "breakdown": "Old entry.", "signal": {"direction": "SHORT", "pattern": "Engulfing", "level_checked": "M-High"}},
        {"signal_ts": "2026-03-19T09:00:00Z", "score": 7, "max_score": 10,
         "breakdown": "Clean pinbar at W-Low.", "signal": {"direction": "LONG", "pattern": "Pinbar", "level_checked": "W-Low"}},
    ]
    out = _format_setup_score(scores)
    assert "7/10" in out
    assert "LONG" in out
    assert "Pinbar" in out
    assert "W-Low" in out
    assert "Clean pinbar at W-Low." in out
    # Should NOT show first entry
    assert "5/10" not in out


# ── _format_weekly_review ────────────────────────────────────────

def test_format_weekly_review_none():
    out = _format_weekly_review(None)
    assert "no data" in out.lower()

def test_format_weekly_review_normal():
    data = {
        "week_starting": "2026-03-09",
        "week_ending": "2026-03-15",
        "generated_at_utc": "2026-03-16T08:00:00Z",
        "model": "claude-opus-4-6",
        "trades_reviewed": 4,
        "setups_reviewed": 12,
        "review": "Win rate 75%. Strong performance.",
    }
    out = _format_weekly_review(data)
    assert "2026-03-09" in out
    assert "2026-03-15" in out
    assert "4 trades" in out
    assert "Win rate 75%" in out


# ── _format_journal_entries ──────────────────────────────────────

def test_format_journal_entries_none():
    out = _format_journal_entries(None)
    assert "no entries" in out.lower()

def test_format_journal_entries_empty():
    out = _format_journal_entries([])
    assert "no entries" in out.lower()

def test_format_journal_entries_shows_newest_first():
    entries = [
        {
            "trade_id": "pos001",
            "journalled_at_utc": "2026-03-19T09:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "LONG", "entry_price": 2839.50, "pnl": 110.0, "pattern": "Pinbar"},
            "journal": "First trade journalled.",
        },
        {
            "trade_id": "pos002",
            "journalled_at_utc": "2026-03-19T11:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "SHORT", "entry_price": 2861.00, "pnl": -45.0, "pattern": "Engulfing"},
            "journal": "Second trade journalled.",
        },
    ]
    out = _format_journal_entries(entries)
    # pos002 (newer) should appear before pos001 (older)
    assert out.index("pos002") < out.index("pos001")
    assert "2839.50" in out
    assert "First trade journalled." in out

def test_format_journal_entries_caps_at_20(tmp_path):
    entries = [
        {
            "trade_id": f"pos{i:03d}",
            "journalled_at_utc": f"2026-03-{i:02d}T09:00:00Z",
            "model": "claude-sonnet-4-6",
            "entry": {"direction": "LONG", "entry_price": 2840.0, "pnl": 10.0},
            "journal": f"Journal entry {i}.",
        }
        for i in range(1, 26)  # 25 entries
    ]
    out = _format_journal_entries(entries)
    # Only last 20 entries shown (pos006 through pos025)
    assert "pos025" in out
    assert "pos006" in out
    assert "pos005" not in out
    assert "pos001" not in out
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
pytest tests/test_dashboard.py -v 2>&1 | head -30
```

Expected: `ImportError` or `FAILED` — functions don't exist yet.

- [ ] **Step 1.3: Add helper functions to dashboard.py**

Add these imports at the top of `dashboard.py` (after existing imports):

```python
from typing import Any
```

Add these path constants after the existing `_CMD_PATH` line:

```python
_BRIEF_PATH = os.getenv("MORNING_BRIEF_PATH", "/var/lib/xauex/morning_brief.json")
_JOURNAL_PATH = os.getenv("TRADE_JOURNAL_PATH", "/var/lib/xauex/trade_journal.json")
_SCORES_PATH = os.getenv("SETUP_SCORES_PATH", "/var/lib/xauex/setup_scores.json")
_REVIEW_PATH = os.getenv("WEEKLY_REVIEW_PATH", "/var/lib/xauex/weekly_review.json")
```

Add these five functions after the `_render_chart` function block (before `KillSwitchModal`):

```python
# ─────────────────────────────────────────────────────────
# Analyst data helpers
# ─────────────────────────────────────────────────────────

def _load_json_safe(path: str) -> Optional[Any]:
    """Load JSON from path; return None on any error."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _format_morning_brief(data: Optional[dict]) -> str:
    if not data:
        return "MORNING BRIEF\n  (no data yet)"
    ts = data.get("generated_at_utc", "")
    model = data.get("model", "")
    stale = data.get("stale_state", False)
    brief = data.get("brief", "")
    header = f"MORNING BRIEF  {ts}  [{model}]"
    warning = "\n  [STALE DATA — bot may be down]\n" if stale else "\n"
    return header + warning + brief


def _format_setup_score(data: Optional[list]) -> str:
    if not data:
        return "LATEST SETUP SCORE\n  (no data yet)"
    entry = data[-1]
    ts = entry.get("signal_ts", "")
    score = entry.get("score", "?")
    max_score = entry.get("max_score", 10)
    breakdown = entry.get("breakdown", "")
    sig = entry.get("signal", {})
    direction = sig.get("direction", "?")
    pattern = sig.get("pattern", "?")
    level = sig.get("level_checked", "?")
    return (
        f"LATEST SETUP SCORE  {ts}  {score}/{max_score}\n"
        f"  {direction} {pattern} @ {level}\n"
        f"{breakdown}"
    )


def _format_weekly_review(data: Optional[dict]) -> str:
    if not data:
        return "WEEKLY REVIEW\n  (no data yet)"
    week_start = data.get("week_starting", "")
    week_end = data.get("week_ending", "")
    trades = data.get("trades_reviewed", 0)
    setups = data.get("setups_reviewed", 0)
    review = data.get("review", "")
    return (
        f"WEEKLY REVIEW  {week_start} → {week_end}"
        f"  ({trades} trades, {setups} setups)\n"
        f"{review}"
    )


def _format_journal_entries(data: Optional[list]) -> str:
    if not data:
        return "TRADE JOURNAL\n  (no entries yet)"
    lines = ["TRADE JOURNAL"]
    # Newest first, cap at 20
    for e in reversed(data[-20:]):
        trade_id = e.get("trade_id", "?")
        ts = e.get("journalled_at_utc", "")[:16]
        entry = e.get("entry", {})
        direction = entry.get("direction", "?")
        entry_price = entry.get("entry_price", 0.0)
        pnl = entry.get("pnl", 0.0)
        journal = e.get("journal", "")
        try:
            price_str = f"{float(entry_price):.2f}"
            pnl_str = f"{float(pnl):+.2f}"
        except (TypeError, ValueError):
            price_str = str(entry_price)
            pnl_str = str(pnl)
        lines.append(f"\n  [{ts}] #{trade_id}  {direction} @ {price_str}  P&L: {pnl_str}")
        lines.append(f"  {journal}")
        lines.append("  " + "─" * 60)
    return "\n".join(lines)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
pytest tests/test_dashboard.py -v
```

Expected: all tests pass.

- [ ] **Step 1.5: Run full test suite to confirm nothing broken**

```bash
make test
```

Expected: existing tests still pass (only the 2 pre-existing `test_backtester.py` failures are acceptable).

- [ ] **Step 1.6: Commit**

```bash
git add dashboard.py tests/test_dashboard.py
git commit -m "feat: add analyst helper functions and tests to dashboard"
```

---

## Task 2: Refactor compose() to TabbedContent + wire refresh

**Files:**
- Modify: `dashboard.py` — imports, CSS, `compose()`, bindings, `_refresh_state`

---

- [ ] **Step 2.1: Add new imports to dashboard.py**

At the top of `dashboard.py`, extend the Textual imports line:

```python
# Before:
from textual.widgets import Footer, Header, Label, Static

# After:
from textual.containers import ScrollableContainer
from textual.widgets import Footer, Header, Label, Static, TabbedContent, TabPane
```

- [ ] **Step 2.2: Update CSS**

Replace the existing `CSS` string in `XAUEXDashboard`:

```python
CSS = """
/* Trading tab — existing panel sizing */
#status-bar { height: 1; }
#account-panel { width: 25; }
#trend-panel { width: 25; }
#levels-panel { width: 1fr; }
#runtime-panel { width: 26; }
#top-row { height: 7; }
#positions-panel { height: 6; }
#signal-panel { height: 6; }
#chart-panel { height: 12; }
#history-panel { height: 12; }
#risk-panel { height: 3; }

/* Analyst and Journal tab panels */
#analyst-brief  { padding: 0 1; margin-bottom: 1; }
#analyst-score  { padding: 0 1; margin-bottom: 1; }
#analyst-review { padding: 0 1; margin-bottom: 1; }
#journal-entries { padding: 0 1; }
"""
```

- [ ] **Step 2.3: Update BINDINGS**

Replace the existing `BINDINGS` list:

```python
BINDINGS = [
    Binding("1", "tab_trading", "Trading", show=False),
    Binding("2", "tab_analyst", "Analyst", show=False),
    Binding("3", "tab_journal", "Journal", show=False),
    Binding("k", "kill_switch", "Kill switch", priority=True),
    Binding("q", "quit", "Quit"),
]
```

- [ ] **Step 2.4: Refactor compose()**

Replace the existing `compose()` method:

```python
def compose(self) -> ComposeResult:
    yield Header(show_clock=True)
    with TabbedContent(initial="trading"):
        with TabPane("Trading [1]", id="trading"):
            with Vertical():
                yield Static("● WAITING FOR BOT...", id="status-bar")
                with Horizontal(id="top-row"):
                    yield Static("", id="account-panel")
                    yield Static("", id="trend-panel")
                    yield Static("", id="levels-panel")
                    yield Static("", id="runtime-panel")
                yield Static("", id="positions-panel")
                yield Static("", id="signal-panel")
                yield Static("", id="chart-panel")
                yield Static("", id="history-panel")
                yield Static("", id="risk-panel")
        with TabPane("Analyst [2]", id="analyst"):
            with ScrollableContainer():
                yield Static("", id="analyst-brief")
                yield Static("", id="analyst-score")
                yield Static("", id="analyst-review")
        with TabPane("Journal [3]", id="journal"):
            with ScrollableContainer():
                yield Static("", id="journal-entries")
    yield Footer()
```

- [ ] **Step 2.5: Add tab-switch action methods**

Add these three methods to `XAUEXDashboard` (after `on_mount`):

```python
def action_tab_trading(self) -> None:
    self.query_one(TabbedContent).active = "trading"

def action_tab_analyst(self) -> None:
    self.query_one(TabbedContent).active = "analyst"

def action_tab_journal(self) -> None:
    self.query_one(TabbedContent).active = "journal"
```

- [ ] **Step 2.6: Extend _refresh_state to load analyst data**

Replace the existing `_refresh_state` method:

```python
def _refresh_state(self) -> None:
    # Bot state
    try:
        with open(_STATE_PATH, "r") as f:
            state = json.load(f)
        self._update_ui(state)
    except Exception:
        self.query_one("#status-bar", Static).update("● WAITING FOR BOT...")

    # Analyst outputs — each loaded independently; missing files → "no data yet"
    brief = _load_json_safe(_BRIEF_PATH)
    scores = _load_json_safe(_SCORES_PATH)
    review = _load_json_safe(_REVIEW_PATH)
    journal = _load_json_safe(_JOURNAL_PATH)

    self.query_one("#analyst-brief", Static).update(_format_morning_brief(brief))
    self.query_one("#analyst-score", Static).update(_format_setup_score(scores))
    self.query_one("#analyst-review", Static).update(_format_weekly_review(review))
    self.query_one("#journal-entries", Static).update(_format_journal_entries(journal))
```

- [ ] **Step 2.7: Run tests**

```bash
make test
```

Expected: all existing tests pass. (Dashboard tests don't test the Textual app structure — they test the helper functions, which haven't changed.)

- [ ] **Step 2.8: Smoke test — start the dashboard and verify tabs**

```bash
python3 -m dashboard
```

Verify manually:
- Dashboard starts without errors
- Tab bar visible at top showing `Trading [1]`, `Analyst [2]`, `Journal [3]`
- Press `1`, `2`, `3` — tabs switch correctly
- Trading tab shows existing bot panels (or "WAITING FOR BOT..." if bot is not running)
- Analyst tab shows `(no data yet)` sections if analyst files are absent, or real content if present
- Journal tab shows `(no entries yet)` if journal is absent, or entries if present
- `Q` quits cleanly
- `K` brings up kill switch modal

- [ ] **Step 2.9: Commit**

```bash
git add dashboard.py
git commit -m "feat: add tab navigation to dashboard with Analyst and Journal tabs"
```
