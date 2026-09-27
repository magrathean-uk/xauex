# XAUEX Risk State and Weighted Pattern Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep XAUEX risk accounting coherent across startup and use closed-bar pattern evidence within the existing aggregate entry-quality blocker without recreating prolonged no-trade periods.

**Architecture:** Restore `RiskState` before constructing its dependents and make weekly baseline rollover date-driven. Evaluate closed execution-timeframe candles before entry-quality scoring, pass typed pattern evidence explicitly to the aggregate policy, and persist its factors through state, event, dashboard, and alerting surfaces. A guarded reconciliation command repairs the already-corrupted live Monday state before deployment restart.

**Tech Stack:** Python 3.13, pytest, cTrader Open API, Flask dashboard state files, systemd, Monit.

---

## File Structure

- Modify: `xauex/bot/risk/gates.py` - deterministic UTC weekly-baseline rollover.
- Modify: `xauex/main.py` - risk-state construction order, identity invariant, closed-bar pattern evidence, aggregate scoring, state and event propagation.
- Modify: `xauex/shared/diagnostics.py` - critical risk-wiring issue and pattern-policy summary.
- Modify: `xauex/app/app.py` - retain policy factors in dashboard window output.
- Modify: `xauex/bot/state/writer.py` - preserve runtime invariant and daily pattern summary in state.
- Modify: `ops/monitoring/check_xauex_signal_stall.py` - alert when pattern factors contribute to all three terminal blocks for two consecutive London days.
- Modify: `ops/monitoring/45-xauex-notify.monit` - keep the existing alert program wired to the deployed script.
- Create: `ops/reconcile_xauex_risk_state.py` - guarded, dry-run-first repair of Monday risk state.
- Modify: `xauex/tests/test_session_manager.py` - pure pattern and entry-quality policy tests.
- Create: `xauex/tests/test_risk_state_wiring.py` - identity and startup regression tests.
- Modify: `xauex/tests/test_xauex_windows.py` - dashboard window evidence tests.
- Modify: `tests/test_diagnostics.py` - critical wiring diagnostics tests.
- Modify: `tests/test_xauex_signal_stall_alerts.py` - consecutive pattern-suppression alert tests.
- Create: `tests/test_reconcile_xauex_risk_state.py` - dry-run and guarded state repair tests.
- Modify: `xauex/.env.example` and `.env.example` - remove the retired strict pattern-gate flag.

### Task 1: Make weekly baseline rollover deterministic

**Files:**
- Modify: `xauex/bot/risk/gates.py:159-225`
- Test: `xauex/tests/test_risk_state_wiring.py`

- [ ] **Step 1: Write failing UTC-week tests**

```python
def test_weekly_baseline_resets_to_monday_when_started_tuesday():
    state = RiskState(week_start_date_utc="2026-07-13", week_start_balance=2931.77, weekly_pnl=-20.29)
    gates = RiskGates(SimpleNamespace(), state)
    gates.ensure_period_baselines(2947.75, now_utc=datetime(2026, 7, 21, tzinfo=timezone.utc))
    assert state.week_start_date_utc == "2026-07-20"
    assert state.week_start_balance == 2947.75
    assert state.weekly_pnl == 0.0

def test_same_week_restart_preserves_weekly_baseline():
    state = RiskState(week_start_date_utc="2026-07-20", week_start_balance=2947.75, weekly_pnl=-16.64)
    gates = RiskGates(SimpleNamespace(), state)
    gates.ensure_period_baselines(2930.93, now_utc=datetime(2026, 7, 22, tzinfo=timezone.utc))
    assert state.week_start_balance == 2947.75
    assert state.weekly_pnl == -16.64
```

- [ ] **Step 2: Run the new tests and verify failure**

Run: `.venv/bin/python -m pytest xauex/tests/test_risk_state_wiring.py -q`

Expected: FAIL because `ensure_period_baselines` does not accept deterministic time and retains stale weekly balance.

- [ ] **Step 3: Implement Monday-based rollover**

```python
def _week_start_utc(now_utc: datetime) -> str:
    current = now_utc.astimezone(timezone.utc).date()
    return (current - timedelta(days=current.weekday())).isoformat()

def ensure_period_baselines(self, balance: float, *, now_utc: datetime | None = None) -> None:
    current = now_utc or datetime.now(timezone.utc)
    week_start = _week_start_utc(current)
    if self.state.week_start_date_utc != week_start:
        self.record_week_start(balance, week_start_date_utc=week_start)
```

