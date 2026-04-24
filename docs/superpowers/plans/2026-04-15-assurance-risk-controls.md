# Assurance Risk Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the analyst-debate runner shadow-only for one week while making live XAUEX risk, target, and protection behavior adaptive to signal assurance.

**Architecture:** The signal generator remains unchanged for live trading and the analyst-debate runner stays in `/var/lib/xauex/shadow_trials` only. The live bot computes an assurance profile from the baseline signal's confidence, validator state, consensus state, and input freshness, then uses that profile to decide trade eligibility, risk budget, target RR, and protected-profit locking.

**Tech Stack:** Python 3, pytest, systemd timers, cTrader Open API runtime, Flask dashboard.

---

### Task 1: Regression Tests

**Files:**
- Modify: `xauex/tests/test_session_manager.py`
- Modify: `tests/test_daily_report.py`
- Modify: `tests/bridge/test_shadow_trial.py`

- [x] **Step 1: Add failing assurance and protection tests**

Add tests for low-confidence validator disagreement, high-assurance target expansion, R-based protect stop pricing, shadow timer monitoring, and one-week shadow report lookback.

- [x] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m pytest xauex/tests/test_session_manager.py::test_assurance_profile_blocks_low_confidence_validator_disagreement xauex/tests/test_session_manager.py::test_assurance_profile_allows_aligned_high_confidence_with_larger_target xauex/tests/test_session_manager.py::test_take_profit_distance_expands_with_assurance_target xauex/tests/test_session_manager.py::test_protect_stop_locks_profit_in_r_not_fixed_one_dollar -q
python3 -m pytest tests/test_daily_report.py::test_daily_report_monitors_shadow_trial_timers tests/bridge/test_shadow_trial.py::test_shadow_trial_report_defaults_to_one_week_lookback -q
```

Expected: failures because the helpers and timer/report behavior are not implemented yet.

### Task 2: Assurance Runtime

**Files:**
- Modify: `xauex/main.py`
- Modify: `xauex/config.py`
- Modify: `.env.example`
- Modify: `xauex/.env.example`
- Modify: `xauex/.env`

- [x] **Step 1: Add assurance profile helpers**

Create a small `XauexAssuranceProfile` dataclass and helpers in `xauex/main.py`:

```python
build_xauex_assurance_profile(signal, config)
build_xauex_take_profit_distance(signal_take_profit, stop_distance, assurance)
build_xauex_protect_stop_price(direction, entry_price, initial_risk_distance, lock_r, min_buffer_usd)
```

- [x] **Step 2: Apply profile in live signal execution**

Use the profile before lot sizing. If `allow_trade` is false, mark the slot as blocked. Size from `max_cash_risk * profile.risk_multiplier` instead of sizing first and scaling the lot later.

- [x] **Step 3: Apply adaptive target and R-based protection**

Set TP from `max(signal_tp_distance, stop_distance * profile.target_rr)`. Store the assurance profile and actual cash risk in session metadata. On protect transition, lock `initial_risk_distance * protect_lock_r` instead of fixed `$1`.

- [x] **Step 4: Keep 1% cap as the canonical max risk**

Set examples and live host env to `XAUEX_RISK_CAP_PERCENT=1.0`.

### Task 3: Shadow Reporting

**Files:**
- Modify: `xauex/signal/shadow_trial.py`
- Modify: `ops/xauex-shadow-report.timer`
- Modify: `ops/run_xauex_daily_report.py`
- Modify: `ops/RUNBOOK.md`
- Modify: `docs/REBUILD.md`

- [x] **Step 1: Keep second runner shadow-only**

No live trading path reads analyst-debate outputs.

- [x] **Step 2: Extend analysis window**

Set shadow report lookback to 7 days, schedule the weekly report after one week of shadow data, and skip email until at least 6 days of completed shadow history exists.

- [x] **Step 3: Include shadow timers in daily report**

- [x] **Step 4: Avoid false failed daily reports after the final signal window**

Downgrade `SIGNAL_STALE` to a warning when the daily signal-run quota has already been completed, while preserving hard failures for disconnected bot/API health and genuinely missed signal runs.

Add shadow compare, evaluate, and report timers to the daily service health list.

### Task 4: Verification

**Files:**
- Test only.

- [x] **Step 1: Run targeted tests**

```bash
python3 -m pytest xauex/tests/test_session_manager.py xauex/tests/test_xauex_signal_policy.py xauex/tests/test_trailing_integration.py tests/test_daily_report.py tests/bridge/test_shadow_trial.py tests/bridge/test_compare_runner.py -q
```

- [x] **Step 2: Compile touched Python files**

```bash
python3 -m py_compile xauex/main.py xauex/config.py xauex/signal/shadow_trial.py ops/run_xauex_daily_report.py
```

- [x] **Step 3: Reinstall systemd units and restart safe surfaces**

```bash
sudo bash ops/install_systemd.sh
sudo systemctl restart xauex.service
sudo systemctl restart xauex-web.service
systemctl --failed --no-pager
```
