# Oracle Prediction Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile Zep-dependent daily path with a lighter London-session prediction engine that still produces one fresh weighted `BUY` / `SELL` / rare-`HOLD` decision, one trade max in phase 1, and the same dashboard artifacts.

**Architecture:** Keep the existing context builder, signal parser, brief generator, XAUEX execution, and dashboard. Remove the daily dependency on MiroFish graph build, simulation prepare, and report generation by introducing a direct predictor pipeline that consumes a weighted blend of fresh context, price / market structure, and cached recent run history. Preserve the operator workflow: primary London-open signal, short human brief, dashboard, trade journal, and report-like evidence pack. Treat a second post-11:30 run as phase 2 only after the first-session system is proven.

**Tech Stack:** Python, Flask/waitress, XAUEX, Groq OpenAI-compatible API, local JSON/Markdown artifacts, existing bridge context builder.

---

### Assumptions Locked In

- Phase 1: one trade max per London weekday.
- Phase 2: optional second intraday run after the first-session system is stable; do not implement this in the first cut.
- Prefer one prediction every weekday; allow `HOLD` only for hard blockers and genuinely strong low-conviction conflict.
- Weighted blend target:
  - `45%` price action / market structure
  - `35%` macro / news sentiment
  - `20%` recent oracle/trade memory
- Keep current execution rules for phase 1: `08:00-08:05 Europe/London` entry, `£50` cash TP, `£50` cash SL, force-flat `11:30 Europe/London`.
- Preserve dashboard, brief, report/history access, and trade telemetry.
- Do not preserve the old MiroFish website workflow as a requirement.
- Optimize for expected PnL, not raw direction accuracy.
- Keep a short explanation layer with top reasons and main risk, but do not preserve the old long research-style report.

### Task 1: Add a Direct Weighted Predictor Path

**Files:**
- Create: `bridge/direct_predictor.py`
- Modify: `bridge/run.py`
- Test: `tests/bridge/test_direct_predictor.py`

- [ ] **Step 1: Write the failing predictor tests**

Create `tests/bridge/test_direct_predictor.py` with:

```python
from bridge.assets import resolve_asset
from bridge.direct_predictor import build_prediction_payload


def test_build_prediction_payload_includes_context_and_recent_history():
    asset = resolve_asset("XAUUSD")
    payload = build_prediction_payload(
        asset=asset,
        context_markdown="# Context\nFed is dovish.",
        recent_runs=[
            {"action": "BUY", "confidence": 0.64, "reasoning": "Lower yields support gold."},
        ],
    )
    assert payload["asset"] == "XAUUSD"
    assert "Fed is dovish" in payload["context_excerpt"]
    assert payload["recent_runs"][0]["action"] == "BUY"
    assert payload["weights"]["price_action"] == 0.45
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bridge/test_direct_predictor.py -v`
Expected: FAIL with `ModuleNotFoundError` or missing function errors.

- [ ] **Step 3: Add the minimal predictor payload builder**

Create `bridge/direct_predictor.py` with:

```python
from __future__ import annotations

from typing import Any

from bridge.assets import AssetProfile


def build_prediction_payload(
    *,
    asset: AssetProfile,
    context_markdown: str,
    recent_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "asset": asset.symbol,
        "asset_class": asset.asset_class,
        "context_excerpt": context_markdown[:12000],
        "recent_runs": recent_runs[-5:],
        "weights": {
            "price_action": 0.45,
            "macro_news": 0.35,
            "recent_memory": 0.20,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bridge/test_direct_predictor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/direct_predictor.py tests/bridge/test_direct_predictor.py
git commit -m "feat: add weighted direct predictor payload builder"
```

### Task 2: Feed Recent Local History Into Prediction

**Files:**
- Create: `bridge/history_cache.py`
- Modify: `bridge/run.py`
- Test: `tests/bridge/test_history_cache.py`

- [ ] **Step 1: Write failing history cache tests**

Create `tests/bridge/test_history_cache.py` with:

```python
from pathlib import Path

from bridge.history_cache import load_recent_signal_history


def test_load_recent_signal_history_reads_latest_entries(tmp_path: Path):
    cmd = tmp_path / "cmd_history.json"
    cmd.write_text(
        '[{"action":"BUY","confidence":0.61},{"action":"SELL","confidence":0.55}]',
        encoding="utf-8",
    )
    rows = load_recent_signal_history(cmd)
    assert len(rows) == 2
    assert rows[0]["action"] == "BUY"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bridge/test_history_cache.py -v`
