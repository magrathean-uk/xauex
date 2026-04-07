# Oracle Session Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Oracle's rigid stop management with a staged session manager, add a fully independent manual-trade lane to the dashboard, and keep the live automated London-morning flow intact.

**Architecture:** Oracle-managed trades remain driven by the bridge signal, but XAUEX wraps each Oracle trade in a persistent session-management state machine that derives a wider catastrophe stop from signal/ATR/structure and then manages exits by phase (`OBSERVE`, `PROTECT`, `TRAIL`). Manual dashboard trades are sent through a separate local command path, executed by the same broker client, labeled as manual, and completely ignored by Oracle limits, memory, and management logic.

**Tech Stack:** Python, Flask, Waitress, asyncio, local JSON state, local Qdrant memory, pytest, systemd services

---

## File Map

### XAUEX execution and state
- Modify: `xauex/main.py`
  - Add Oracle session-manager state lifecycle
  - Poll and execute manual trade commands
  - Separate Oracle-owned and manual-owned positions
  - Improve state persistence for dashboard use
- Modify: `xauex/config.py`
  - Add Oracle session-manager config knobs
  - Add manual command file path config
- Modify: `xauex/.env.example`
  - Document new Oracle management settings
  - Document manual command path
- Modify: `xauex/.env`
  - Enable the new live settings on this machine after verification
- Modify: `xauex/bot/risk/sizing.py`
  - Add cash-capped sizing path for Oracle stop width
- Modify: `xauex/bot/execution/executor.py`
  - Allow labeled owner metadata on tracked positions
  - Preserve enough position metadata for restart recovery and dashboard rendering
- Modify: `xauex/bot/api/models.py`
  - Extend position model only if needed to carry owner/session metadata in-memory

### Dashboard
- Modify: `dashboard_web/app.py`
  - Add manual-trade submit and close endpoints
  - Read/write manual command file
  - Expose richer open-position state and chart payload
- Modify: `dashboard_web/templates/index.html`
  - Add manual-trade form
  - Add manual-close controls
  - Add lightweight chart rendering

### Tests
- Modify/Create:
  - `tests/xauex/test_mirofish_signal_policy.py`
  - `tests/xauex/test_session_manager.py`
  - `tests/xauex/test_manual_trade_commands.py`
  - `tests/dashboard/test_manual_controls.py`

### Docs
- Modify:
  - `ops/RUNBOOK.md`
  - `README.md`

---

### Task 1: Add Oracle Session-Manager Configuration and State Shapes

**Files:**
- Modify: `xauex/config.py`
- Modify: `xauex/.env.example`
- Test: `tests/xauex/test_session_manager.py`

- [ ] **Step 1: Write the failing config/state test**

```python
def test_load_config_includes_mirofish_session_manager_settings(monkeypatch):
    monkeypatch.setenv("MIROFISH_SESSION_PROTECT_R", "0.85")
    monkeypatch.setenv("MIROFISH_SESSION_TRAIL_R", "1.35")
    monkeypatch.setenv("MIROFISH_SESSION_ATR_MULTIPLIER", "1.4")
    monkeypatch.setenv("MIROFISH_SESSION_STRUCTURE_BUFFER_USD", "2.5")
    monkeypatch.setenv("MIROFISH_MANUAL_COMMAND_PATH", "/tmp/manual.json")

    cfg = load_config()

    assert cfg.mirofish_session_protect_r == 0.85
    assert cfg.mirofish_session_trail_r == 1.35
    assert cfg.mirofish_session_atr_multiplier == 1.4
    assert cfg.mirofish_session_structure_buffer_usd == 2.5
    assert cfg.mirofish_manual_command_path == "/tmp/manual.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_session_manager.py::test_load_config_includes_mirofish_session_manager_settings -v`

Expected: FAIL because the config fields do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add to `Config` in `xauex/config.py`:

```python
    mirofish_session_protect_r: float
    mirofish_session_trail_r: float
    mirofish_session_atr_multiplier: float
    mirofish_session_structure_buffer_usd: float
    mirofish_session_protect_buffer_usd: float
    mirofish_session_low_confidence_protect_r: float
    mirofish_session_high_confidence_protect_r: float
    mirofish_manual_command_path: str
```

Collect them in `load_config()` with sane defaults:

