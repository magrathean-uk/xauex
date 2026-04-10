# Oracle Live Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live Oracle trading/runtime path internally consistent, rebuild-safe, and operator-visible without changing the core direct-prediction strategy.

**Architecture:** Keep the existing `bridge -> cmd.json -> xauex -> dashboard` production path. Fix scheduling/recovery in `ops`, unify signal truth in `dashboard_web` and diagnostics, harden dry-run behavior in `bridge`, and reconcile deployment/docs/config drift so the live setup can be rebuilt without machine-local tribal knowledge.

**Tech Stack:** Python 3.11+, Flask/Waitress, asyncio XAUEX bot, systemd timers/services, pytest, shell ops scripts.

---

## File Structure

**Primary files to modify**
- `ops/xauex-start.timer`
- `ops/xauex-stop.timer`
- `ops/xauex-stop.service`
- `ops/xauex-trade-journal.timer`
- `ops/xauex-weekly-review.timer`
- `ops/oracle-dashboard.service`
- `ops/run_oracle_dashboard.sh`
- `ops/install_systemd.sh`
- `ops/RUNBOOK.md`
- `README.md`
- `docs/REBUILD.md`
- `.env.example`
- `xauex/.env.example`
- `xauex/config.py`
- `dashboard_web/app.py`
- `dashboard_web/templates/index.html`
- `diagnostics.py`
- `status.sh`
- `bridge/run.py`
- `tests/dashboard/test_manual_controls.py`
- `tests/dashboard/test_direct_report.py`
- `tests/test_diagnostics.py`
- `xauex/tests/test_mirofish_windows.py`
- `xauex/tests/test_api_client_auth.py`

**Responsibilities**
- `ops/*`: service lifecycle, scheduling, and deployment portability
- `dashboard_web/*` + `diagnostics.py`: coherent operator truth model
- `bridge/run.py`: non-destructive dry-run semantics
- `*.example` + docs: rebuild-safe config and documentation alignment
- tests: regression coverage for the fixes above

---

### Task 1: Fix Service Scheduling And Recovery

**Files:**
- Modify: `ops/xauex-start.timer`
- Modify: `ops/xauex-stop.timer`
- Modify: `ops/xauex-stop.service`
- Modify: `ops/xauex-trade-journal.timer`
- Modify: `ops/xauex-weekly-review.timer`
- Modify: `ops/oracle-dashboard.service`
- Modify: `ops/run_oracle_dashboard.sh`
- Modify: `ops/install_systemd.sh`
- Test: `xauex/tests/test_mirofish_windows.py`

- [ ] **Step 1: Write the failing tests and schedule assertions**

Add/update tests in `xauex/tests/test_mirofish_windows.py` for end-of-session expectations that match the intended London runtime.

```python
def test_stale_previous_slot_signal_does_not_consume_midday_slot(...):
    ...

def test_same_slot_stale_signal_still_consumes_slot(...):
    ...
```

Then add a lightweight shell-verified checklist in comments or docstrings for systemd expectations:
- bot not stopped before Oracle close boundary
- journal/review after close boundary
- timer behavior safe after restart

- [ ] **Step 2: Run the focused test file to establish baseline**

Run:

```bash
source .venv/bin/activate
pytest -q xauex/tests/test_mirofish_windows.py
```

Expected: pass/fail output recorded before service-file edits; this establishes current runtime guard coverage.

- [ ] **Step 3: Implement schedule/service hardening**

Update service/timer files so:
- Friday stop aligns with actual close/force-flat policy
- journal/review run after the stop/flat boundary
- dashboard service is rebuild-portable and not hardcoded to the local username
- installer deploys the updated units consistently

Minimal code examples:

```ini
[Timer]
OnCalendar=Fri *-*-* 15:06:00 Europe/London
Persistent=true
```

```ini
[Service]
User=__RUN_USER__
EnvironmentFile=__REPO_ROOT__/.env
EnvironmentFile=__REPO_ROOT__/xauex/.env
```