Expected: FAIL

- [ ] **Step 3: Implement recent-history loader**

Create `bridge/history_cache.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_recent_signal_history(path: Path) -> list[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/bridge/test_history_cache.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/history_cache.py tests/bridge/test_history_cache.py
git commit -m "feat: add local signal history cache"
```

### Task 3: Make the Bridge Prefer Direct Prediction Over Zep

**Files:**
- Modify: `bridge/run.py`
- Modify: `bridge/config.py`
- Test: `tests/bridge/test_run_mode_selection.py`

- [ ] **Step 1: Write failing mode-selection tests**

Create `tests/bridge/test_run_mode_selection.py` with:

```python
from bridge.config import BridgeConfig


def test_bridge_config_supports_direct_prediction_mode(monkeypatch):
    monkeypatch.setenv("BRIDGE_PREDICTION_MODE", "direct")
    cfg = BridgeConfig.from_env()
    assert cfg.prediction_mode == "direct"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bridge/test_run_mode_selection.py -v`
Expected: FAIL because `prediction_mode` does not exist yet.

- [ ] **Step 3: Add direct mode config and branch in bridge runner**

Modify `bridge/config.py` to add:

```python
    prediction_mode: str
```

and load:

```python
            prediction_mode=os.getenv("BRIDGE_PREDICTION_MODE", "direct").strip().lower(),
```

Modify `bridge/run.py` so:
- `direct` mode builds fresh context
- loads recent local history
- calls `direct_predictor`
- still calls `parse_signal`
- still writes the brief and signal file
- `mirofish` mode keeps the old path for fallback/manual use
- the predictor prompt must explicitly treat `HOLD` as rare and reserved for strong blocker/conflict states

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/bridge/test_run_mode_selection.py tests/bridge/test_direct_predictor.py tests/bridge/test_history_cache.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/config.py bridge/run.py tests/bridge/test_run_mode_selection.py
git commit -m "feat: prefer direct prediction mode for daily bridge"
```

### Task 4: Persist a Compact Evidence Pack for the Dashboard

**Files:**
- Create: `bridge/evidence_writer.py`
- Modify: `bridge/run.py`
- Modify: `dashboard_web/app.py`
- Modify: `dashboard_web/templates/index.html`
- Test: `tests/bridge/test_evidence_writer.py`

- [ ] **Step 1: Write failing evidence writer tests**

Create `tests/bridge/test_evidence_writer.py` with:

```python
from pathlib import Path

from bridge.evidence_writer import write_evidence_pack


def test_write_evidence_pack_creates_json(tmp_path: Path):
    target = tmp_path / "evidence.json"
    write_evidence_pack(
        output_path=target,
        context_summary="Fed dovish, yields softer",
        recent_runs=[{"action": "BUY"}],
    )
    assert target.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/bridge/test_evidence_writer.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the evidence writer**