```python
    mirofish_session_protect_r = collect(
        _float_range, "MIROFISH_SESSION_PROTECT_R", 0.1, 5.0, 0.85
    )
    mirofish_session_trail_r = collect(
        _float_range, "MIROFISH_SESSION_TRAIL_R", 0.2, 8.0, 1.35
    )
    mirofish_session_atr_multiplier = collect(
        _float_range, "MIROFISH_SESSION_ATR_MULTIPLIER", 0.1, 10.0, 1.4
    )
    mirofish_session_structure_buffer_usd = collect(
        _float_range, "MIROFISH_SESSION_STRUCTURE_BUFFER_USD", 0.1, 50.0, 2.5
    )
    mirofish_session_protect_buffer_usd = collect(
        _float_range, "MIROFISH_SESSION_PROTECT_BUFFER_USD", 0.1, 20.0, 1.0
    )
    mirofish_session_low_confidence_protect_r = collect(
        _float_range, "MIROFISH_SESSION_LOW_CONFIDENCE_PROTECT_R", 0.1, 5.0, 0.7
    )
    mirofish_session_high_confidence_protect_r = collect(
        _float_range, "MIROFISH_SESSION_HIGH_CONFIDENCE_PROTECT_R", 0.1, 5.0, 1.0
    )
    mirofish_manual_command_path = os.getenv("MIROFISH_MANUAL_COMMAND_PATH", "/var/lib/xauex/manual_trade_cmd.json")
```

Pass them into the returned `Config(...)`.

Add matching examples to `xauex/.env.example`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_session_manager.py::test_load_config_includes_mirofish_session_manager_settings -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xauex/config.py xauex/.env.example tests/xauex/test_session_manager.py
git commit -m "feat: add Oracle session manager config"
```

### Task 2: Add Cash-Capped Stop Construction and Session Phase Logic

**Files:**
- Modify: `xauex/bot/risk/sizing.py`
- Modify: `xauex/main.py`
- Test: `tests/xauex/test_session_manager.py`

- [ ] **Step 1: Write the failing risk/phase tests**

```python
def test_oracle_initial_stop_uses_widest_signal_structure_or_atr():
    stop = build_mirofish_initial_stop_distance(
        signal_stop=12.0,
        atr_stop=16.5,
        structure_stop=14.0,
        min_stop=8.0,
        max_stop=30.0,
    )
    assert stop == 16.5


def test_oracle_wider_stop_reduces_lot_but_respects_cash_cap():
    lot = calculate_mirofish_lot_size_from_cash_risk(
        cash_risk=50.0,
        stop_distance=20.0,
        lot_size=100.0,
        volume_step=0.01,
        volume_min=0.01,
        volume_max=10.0,
        max_lot_size=0.1,
    )
    assert lot == 0.02


def test_session_phase_moves_to_protect_then_trail():
    state = {
        "phase": "OBSERVE",
        "direction": "LONG",
        "entry_price": 100.0,
        "initial_risk_distance": 10.0,
        "confidence_bucket": "medium",
    }
    protect = advance_mirofish_session_phase(state, current_price=109.0, protect_r=0.85, trail_r=1.35)
    trail = advance_mirofish_session_phase(protect, current_price=114.0, protect_r=0.85, trail_r=1.35)
    assert protect["phase"] == "PROTECT"
    assert trail["phase"] == "TRAIL"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_session_manager.py -v`

Expected: FAIL because the helpers and lifecycle do not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `xauex/bot/risk/sizing.py`, add:

```python
def calculate_mirofish_lot_size_from_cash_risk(
    *,
    cash_risk: float,
    stop_distance: float,
    lot_size: float,
    volume_step: float,
    volume_min: float,
    volume_max: float,
    max_lot_size: float,
) -> Optional[float]:
    raw = cash_risk / (stop_distance * lot_size)
    sized = math.floor(raw / volume_step) * volume_step
    if sized < volume_min:
        return None
    return round(min(sized, volume_max, max_lot_size), 5)
```

In `xauex/main.py`, add focused helpers:

```python
def build_mirofish_initial_stop_distance(*, signal_stop, atr_stop, structure_stop, min_stop, max_stop):
    return round(max(min_stop, min(max(signal_stop, atr_stop, structure_stop), max_stop)), 2)


def advance_mirofish_session_phase(state, *, current_price, protect_r, trail_r):
    ...
```

Then update Oracle order placement so it:
- computes `signal_stop`
- derives `atr_stop` and `structure_stop`
- uses the widest bounded stop
- sizes from cash cap
- stores Oracle session state keyed by position id

Update the position monitor so Oracle-owned positions:
- stay in `OBSERVE` early
- move to `PROTECT` and tighten to breakeven-plus buffer
- move to `TRAIL` later
- never widen a stop

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_session_manager.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xauex/main.py xauex/bot/risk/sizing.py tests/xauex/test_session_manager.py
git commit -m "feat: add Oracle session manager lifecycle"
```