- [ ] **Step 4: Run targeted verification**

Run:

```bash
systemd-analyze verify ops/xauex-start.timer ops/xauex-stop.timer ops/xauex-stop.service ops/xauex-trade-journal.timer ops/xauex-weekly-review.timer ops/oracle-dashboard.service
source .venv/bin/activate
pytest -q xauex/tests/test_mirofish_windows.py
```

Expected:
- `systemd-analyze verify` exits `0`
- timer regression tests pass

---

### Task 2: Unify Dashboard And Diagnostics Truth

**Files:**
- Modify: `dashboard_web/app.py`
- Modify: `diagnostics.py`
- Modify: `status.sh`
- Modify: `dashboard_web/templates/index.html`
- Test: `tests/dashboard/test_manual_controls.py`
- Test: `tests/dashboard/test_direct_report.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing dashboard/diagnostics regression tests**

Add tests asserting:
- top-level dashboard signal and diagnostics signal agree
- run-slot counts match between account and diagnostics
- helper/status output no longer depends on stale simulation status or `logs/run.log`

Example test shape:

```python
def test_dashboard_diagnostics_reuse_normalized_oracle_signal(client, monkeypatch, tmp_path):
    ...
    assert payload["signal"]["action"] == "SELL"
    assert payload["diagnostics"]["components"]["signal"]["action"] == "SELL"
```

- [ ] **Step 2: Run the focused dashboard/diagnostics tests to verify RED**

Run:

```bash
source .venv/bin/activate
pytest -q tests/dashboard/test_manual_controls.py tests/dashboard/test_direct_report.py tests/test_diagnostics.py
```

Expected: at least one failure capturing the current inconsistency or missing behavior.

- [ ] **Step 3: Implement one Oracle-normalized signal path**

Refactor `dashboard_web/app.py` and `diagnostics.py` so diagnostics accept the normalized signal already extracted from `cmd.json` instead of inferring it from strategy history.

Minimal implementation direction:

```python
signal = _extract_signal(cmd)
diagnostics = build_diagnostics_snapshot(state, oracle_signal=signal)
```

```python
def build_diagnostics_snapshot(state, *, oracle_signal=None, reference_time=None):
    last_signal = _safe_dict(oracle_signal) or ...
```

Also align signal-run counting semantics to use the same rule in both places.

- [ ] **Step 4: Simplify the repo status helper**

Update `status.sh` so it reports:
- live services
- latest Oracle signal
- latest XAUEX health
- recent Oracle logs

Remove stale coupling to:
- latest simulation upload directory
- `/home/bolyki/mirofish-gold-oracle/logs/run.log`

- [ ] **Step 5: Run targeted verification**

Run:

```bash
source .venv/bin/activate
pytest -q tests/dashboard/test_manual_controls.py tests/dashboard/test_direct_report.py tests/test_diagnostics.py
curl -s http://127.0.0.1:8089/api/dashboard | python3 -m json.tool | sed -n '1,220p'
bash status.sh
```

Expected:
- focused tests pass
- dashboard JSON shows matching top-level and diagnostics signal state
- status helper output is live-runtime relevant

---

### Task 3: Make Bridge Dry-Run Non-Destructive

**Files:**
- Modify: `bridge/run.py`
- Test: `tests/bridge/test_evidence_writer.py`
- Create or Modify: `tests/bridge/test_run_mode_selection.py`

- [ ] **Step 1: Write the failing dry-run regression test**

Add a test that prepares existing brief/evidence files, runs the dry-run code path, and asserts those files remain unchanged unless explicit override paths are provided.

```python
def test_dry_run_does_not_overwrite_live_brief_or_evidence(tmp_path, monkeypatch):
    ...
