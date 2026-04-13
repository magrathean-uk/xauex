# Free Fed Policy Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add free official Fed/FRED policy-state context to XAUEX's structured signal packet without introducing paid FedWatch or brittle scraping.

**Architecture:** Add one focused policy-context adapter that reads the official FOMC calendar plus free FRED rate series, normalize the result into an additive `policy_context` block inside the existing market snapshot, and surface it through the direct report and signal packet. Keep all failures non-fatal and preserve the current live signal contract.

**Tech Stack:** Python, `httpx`, stdlib date handling, existing XAUEX signal pipeline, pytest.

---

## File Structure

- Create: `xauex/signal/policy_context.py`
  - fetch and normalize official FOMC calendar + FRED rate series
  - derive `days_to_fomc`, `fomc_window_state`, and rate-gap metrics

- Modify: `xauex/signal/market_snapshot.py`
  - call the new policy-context adapter
  - embed `policy_context` into the structured market snapshot
  - merge policy-context status into existing freshness/summary output

- Modify: `xauex/signal/direct_predictor.py`
  - include `policy_context` in the report and recent actions

- Modify: `xauex/signal/run.py`
  - ensure the packet carries the enriched market snapshot through the dry/live paths

- Test: `tests/bridge/test_policy_context.py`
  - unit tests for normalization and derived metrics

- Modify: `tests/bridge/test_market_snapshot.py`
  - verify `policy_context` is carried into the snapshot

- Modify: `tests/bridge/test_direct_predictor.py`
  - verify the direct report renders policy context

- Modify: `.env.example`
- Modify: `xauex/.env.example`
  - document any optional overrides only if needed

---

### Task 1: Add Policy Context Adapter

**Files:**
- Create: `xauex/signal/policy_context.py`
- Test: `tests/bridge/test_policy_context.py`

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone

from xauex.signal.policy_context import (
    _derive_fomc_window_state,
    _normalize_policy_context,
)


def test_normalize_policy_context_derives_target_gap_metrics():
    payload = _normalize_policy_context(
        next_fomc_date="2026-05-06",
        effective_rate=3.64,
        target_lower=3.50,
        target_upper=3.75,
        now=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )

    assert payload["next_fomc_date"] == "2026-05-06"
    assert payload["target_mid"] == 3.625
    assert payload["dff_minus_target_mid_bps"] == 1.5
    assert payload["fomc_window_state"] == "normal"


def test_derive_fomc_window_state_marks_approaching_today_and_recent():
    assert _derive_fomc_window_state(days_to_fomc=3) == "approaching"
    assert _derive_fomc_window_state(days_to_fomc=0) == "today"
    assert _derive_fomc_window_state(days_to_fomc=-1) == "recent"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bridge/test_policy_context.py -q`
Expected: import error or missing-function failure from `xauex.signal.policy_context`

- [ ] **Step 3: Write minimal implementation**

```python
def _derive_fomc_window_state(*, days_to_fomc: int) -> str:
    if days_to_fomc == 0:
        return "today"
    if 1 <= days_to_fomc <= 3:
        return "approaching"
    if days_to_fomc == -1:
        return "recent"
    return "normal"