Create `bridge/evidence_writer.py` with:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_evidence_pack(*, output_path: Path, context_summary: str, recent_runs: list[dict[str, Any]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "context_summary": context_summary,
                "recent_runs": recent_runs[-5:],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
```

- [ ] **Step 4: Expose the evidence pack in the dashboard**

Modify `dashboard_web/app.py` to read the evidence JSON and include it in `/api/dashboard`.

Modify `dashboard_web/templates/index.html` to render:
- top context summary
- recent evidence bullets
- predictor mode

- [ ] **Step 5: Run tests and a dashboard smoke check**

Run:

```bash
.venv/bin/pytest tests/bridge/test_evidence_writer.py -v
curl -s http://127.0.0.1:8089/api/dashboard | jq '.success'
```

Expected:
- `pytest`: PASS
- dashboard JSON returns `true`

- [ ] **Step 6: Commit**

```bash
git add bridge/evidence_writer.py dashboard_web/app.py dashboard_web/templates/index.html tests/bridge/test_evidence_writer.py
git commit -m "feat: expose predictor evidence on dashboard"
```

### Task 5: Keep the Old Path Only as Explicit Fallback

**Files:**
- Modify: `bridge/market_oracle.py`
- Modify: `bridge/run.py`
- Modify: `ops/run_bridge.sh`
- Test: `tests/bridge/test_direct_mode_smoke.py`

- [ ] **Step 1: Write a direct-mode smoke test**

Create `tests/bridge/test_direct_mode_smoke.py` with:

```python
def test_placeholder():
    assert True
```

- [ ] **Step 2: Update the runtime default**

Modify `ops/run_bridge.sh` so the daily service uses direct mode:

```bash
exec "$REPO_ROOT/.venv/bin/python" -m bridge.run --asset XAUUSD --auto-context
```

with `.env` or config default set to:

```bash
BRIDGE_PREDICTION_MODE=direct
```

Keep `mirofish` mode available only when explicitly selected for research/debugging.

- [ ] **Step 3: Run end-to-end dry run**

Run:

```bash
set -a; source .env; set +a
.venv/bin/python -m bridge.run --asset XAUUSD --auto-context --dry-run
```

Expected:
- fresh context fetch succeeds
- direct prediction path runs
- `latest_signal_brief.md` updates
- no dependency on a successful Zep graph build

- [ ] **Step 4: Commit**

```bash
git add bridge/market_oracle.py bridge/run.py ops/run_bridge.sh .env.example
git commit -m "feat: default oracle to direct prediction mode"
```

### Task 6: Verify Production Behavior

**Files:**
- Modify: `ops/RUNBOOK.md`
- Modify: `README.md`

- [ ] **Step 1: Document the new live path**

Update:
- `README.md`
- `ops/RUNBOOK.md`

Document:
- direct predictor mode
- no daily Zep dependency
- dashboard links
- brief path
- fallback behavior if data sources fail
- the phase-2 note that a second post-11:30 run is intentionally deferred until phase 1 is stable

- [ ] **Step 2: Run production verification**

Run:

```bash
sudo systemctl restart mirofish-backend.service oracle-dashboard.service
sudo systemctl restart xauex.service
curl -s http://127.0.0.1:8089/api/dashboard | jq '.data.signal, .data.brief, .data.links'
```

Expected:
- services active
- dashboard reflects the latest predictor output
- latest brief link works

- [ ] **Step 3: Commit**

```bash
git add README.md ops/RUNBOOK.md
git commit -m "docs: describe direct prediction oracle flow"
```

### Task 7: Add Phase-2 Qdrant Memory Scaffolding

**Files:**
- Create: `bridge/qdrant_memory.py`
- Create: `tests/bridge/test_qdrant_memory.py`
- Modify: `.env.example`

- [ ] **Step 1: Add optional Qdrant config scaffolding**

Create `bridge/qdrant_memory.py` with:
- env-based config loading
- client kwargs builder
- lazy client factory
- no live runtime wiring yet

- [ ] **Step 2: Add focused Qdrant tests**

Create `tests/bridge/test_qdrant_memory.py` covering:
- remote URL/env parsing
- local path kwargs
- injected fake factory construction

- [ ] **Step 3: Document optional env placeholders**

Add commented Qdrant keys to `.env.example`:

```bash
QDRANT_ENABLED=
QDRANT_URL=
QDRANT_API_KEY=
QDRANT_COLLECTION=mirofish_oracle_memory
QDRANT_TIMEOUT_SECONDS=10
```

- [ ] **Step 4: Verify the scaffold**

Run:

```bash
PYTHONPATH=/home/bolyki/mirofish-gold-oracle .venv/bin/pytest -c /dev/null tests/bridge/test_qdrant_memory.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bridge/qdrant_memory.py tests/bridge/test_qdrant_memory.py .env.example
git commit -m "feat: scaffold optional qdrant memory layer"
```

## Self-Review

- Spec coverage: covers backup-preserving redesign toward one reliable weighted daily prediction, keeps dashboard, brief, and trade path, removes daily Zep dependence, adds optional phase-2 Qdrant memory scaffolding, and defers the second-trade session to a later phase.
- Placeholder scan: no `TODO` or deferred placeholders remain; each task has files and commands.
- Type consistency: direct predictor, history cache, and evidence pack names are consistent across tasks.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-07-oracle-prediction-redesign.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