```

- [ ] **Step 2: Run the focused bridge tests to verify RED**

Run:

```bash
source .venv/bin/activate
pytest -q tests/bridge/test_run_mode_selection.py tests/bridge/test_evidence_writer.py
```

Expected: failure showing that dry-run still mutates live artifacts.

- [ ] **Step 3: Implement minimal dry-run guard**

Adjust `bridge/run.py` so the dry-run early-return happens before any default brief/evidence writes, while still allowing explicit non-live output paths if the CLI later grows that option.

Minimal code direction:

```python
if args.dry_run:
    logger.info("DRY RUN - signal/brief/evidence not written")
    return
```

Place this before the brief/evidence write path, or route dry-run writes to isolated temporary/explicit outputs.

- [ ] **Step 4: Run targeted verification**

Run:

```bash
source .venv/bin/activate
pytest -q tests/bridge/test_run_mode_selection.py tests/bridge/test_evidence_writer.py
```

Expected: both tests pass.

---

### Task 4: Reconcile Config And Documentation Drift

**Files:**
- Modify: `.env.example`
- Modify: `xauex/.env.example`
- Modify: `xauex/config.py`
- Modify: `README.md`
- Modify: `docs/REBUILD.md`
- Modify: `ops/RUNBOOK.md`
- Test: `xauex/tests/test_api_client_auth.py`

- [ ] **Step 1: Write the failing config/example regression tests**

Add or extend tests to cover the documented cTrader TLS server-name path and any config parsing needed by the live deployment.

```python
def test_connect_passes_tls_server_name_override_to_transport(...):
    ...
```

- [ ] **Step 2: Run the focused auth/config tests to verify baseline**

Run:

```bash
source .venv/bin/activate
pytest -q xauex/tests/test_api_client_auth.py
```

Expected: current baseline captured before doc/example updates.

- [ ] **Step 3: Update example configs and docs**

Reconcile:
- cTrader host/TLS example config
- force-flat/session timing values
- Python version statement
- dashboard/network access assumptions
- analyst/Gemini dependency notes

Minimal doc/config snippets:

```dotenv
CTRADER_HOST=demo-uk-eqx-01.p.c-trader.com
CTRADER_TLS_SERVER_NAME=connect.spotware.com
```

```markdown
Access is restricted by VPN/localhost policy and should be verified explicitly after install.
```

- [ ] **Step 4: Run targeted verification**

Run:

```bash
source .venv/bin/activate
pytest -q xauex/tests/test_api_client_auth.py
python -m py_compile $(rg --files -g '*.py')
```

Expected: tests and compilation pass after config/doc updates.

---

### Task 5: Integrate And Verify End To End

**Files:**
- Verify across all modified files

- [ ] **Step 1: Run full automated verification**

Run:

```bash
source .venv/bin/activate
pytest -q
python -m py_compile $(rg --files -g '*.py')
```

Expected:
- `pytest -q` passes
- compilation exits `0`

- [ ] **Step 2: Run live runtime smoke verification**

Run:

```bash
systemctl --no-pager --full status mirofish-backend.service oracle-dashboard.service xauex.service mirofish-bridge.timer xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-weekly-review.timer
curl -s http://127.0.0.1:8051/health
curl -s http://127.0.0.1:8089/api/dashboard | python3 -m json.tool | sed -n '1,220p'
bash status.sh
```

Expected:
- services/timers active and coherent
- XAUEX health reports running/connected or accurately degraded
- dashboard payload is internally consistent
- status helper reflects the live Oracle runtime

- [ ] **Step 3: Reconcile spec coverage**

Check this plan against `docs/superpowers/specs/2026-04-09-oracle-live-hardening-design.md` and confirm:
- schedule/session consistency covered by Task 1
- dashboard truth covered by Task 2
- dry-run behavior covered by Task 3
- rebuild/config drift covered by Task 4
- end-to-end runtime verification covered by Task 5

- [ ] **Step 4: Close out**

Prepare a concise final report summarizing:
- what changed
- which issues were fixed
- what verification was run
- any remaining intentional follow-up items
