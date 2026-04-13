# Signal Package Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `xauex.signal` the canonical implementation for signal generation while keeping temporary `bridge/` compatibility wrappers for older imports.

**Architecture:** Copy the current signal-generation modules into `xauex/signal/` and update the live entrypoints to import from that package. Keep `bridge/` modules as thin wrappers that re-export the XAUEX implementation during the transition so unrelated callers keep working. Do not change signal behavior unless a test proves the package boundary needs it.

**Tech Stack:** Python 3.13, `pytest`, `httpx`, `openai`, `python-dotenv`, `feedparser`, `beautifulsoup4`

---

### Task 1: Add canonical `xauex.signal` implementation modules

**Files:**
- Create: `xauex/signal/assets.py`
- Create: `xauex/signal/config.py`
- Create: `xauex/signal/context_builder.py`
- Create: `xauex/signal/direct_predictor.py`
- Create: `xauex/signal/evidence_writer.py`
- Create: `xauex/signal/history_cache.py`
- Create: `xauex/signal/market_oracle.py`
- Create: `xauex/signal/qdrant_memory.py`
- Create: `xauex/signal/brief_writer.py`
- Create: `xauex/signal/signal_parser.py`
- Create: `xauex/signal/signal_writer.py`
- Create: `xauex/signal/source_registry.py`
- Create: `xauex/signal/export_sources.py`
- Create: `xauex/signal/gold_oracle.py`
- Modify: `xauex/signal/__init__.py`
- Modify: `xauex/signal/run.py`
- Test: `tests/bridge/test_history_cache.py`
- Test: `tests/bridge/test_signal_writer.py`
- Test: `tests/bridge/test_evidence_writer.py`
- Test: `tests/bridge/test_direct_predictor.py`
- Test: `tests/bridge/test_qdrant_memory.py`
- Test: `tests/bridge/test_run_mode_selection.py`
- Test: `xauex/tests/test_mirofish_signal_policy.py`

- [ ] **Step 1: Write the failing tests**

```python
from xauex.signal.assets import resolve_asset
from xauex.signal.signal_writer import write_signal


def test_signal_package_is_importable():
    asset = resolve_asset("XAUUSD")
    assert asset.symbol == "XAUUSD"


def test_write_signal_uses_xauex_signal_key_and_preserves_notes(tmp_path):
    output_path = tmp_path / "cmd.json"
    output_path.write_text('{"kill_switch": true, "notes": ["keep-me"]}', encoding="utf-8")

    write_signal({"symbol": "XAUUSD", "action": "BUY", "confidence": 0.81}, str(output_path))

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["xauex_signal"]["action"] == "BUY"
```

- [ ] **Step 2: Run the package-level tests and confirm the new imports fail before implementation**

Run:
`pytest tests/bridge/test_signal_writer.py tests/bridge/test_history_cache.py tests/bridge/test_direct_predictor.py tests/bridge/test_evidence_writer.py tests/bridge/test_qdrant_memory.py tests/bridge/test_run_mode_selection.py xauex/tests/test_mirofish_signal_policy.py -v`

Expected: import failures for `xauex.signal.*` until the new package files exist.

- [ ] **Step 3: Implement the new package modules by moving the current bridge logic into `xauex/signal/`**

```python
# xauex/signal/run.py
from xauex.signal.assets import all_symbols, resolve_asset
from xauex.signal.config import BridgeConfig
from xauex.signal.context_builder import ContextBuilder
from xauex.signal.direct_predictor import build_prediction_payload, build_recent_actions, render_direct_report
from xauex.signal.evidence_writer import write_evidence_pack
from xauex.signal.history_cache import load_recent_trade_memory, load_state_snapshot
from xauex.signal.market_oracle import MarketOracle
from xauex.signal.qdrant_memory import retrieve_qdrant_memory_snippets
from xauex.signal.signal_parser import parse_signal
from xauex.signal.signal_writer import write_signal
```

- [ ] **Step 4: Run the targeted tests and confirm the new package passes**

Run:
`pytest tests/bridge/test_signal_writer.py tests/bridge/test_history_cache.py tests/bridge/test_direct_predictor.py tests/bridge/test_evidence_writer.py tests/bridge/test_qdrant_memory.py tests/bridge/test_run_mode_selection.py xauex/tests/test_mirofish_signal_policy.py -v`

Expected: all targeted signal tests pass with imports coming from `xauex.signal`.

### Task 2: Convert `bridge/` to compatibility wrappers

**Files:**
- Modify: `bridge/__init__.py`
- Modify: `bridge/assets.py`
- Modify: `bridge/config.py`
- Modify: `bridge/context_builder.py`
- Modify: `bridge/direct_predictor.py`
- Modify: `bridge/evidence_writer.py`
- Modify: `bridge/export_sources.py`
- Modify: `bridge/gold_oracle.py`
- Modify: `bridge/history_cache.py`
- Modify: `bridge/market_oracle.py`
- Modify: `bridge/qdrant_memory.py`
- Modify: `bridge/brief_writer.py`
- Modify: `bridge/signal_parser.py`
- Modify: `bridge/signal_writer.py`
- Modify: `bridge/source_registry.py`
- Modify: `bridge/run.py`

- [ ] **Step 1: Write wrapper-focused regression tests**

```python
from bridge.signal_writer import write_signal as bridge_write_signal
from xauex.signal.signal_writer import write_signal as xauex_write_signal


def test_bridge_wrapper_resolves_to_xauex_signal_impl():
    assert bridge_write_signal is xauex_write_signal
```

- [ ] **Step 2: Run the wrapper test and confirm the old bridge path still works**

Run:
`pytest tests/bridge/test_signal_writer.py -v`

Expected: the test still passes after the wrapper rewrite.

- [ ] **Step 3: Replace bridge implementations with thin re-exports**

```python
# bridge/signal_writer.py
from xauex.signal.signal_writer import *  # noqa: F401,F403
```

- [ ] **Step 4: Run the same targeted test set again**

Run:
`pytest tests/bridge/test_signal_writer.py tests/bridge/test_run_mode_selection.py xauex/tests/test_mirofish_signal_policy.py -v`

Expected: old bridge imports continue to work, but the implementation now lives in `xauex.signal`.

### Task 3: Update live entrypoints and remove obsolete direct bridge imports

**Files:**
- Modify: `main.py`
- Modify: `ops/run_bridge.sh`
- Modify: `xauex/tests/test_mirofish_signal_policy.py`
- Modify: `tests/bridge/test_run_mode_selection.py`
- Modify: `tests/bridge/test_direct_predictor.py`
- Modify: `tests/bridge/test_evidence_writer.py`
- Modify: `tests/bridge/test_history_cache.py`
- Modify: `tests/bridge/test_qdrant_memory.py`
- Modify: `tests/bridge/test_signal_writer.py`

- [ ] **Step 1: Update imports to `xauex.signal.*` in the live entrypoints and tests**

```python
from xauex.signal.config import BridgeConfig
from xauex.signal.signal_parser import parse_signal
from xauex.signal.signal_writer import write_signal
```

- [ ] **Step 2: Run the narrow live-path tests and command-line smoke tests**

Run:
`pytest tests/bridge/test_run_mode_selection.py tests/bridge/test_signal_writer.py xauex/tests/test_mirofish_signal_policy.py -v`

Expected: the updated live path still uses the same behavior with the new package imports.

- [ ] **Step 3: Keep `bridge/` only as a compatibility layer**

If every signal-facing import has moved to `xauex.signal` and all targeted tests pass, leave the wrapper modules in place for now and defer full `bridge/` deletion until no external code paths depend on it.