Update `record_week_start` to accept the supplied week-start date and retain the existing daily baseline behavior.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest xauex/tests/test_risk_state_wiring.py xauex/tests/test_session_manager.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xauex/bot/risk/gates.py xauex/tests/test_risk_state_wiring.py
git commit -m "Fix XAUEX weekly risk baseline rollover"
```

### Task 2: Restore shared risk state before executor construction

**Files:**
- Modify: `xauex/main.py:1581-1599,5155-5161`
- Test: `xauex/tests/test_risk_state_wiring.py`

- [ ] **Step 1: Write failing shared-identity tests**

```python
def test_restored_risk_state_is_shared_by_gates_and_executor():
    restored = RiskState(daily_pnl=-16.64, consecutive_losses_today=1)
    orchestrator = _orchestrator_with_restored_state(restored)
    asyncio.run(orchestrator._initialize_risk_components())
    assert orchestrator.executor.risk_gates is orchestrator.risk_gates
    assert orchestrator.executor.risk_gates.state is orchestrator.risk_state

def test_risk_wiring_invariant_rejects_replaced_gate_object():
    orchestrator = _wired_orchestrator()
    orchestrator.risk_gates = RiskGates(orchestrator.config, RiskState())
    assert orchestrator._risk_state_wiring_error() == "RISK_STATE_WIRING_INVALID"
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest xauex/tests/test_risk_state_wiring.py -q`

Expected: FAIL because startup constructs the executor before state restoration and no invariant exists.

- [ ] **Step 3: Implement one construction path and invariant**

Move `await self._restore_risk_state()` before dependent construction. Make `_restore_risk_state` replace only `self.risk_state`; construct `RiskGates` and `Executor` afterwards. Add:

```python
def _risk_state_wiring_error(self) -> str | None:
    if self.executor is None or self.risk_gates is None:
        return "RISK_STATE_WIRING_INVALID"
    if self.executor.risk_gates is not self.risk_gates:
        return "RISK_STATE_WIRING_INVALID"
    if self.risk_gates.state is not self.risk_state:
        return "RISK_STATE_WIRING_INVALID"
    if self.executor.risk_gates.state is not self.risk_state:
        return "RISK_STATE_WIRING_INVALID"
    return None
```

Call it after startup construction and before each automated entry. Startup raises a clear `RuntimeError`; runtime entry records `RISK_STATE_WIRING_INVALID`, writes state, and refuses the candidate.

- [ ] **Step 4: Run focused regression tests**

Run: `.venv/bin/python -m pytest xauex/tests/test_risk_state_wiring.py xauex/tests/test_manual_trade_commands.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xauex/main.py xauex/tests/test_risk_state_wiring.py
git commit -m "Keep XAUEX risk state wiring coherent"
```

### Task 3: Add closed-bar weighted pattern evidence

**Files:**
- Modify: `xauex/main.py:65-80,200-275,2470-2695,4140-4765`
- Modify: `xauex/.env.example:121-126`
- Modify: `.env.example`
- Test: `xauex/tests/test_session_manager.py`

- [ ] **Step 1: Write failing pattern-policy tests**

```python
def test_current_execution_bar_is_excluded_from_pattern_pair():
    bars = [_bar("2026-07-20T07:50:00Z"), _bar("2026-07-20T07:55:00Z"), _bar("2026-07-20T08:00:00Z")]
    previous, signal = _MODULE.select_closed_execution_pattern_bars(
        bars, now_utc=datetime(2026, 7, 20, 8, 2, tzinfo=timezone.utc), timeframe="M5"
    )
    assert signal["open_time"] == datetime(2026, 7, 20, 7, 55, tzinfo=timezone.utc)

def test_missing_pattern_reduces_risk_without_blocking_alone():
    decision = build_xauex_entry_quality_decision(..., pattern_evidence={"factor": "PATTERN_MISSING", "weight": 2})
    assert decision["allowed"] is True
    assert decision["risk_multiplier"] == 0.25

def test_missing_pattern_and_stale_low_confidence_uses_existing_hard_blocker():
    decision = build_xauex_entry_quality_decision(..., pattern_evidence={"factor": "PATTERN_MISSING", "weight": 2})
    assert decision["reason"] == "HARD_BLOCKER"
    assert decision["policy_factors"] == ["PATTERN_MISSING", "STALE_CONTEXT_LOW_CONFIDENCE"]