def _normalize_policy_context(*, next_fomc_date, effective_rate, target_lower, target_upper, now):
    target_mid = round((target_lower + target_upper) / 2.0, 3)
    days_to_fomc = (datetime.strptime(next_fomc_date, "%Y-%m-%d").date() - now.date()).days
    return {
        "status": "available",
        "source": "fed_fomc_calendar+fred",
        "next_fomc_date": next_fomc_date,
        "days_to_fomc": days_to_fomc,
        "fomc_window_state": _derive_fomc_window_state(days_to_fomc=days_to_fomc),
        "effective_fed_funds_rate": effective_rate,
        "target_lower": target_lower,
        "target_upper": target_upper,
        "target_mid": target_mid,
        "dff_minus_target_mid_bps": round((effective_rate - target_mid) * 100, 1),
        "dff_minus_upper_bps": round((effective_rate - target_upper) * 100, 1),
        "dff_minus_lower_bps": round((effective_rate - target_lower) * 100, 1),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/bridge/test_policy_context.py -q`
Expected: `2 passed`

- [ ] **Step 5: Expand the adapter to real fetches**

Implement:
- free FRED fetch for `DFF`, `DFEDTARU`, `DFEDTARL`
- official FOMC calendar parsing from the Fed
- graceful fallback:

```python
{
    "status": "unavailable",
    "source": "fed_fomc_calendar+fred",
    "summary": "Official Fed policy context is unavailable."
}
```

- [ ] **Step 6: Run adapter tests again**

Run: `python3 -m pytest tests/bridge/test_policy_context.py -q`
Expected: pass with the real adapter still satisfying the normalization cases

- [ ] **Step 7: Commit**

```bash
git add tests/bridge/test_policy_context.py xauex/signal/policy_context.py
git commit -m "Add free Fed policy context adapter"
```

### Task 2: Embed Policy Context Into Market Snapshot

**Files:**
- Modify: `xauex/signal/market_snapshot.py`
- Modify: `tests/bridge/test_market_snapshot.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_build_market_snapshot_includes_policy_context(monkeypatch):
    monkeypatch.setattr(
        "xauex.signal.market_snapshot.fetch_policy_context",
        lambda config: {
            "status": "available",
            "next_fomc_date": "2026-05-06",
            "days_to_fomc": 22,
            "fomc_window_state": "normal",
            "effective_fed_funds_rate": 3.64,
            "target_mid": 3.625,
            "summary": "Daily effective fed funds remains near the target midpoint.",
        },
    )
    snapshot = build_market_snapshot(asset=resolve_asset("XAUUSD"), config=cfg, context_items=[])
    assert snapshot["policy_context"]["status"] == "available"
    assert snapshot["input_freshness"]["policy_context_state"] == "available"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bridge/test_market_snapshot.py::test_build_market_snapshot_includes_policy_context -q`
Expected: missing `fetch_policy_context` hook or missing `policy_context`

- [ ] **Step 3: Write minimal implementation**

In `xauex/signal/market_snapshot.py`, import and embed the new adapter:

```python
from xauex.signal.policy_context import fetch_policy_context

policy_context = fetch_policy_context(config=config)
freshness["policy_context_state"] = policy_context.get("status", "unknown")
freshness["policy_context_summary"] = policy_context.get("summary", "")
```

and return:

```python
{
    "series": series_payload,
    "policy_context": policy_context,
    ...
}
```

- [ ] **Step 4: Run the narrow test**

Run: `python3 -m pytest tests/bridge/test_market_snapshot.py::test_build_market_snapshot_includes_policy_context -q`
Expected: `1 passed`

- [ ] **Step 5: Run the full market snapshot test file**

Run: `python3 -m pytest tests/bridge/test_market_snapshot.py -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add xauex/signal/market_snapshot.py tests/bridge/test_market_snapshot.py
git commit -m "Embed policy context in market snapshot"
```

### Task 3: Surface Policy Context In Reports And Actions

**Files:**
- Modify: `xauex/signal/direct_predictor.py`
- Modify: `tests/bridge/test_direct_predictor.py`

- [ ] **Step 1: Write the failing report test**

```python
def test_render_direct_report_includes_policy_context():
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\\nFed policy context present.",
        recent_runs=[],
        state_snapshot=state,
        market_snapshot={
            "series": {},
            "policy_context": {
                "status": "available",
                "next_fomc_date": "2026-05-06",
                "days_to_fomc": 22,
                "fomc_window_state": "normal",
                "summary": "Daily effective fed funds remains near the target midpoint.",
            },
            "input_freshness": {},
        },
    )
    report = render_direct_report(payload)
    assert "Policy Context" in report
    assert "next_fomc_date" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bridge/test_direct_predictor.py::test_render_direct_report_includes_policy_context -q`
Expected: report missing the new section

- [ ] **Step 3: Write minimal implementation**

Add a new report section:

```python
policy_context = market_snapshot.get("policy_context") or {}
if policy_context:
    lines.append("")
    lines.append("## Policy Context")
    for key, value in policy_context.items():
        if key in {"status", "next_fomc_date", "days_to_fomc", "fomc_window_state", "summary"}:
            lines.append(f"- {key}: {value}")
```

Also add a recent action:

```python
if policy_context.get("status") == "available":
    actions.append({
        "agent_name": "policy_context",
        "action_type": "NEUTRAL",
        "content": str(policy_context.get("summary", ""))[:260],
    })