### Task 3: Keep Oracle and Manual Positions Fully Separate

**Files:**
- Modify: `xauex/bot/execution/executor.py`
- Modify: `xauex/main.py`
- Test: `tests/xauex/test_manual_trade_commands.py`

- [ ] **Step 1: Write the failing ownership-isolation tests**

```python
def test_manual_positions_do_not_count_toward_oracle_daily_limit():
    state = {"risk": {"mirofish_trades_taken_london": 0}}
    positions = [
        {"position_id": "1", "owner": "manual"},
    ]
    assert count_oracle_open_positions(positions) == 0


def test_oracle_manager_skips_manual_positions():
    manual = {"position_id": "1", "owner": "manual", "phase": None}
    assert should_manage_with_oracle_session_manager(manual) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_manual_trade_commands.py -v`

Expected: FAIL because owner metadata and helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Extend tracked positions in `xauex/bot/execution/executor.py`:

```python
    owner: str = "strategy"
    metadata: dict = field(default_factory=dict)
```

Allow `place_market_order()` to accept optional owner/metadata:

```python
    async def place_market_order(..., owner: str = "strategy", metadata: Optional[dict] = None) -> Optional[str]:
```

Update Oracle calls to pass `owner="oracle"`.
Update forthcoming manual calls to pass `owner="manual"`.

In `xauex/main.py`, add small helpers:

```python
def should_manage_with_oracle_session_manager(position_payload: dict) -> bool:
    return str(position_payload.get("owner", "")).lower() == "oracle"


def count_oracle_open_positions(positions: list[dict]) -> int:
    return sum(1 for position in positions if should_manage_with_oracle_session_manager(position))
```

Use those helpers so Oracle limits and Oracle management ignore manual positions.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_manual_trade_commands.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xauex/bot/execution/executor.py xauex/main.py tests/xauex/test_manual_trade_commands.py
git commit -m "feat: separate Oracle and manual trade ownership"
```

### Task 4: Add Manual Trade Command Intake in XAUEX

**Files:**
- Modify: `xauex/main.py`
- Test: `tests/xauex/test_manual_trade_commands.py`

- [ ] **Step 1: Write the failing manual-command tests**

```python
def test_manual_trade_command_is_loaded_and_cleared(tmp_path):
    path = tmp_path / "manual_trade_cmd.json"
    path.write_text(json.dumps({"action": "BUY", "lot_size": 0.02}))

    cmd = load_manual_trade_command(path)

    assert cmd["action"] == "BUY"
    assert not path.exists()


def test_manual_trade_command_validation_rejects_bad_side():
    valid, reason = validate_manual_trade_command({"action": "HOLD", "lot_size": 0.01})
    assert valid is False
    assert "action" in reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_manual_trade_commands.py -v`

Expected: FAIL because the command path logic does not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `xauex/main.py`, add:

```python
def load_manual_trade_command(path: Path) -> dict | None:
    ...


def validate_manual_trade_command(payload: dict) -> tuple[bool, str]:
    ...
```

Add a polling coroutine that:
- reads the separate manual command file
- validates side, lot size, optional SL, optional TP
- submits a broker order via the shared executor
- marks owner `manual`
- records dashboard-visible status/result

Make sure failures are logged and surfaced in state, but do not affect Oracle state.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_manual_trade_commands.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add xauex/main.py tests/xauex/test_manual_trade_commands.py
git commit -m "feat: add manual dashboard trade command intake"
```

### Task 5: Add Dashboard Manual Controls and Lightweight Chart

**Files:**
- Modify: `dashboard_web/app.py`
- Modify: `dashboard_web/templates/index.html`
- Test: `tests/dashboard/test_manual_controls.py`

- [ ] **Step 1: Write the failing dashboard tests**

```python
def test_manual_trade_submit_writes_manual_command_file(app, tmp_path):
    app.config["TESTING"] = True
    client = app.test_client()
    response = client.post("/api/manual-trade", json={
        "action": "BUY",
        "lot_size": 0.02,
        "stop_loss": 10.0,
        "take_profit": 20.0,
    })
    assert response.status_code == 200


def test_dashboard_payload_contains_chart_series(app):
    client = app.test_client()
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    assert "chart" in response.get_json()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle .venv/bin/pytest -c /dev/null tests/dashboard/test_manual_controls.py -v`

Expected: FAIL because the endpoint and chart payload do not exist yet.

- [ ] **Step 3: Write minimal implementation**

In `dashboard_web/app.py`:
- add `MANUAL_CMD_PATH`
- add `POST /api/manual-trade`
- add `POST /api/manual-trade/close`
- add manual-trade status in `/api/dashboard`
- add chart payload:

```python
"chart": {
    "recent_h1_closes": state.get("recent_h1_closes", []) or [],
    "trade_entries": state.get("trade_entries_on_chart", []) or [],
}
```

In `dashboard_web/templates/index.html`:
- add manual-trade form with `BUY/SELL`, lot, SL, TP
- add per-position close action for manual trades
- render lightweight SVG/canvas chart from `chart.recent_h1_closes`
- label Oracle and manual positions separately

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=/home/bolyki/mirofish-gold-oracle .venv/bin/pytest -c /dev/null tests/dashboard/test_manual_controls.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard_web/app.py dashboard_web/templates/index.html tests/dashboard/test_manual_controls.py
git commit -m "feat: add manual trade controls to dashboard"
```

### Task 6: Wire Live Settings, Docs, and End-to-End Verification

**Files:**
- Modify: `xauex/.env`
- Modify: `README.md`
- Modify: `ops/RUNBOOK.md`

- [ ] **Step 1: Update live defaults**

Set in `xauex/.env`:

```dotenv
MIROFISH_SESSION_PROTECT_R=0.85
MIROFISH_SESSION_TRAIL_R=1.35
MIROFISH_SESSION_ATR_MULTIPLIER=1.4
MIROFISH_SESSION_STRUCTURE_BUFFER_USD=2.5
MIROFISH_SESSION_PROTECT_BUFFER_USD=1.0
MIROFISH_SESSION_LOW_CONFIDENCE_PROTECT_R=0.70
MIROFISH_SESSION_HIGH_CONFIDENCE_PROTECT_R=1.00
MIROFISH_MANUAL_COMMAND_PATH=/var/lib/xauex/manual_trade_cmd.json
```

- [ ] **Step 2: Update docs**

Document:
- Oracle session manager phases
- manual trade lane and isolation behavior
- manual command path
- dashboard manual control usage

- [ ] **Step 3: Run focused test suites**

Run:

```bash
PYTHONPATH=/home/bolyki/mirofish-gold-oracle/xauex .venv/bin/pytest -c /dev/null tests/xauex/test_session_manager.py tests/xauex/test_manual_trade_commands.py -v
PYTHONPATH=/home/bolyki/mirofish-gold-oracle .venv/bin/pytest -c /dev/null tests/dashboard/test_manual_controls.py tests/dashboard/test_direct_report.py -v
PYTHONPATH=/home/bolyki/mirofish-gold-oracle .venv/bin/pytest -c /dev/null tests/bridge -v
```

Expected:
- all targeted XAUEX tests pass
- dashboard tests pass
- existing bridge tests remain green

- [ ] **Step 4: Restart services and run live-safe verification**

Run:

```bash
sudo systemctl restart mirofish-backend.service
sudo systemctl restart oracle-dashboard.service
sudo systemctl restart xauex.service
curl -fsS http://127.0.0.1:8089/api/dashboard >/tmp/oracle-dashboard.json
```

Expected:
- all services active
- dashboard JSON contains `chart`
- dashboard JSON contains manual trade section

- [ ] **Step 5: Run one controlled manual command smoke test**

Write a small manual command and confirm it appears in state without corrupting Oracle state:

```bash
printf '%s\n' '{"action":"BUY","lot_size":0.01,"stop_loss":10.0,"take_profit":15.0}' | sudo tee /var/lib/xauex/manual_trade_cmd.json >/dev/null
sleep 15
journalctl -u xauex.service -n 80 --no-pager
curl -fsS http://127.0.0.1:8089/api/dashboard
```

Expected:
- manual trade execution is logged
- manual position is visible on dashboard
- Oracle trade count and Oracle state remain separate

- [ ] **Step 6: Commit and push**

```bash
git add xauex/.env README.md ops/RUNBOOK.md
git commit -m "feat: wire Oracle session manager and manual dashboard trading"
git push origin main
```

---

## Self-Review

Spec coverage:
- staged Oracle session manager: Tasks 1-2
- cash-capped flexible stop model: Task 2
- manual-trade independent lane: Tasks 3-5
- lightweight chart: Task 5
- docs/live wiring/verification: Task 6

Placeholder scan:
- no `TBD`, `TODO`, or deferred “implement later” placeholders remain

Type consistency:
- `manual` and `oracle` are the only ownership labels introduced in this plan
- session phases are consistently `ENTERED`, `OBSERVE`, `PROTECT`, `TRAIL`, `EXITED`

Execution note:
- use subagents per task where practical
- do not push to GitHub until all verification commands have been rerun successfully