def test_opposite_pattern_reaches_hard_blocker_alone():
    decision = build_xauex_entry_quality_decision(..., pattern_evidence={"factor": "PATTERN_DIRECTION_MISMATCH", "weight": 3})
    assert decision["reason"] == "HARD_BLOCKER"
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest xauex/tests/test_session_manager.py -q`

Expected: FAIL because no closed-bar selector or explicit pattern evidence argument exists.

- [ ] **Step 3: Implement typed evidence and aggregate scoring**

Add a small `XauexPatternEvidence` dataclass in `xauex/main.py` with `factor`, `weight`, `pattern`, `level`, `timeframe`, `previous_open_time_utc`, and `signal_open_time_utc` fields. Add weights:

```python
"PATTERN_MISSING": 2,
"PATTERN_DIRECTION_MISMATCH": 3,
"PATTERN_DATA_UNAVAILABLE": 1,
```

Implement `select_closed_execution_pattern_bars` using the active timeframe duration and `open_time + duration <= now_utc`. Evaluate the existing detector against nearby HTF levels. Treat `INSIDE_BAR` as `PATTERN_MISSING`, not directional confirmation. Pass `pattern_evidence` explicitly into `build_xauex_entry_quality_decision`, add its factor once, and retain the existing threshold of 3.

Retire the old `XAUEX_REQUIRE_PATTERN_MATCH` branch instead of retaining a strict second terminal gate. Populate `placement_pattern` and `placement_level` only for a directional match.

- [ ] **Step 4: Run policy and window tests**

Run: `.venv/bin/python -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_xauex_windows.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xauex/main.py xauex/.env.example .env.example xauex/tests/test_session_manager.py
git commit -m "Add weighted XAUEX pattern evidence"
```

### Task 4: Persist and surface policy evidence

**Files:**
- Modify: `xauex/main.py:2990-3075,4140-4235,4650-4770`
- Modify: `xauex/bot/state/writer.py:160-220`
- Modify: `xauex/shared/diagnostics.py:300-430`
- Modify: `xauex/app/app.py:520-585`
- Test: `xauex/tests/test_xauex_windows.py`
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write failing visibility tests**

```python
def test_terminal_hard_block_records_policy_factors_in_window_state():
    orch._mark_slot_used(..., reason="HARD_BLOCKER", policy_factors=["PATTERN_MISSING", "STALE_CONTEXT_LOW_CONFIDENCE"], hard_block_score=3)
    run = orch.risk_state.xauex_signal_runs_london[-1]
    assert run["policy_factors"] == ["PATTERN_MISSING", "STALE_CONTEXT_LOW_CONFIDENCE"]
    assert run["hard_block_score"] == 3

def test_diagnostics_marks_invalid_risk_wiring_critical():
    snapshot = build_diagnostics_snapshot({"runtime": {"risk_state_wiring": {"valid": False}}})
    assert snapshot["overall_status"] == "blocked"
    assert snapshot["current_issues"][0]["code"] == "RISK_STATE_WIRING_INVALID"
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest xauex/tests/test_xauex_windows.py tests/test_diagnostics.py -q`

Expected: FAIL because slot records and diagnostics do not retain these fields.

- [ ] **Step 3: Implement state, event, dashboard, and diagnostics propagation**

Extend `_record_xauex_signal_run` and `_mark_slot_used` with optional `policy_factors`, `hard_block_score`, and `pattern_evidence`. Include them in the `risk_result` and `blocked_trade_candidate` event payloads. Include a `risk_state_wiring` object and current-day pattern counters in the runtime state written by `write_state`.

In diagnostics, append this critical issue when invalid:

```python
_issue(
    component="risk",
    severity="critical",
    code="RISK_STATE_WIRING_INVALID",
    summary="XAUEX risk-state wiring is invalid.",
    details="Risk limits cannot be trusted until the bot is restarted with a coherent state graph.",
    next_action="Restart XAUEX and inspect risk-state restoration logs.",
)
```

Preserve `policy_factors`, `hard_block_score`, and `pattern_evidence` in each dashboard window item.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/python -m pytest xauex/tests/test_xauex_windows.py tests/test_diagnostics.py tests/dashboard/test_direct_report.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add xauex/main.py xauex/bot/state/writer.py xauex/shared/diagnostics.py xauex/app/app.py xauex/tests/test_xauex_windows.py tests/test_diagnostics.py
git commit -m "Expose XAUEX policy and risk wiring evidence"
```

### Task 5: Alert on repeated pattern-caused suppression

**Files:**
- Modify: `ops/monitoring/check_xauex_signal_stall.py`
- Modify: `ops/monitoring/45-xauex-notify.monit`
- Test: `tests/test_xauex_signal_stall_alerts.py`

- [ ] **Step 1: Write a failing two-day alert test**

```python
def test_signal_stall_alerts_when_pattern_factors_block_all_windows_for_two_days(tmp_path):
    events = _pattern_hard_block_events_for_days("2026-07-20", "2026-07-21")
    journal_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    rc = MODULE.run_once(..., now=datetime(2026, 7, 21, 13, 0, tzinfo=timezone.utc))
    assert rc == 0
    assert "pattern policy suppressed all 3 windows" in mail_capture.read_text(encoding="utf-8").lower()
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_xauex_signal_stall_alerts.py -q`

Expected: FAIL because the monitor ignores `risk_result.policy_factors`.

- [ ] **Step 3: Implement deduplicated alert detection**