```

- [ ] **Step 4: Run the narrow test**

Run: `python3 -m pytest tests/bridge/test_direct_predictor.py::test_render_direct_report_includes_policy_context -q`
Expected: `1 passed`

- [ ] **Step 5: Run the file**

Run: `python3 -m pytest tests/bridge/test_direct_predictor.py -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add xauex/signal/direct_predictor.py tests/bridge/test_direct_predictor.py
git commit -m "Expose policy context in signal reports"
```

### Task 4: Wire The Run Path And Preserve Dry-Run Safety

**Files:**
- Modify: `xauex/signal/run.py`
- Modify: `tests/bridge/test_run_mode_selection.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_build_direct_prediction_artifacts_carries_policy_context(monkeypatch):
    monkeypatch.setattr(
        "xauex.signal.run.build_market_snapshot",
        lambda **kwargs: {
            "series": {},
            "policy_context": {"status": "available", "next_fomc_date": "2026-05-06"},
            "input_freshness": {"policy_context_state": "available"},
        },
    )
    artifacts = build_direct_prediction_artifacts(
        config=cfg,
        asset_symbol="XAUUSD",
        context_markdown="# Context\\nFed policy context present.",
    )
    assert artifacts["payload"]["market_snapshot"]["policy_context"]["status"] == "available"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/bridge/test_run_mode_selection.py::test_build_direct_prediction_artifacts_carries_policy_context -q`
Expected: missing payload field or failing assertion

- [ ] **Step 3: Write minimal implementation**

Ensure `build_direct_prediction_artifacts()` simply carries through the enriched market snapshot and does not special-case away the new block.

- [ ] **Step 4: Run the narrow test**

Run: `python3 -m pytest tests/bridge/test_run_mode_selection.py::test_build_direct_prediction_artifacts_carries_policy_context -q`
Expected: `1 passed`

- [ ] **Step 5: Run the file**

Run: `python3 -m pytest tests/bridge/test_run_mode_selection.py -q`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add xauex/signal/run.py tests/bridge/test_run_mode_selection.py
git commit -m "Carry policy context through signal build path"
```

### Task 5: Update Env Examples And Verify End-To-End Dry Run

**Files:**
- Modify: `.env.example`
- Modify: `xauex/.env.example`

- [ ] **Step 1: Document only what is necessary**

Keep env additions minimal. If no config is needed beyond existing free-source settings, do not add new knobs.

- [ ] **Step 2: Run focused suite**

Run:

```bash
python3 -m pytest \
  tests/bridge/test_policy_context.py \
  tests/bridge/test_market_snapshot.py \
  tests/bridge/test_direct_predictor.py \
  tests/bridge/test_run_mode_selection.py -q
```

Expected: all tests pass

- [ ] **Step 3: Compile the modified modules**

Run:

```bash
python3 -m py_compile \
  xauex/signal/policy_context.py \
  xauex/signal/market_snapshot.py \
  xauex/signal/direct_predictor.py \
  xauex/signal/run.py
```

Expected: exit 0

- [ ] **Step 4: Run a real dry-run**

Run:

```bash
set -a && source .env && set +a && \
LOG_LEVEL=WARNING ./.venv/bin/python -m xauex.signal.run --asset XAUUSD --auto-context --dry-run
```

Expected:
- no live files written
- signal JSON still prints
- `decision_packet.market_snapshot.policy_context` is present when free sources succeed
- signal still completes if one policy source is unavailable

- [ ] **Step 5: Commit**

```bash
git add .env.example xauex/.env.example
git commit -m "Document free Fed policy context inputs"
```

---

## Self-Review

- Spec coverage:
  - free official sources only: covered in Tasks 1-2
  - no paid FedWatch path: preserved by architecture and absence of paid dependency
  - no Forex Factory in live logic: explicitly excluded
  - additive-only packet/report behavior: covered in Tasks 2-4
  - dry-run safety: covered in Task 5

- Placeholder scan:
  - no TBD/TODO placeholders
  - each task includes exact files, commands, and expected outputs

- Type consistency:
  - `policy_context` is the single block name everywhere
  - `fomc_window_state`, `days_to_fomc`, and `dff_minus_*_bps` names are consistent

Plan complete and saved to `docs/superpowers/plans/2026-04-14-free-fed-policy-context.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration

2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