Read `risk_result` events, group terminal `HARD_BLOCKER` entries by London date and expected slots, and count only entries whose `policy_factors` contain a `PATTERN_` item. When all three slots are present on two consecutive London trading days, add a `pattern-suppression` `SignalAlert` with a London-date alert key. Reuse the script's existing sendmail and sent-key persistence. Keep exit status zero after successfully sending, so Monit does not create a duplicate generic failure alert.

- [ ] **Step 4: Run focused monitoring tests**

Run: `.venv/bin/python -m pytest tests/test_xauex_signal_stall_alerts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/monitoring/check_xauex_signal_stall.py ops/monitoring/45-xauex-notify.monit tests/test_xauex_signal_stall_alerts.py
git commit -m "Alert on XAUEX pattern suppression"
```

### Task 6: Repair current live state and deploy safely

**Files:**
- Create: `ops/reconcile_xauex_risk_state.py`
- Test: `tests/test_reconcile_xauex_risk_state.py`

- [ ] **Step 1: Write failing guarded-repair tests**

```python
def test_plan_repair_uses_monday_day_baseline_and_closed_xauex_losses(tmp_path):
    plan = MODULE.plan_repair(state_payload=_monday_state(), risk_payload=_broken_risk(), now_utc=datetime(2026, 7, 20, 13, tzinfo=timezone.utc))
    assert plan["risk"]["daily_pnl"] == -16.64
    assert plan["risk"]["weekly_pnl"] == -16.64
    assert plan["risk"]["consecutive_losses_today"] == 1
    assert plan["risk"]["week_start_balance"] == 2947.75

def test_plan_repair_refuses_non_monday_or_missing_day_baseline():
    with pytest.raises(ValueError, match="Monday"):
        MODULE.plan_repair(..., now_utc=datetime(2026, 7, 21, tzinfo=timezone.utc))
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv/bin/python -m pytest tests/test_reconcile_xauex_risk_state.py -q`

Expected: FAIL because the guarded repair tool does not exist.

- [ ] **Step 3: Implement dry-run-first repair**

Implement `plan_repair` as a pure function. It requires Monday UTC and matching `risk.day_start_date_utc`, sums XAUEX-owned closed trades for that UTC day, and returns a replacement risk payload. CLI defaults to printing JSON; `--apply` creates a timestamped sibling backup, atomically writes mode `0600`, and prints only non-sensitive counts and PnL.

- [ ] **Step 4: Run tool tests and dry run**

Run: `.venv/bin/python -m pytest tests/test_reconcile_xauex_risk_state.py -q`

Run: `.venv/bin/python ops/reconcile_xauex_risk_state.py --state /var/lib/xauex/state.json --risk /var/lib/xauex/risk_state.json`

Expected: PASS; dry run proposes daily and weekly gross PnL of `-16.64` and one consecutive loss without writing files.

- [ ] **Step 5: Run full verification before deploy**

Run: `.venv/bin/python -m pytest -q`

Run: `make -C xauex lint`

Expected: all tests pass and Ruff/mypy report no issues.

- [ ] **Step 6: Apply repair, deploy, and verify live state**

Run the repair with `--apply`, install the monitoring script and Monit file through the existing XAUEX deployment path, restart `xauex.service`, reload Monit, then verify:

```bash
systemctl is-active xauex.service xauex-web.service
systemctl --failed --no-pager
sudo -n monit summary | rg 'xauex|systemd-failed|Service Name|Status'
curl -fsS http://127.0.0.1:8089/api/diagnostics | jq '.data'
jq '.risk | {daily_pnl,weekly_pnl,consecutive_losses_today,week_start_balance,week_start_date_utc}' /var/lib/xauex/state.json
```

Expected: both services active, no failed units, XAUEX Monit checks OK, risk state retains the repaired loss, and no order is placed by deployment.

- [ ] **Step 7: Commit and push only after live verification**

```bash
git add ops/reconcile_xauex_risk_state.py tests/test_reconcile_xauex_risk_state.py
git commit -m "Add guarded XAUEX risk state repair"
git push origin main
```

## Plan Self-Review

- Spec coverage: Tasks 1-2 cover shared risk state, Monday rollover, and invariant. Task 3 covers the weighted closed-bar pattern policy and retirement of the strict gate. Task 4 covers state, event, dashboard, and diagnostics evidence. Task 5 covers the two-day suppression alert. Task 6 covers guarded live repair, deployment, tests, and push.
- Placeholder scan: all test names, target files, commands, and required behavior are specified. No unresolved implementation choices remain.
- Type consistency: pattern evidence uses one explicit `XauexPatternEvidence` structure passed to entry-quality scoring and propagated as dictionaries only at state/event boundaries. Weekly rollover accepts injected UTC time only for deterministic tests and preserves existing production call sites.
