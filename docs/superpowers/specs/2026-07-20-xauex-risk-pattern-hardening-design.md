# XAUEX Risk State and Weighted Pattern Design

Date: 2026-07-20
Status: Draft for written review

## Context

The live XAUEX process closed a losing trade while logging the correct loss, but the persisted risk state and dashboard continued to report zero daily PnL, zero weekly PnL, and zero consecutive losses. Startup constructs an `Executor` with one `RiskGates` instance, then risk restoration replaces the orchestrator's `RiskGates` instance. The executor updates the abandoned instance when a trade closes.

The weekly baseline also rolls its date and PnL forward without taking a new balance snapshot. If the service misses Monday and starts later in the week, the weekly reset may not happen at all.

Recent XAUEX trades were recorded with `pattern=NONE`. A strict, feature-flagged H1 pattern gate exists but is disabled. Enabling it unchanged would create a second independent blocker and risks returning the bot to prolonged no-trade periods.

## Goals

- Keep one authoritative `RiskState` shared by the orchestrator, `RiskGates`, and `Executor`.
- Reset the weekly baseline to the correct UTC Monday and current balance, including delayed startup after Monday.
- Detect risk-state wiring failures immediately and expose them as critical runtime health failures.
- Evaluate price-action patterns on fully closed bars before entry-quality scoring.
- Feed pattern quality into the existing aggregate `HARD_BLOCKER` score.
- Ensure missing pattern confirmation never blocks a trade by itself.
- Record enough pattern evidence to explain both permitted and blocked decisions.

## Non-Goals

- Do not change gross realized PnL to net PnL accounting.
- Do not add another standalone market-entry blocker.
- Do not require every trade to have a named candlestick pattern.
- Do not change LLM models, signal schedules, position sizing formulas, or broker credentials.
- Do not automatically increase risk when a pattern matches.

## Startup and Risk State

Startup will restore the persisted `RiskState` before constructing `RiskGates` and `Executor`. Construction will then happen once, in this order:

1. Load or initialize `RiskState`.
2. Construct `RiskGates(config, risk_state)`.
3. Construct `Executor(..., risk_gates=risk_gates)`.
4. Establish daily and weekly baselines.
5. Validate object identity before accepting entries.

The invariant is:

```text
orchestrator.risk_gates is executor.risk_gates
orchestrator.risk_state is orchestrator.risk_gates.state
executor.risk_gates.state is orchestrator.risk_state
```

Startup must fail if this invariant is false. The runtime health snapshot must also report a critical `RISK_STATE_WIRING_INVALID` issue and refuse automated entries if the invariant later becomes false. This is an operational integrity failure, not an additional market-quality blocker.

## Weekly Baseline

Weekly rollover will calculate the current UTC week's Monday. If the persisted `week_start_date_utc` does not equal that Monday, `record_week_start` will atomically set:

- `week_start_date_utc` to that Monday;
- `week_start_balance` to the current broker balance;
- `weekly_pnl` to zero;
- `weekly_halted` to false.

This works on Monday and on any later weekday after downtime. Daily rollover behavior remains unchanged except for tests proving it continues to preserve an existing same-day baseline across restarts.

## Weighted Pattern Evidence

Pattern evaluation will run once for each directional XAUEX candidate before `build_xauex_entry_quality_decision`. The resulting structured `pattern_evidence` will be passed explicitly to the entry-quality function; it will not be hidden in or inferred from LLM text.

The evaluator will use the latest two fully closed bars from the active execution timeframe, currently M5, and the existing HTF levels. It must explicitly exclude a currently forming bar. The existing `PatternDetector` remains the classifier.

Pattern results contribute to the existing entry-quality score:

| Result | Factor | Weight | Effect by itself |
| --- | --- | ---: | --- |
| Directional match | none | 0 | Permit normal evaluation and record the pattern |
| No directional pattern | `PATTERN_MISSING` | 2 | Reduce risk, never block alone |
| Neutral inside bar | `PATTERN_MISSING` | 2 | Reduce risk, never treat as directional proof |
| Opposite directional pattern | `PATTERN_DIRECTION_MISMATCH` | 3 | Reach the existing hard-block threshold |
| Bars, levels, or detector unavailable after retry | `PATTERN_DATA_UNAVAILABLE` | 1 | Warning only, never block alone |

The existing hard-block threshold remains 3. Examples:

- `PATTERN_MISSING` alone scores 2 and remains eligible at reduced risk.
- `PATTERN_MISSING` plus `STALE_CONTEXT_LOW_CONFIDENCE` scores 3 and blocks.
- `PATTERN_DIRECTION_MISMATCH` scores 3 and blocks because current price action directly contradicts the proposed direction.
- `PATTERN_DATA_UNAVAILABLE` alone scores 1 and remains eligible at reduced risk.

The separate `XAUEX_REQUIRE_PATTERN_MATCH` enforcement branch will be retired from the live entry path. Pattern evidence will have one route through the aggregate entry-quality decision.

## Trade and Decision Evidence

Every evaluated candidate will record:

- evaluation timeframe;
- previous and signal bar open times;
- detected pattern;
- matched HTF level;
- pattern direction;
- result factor and weight;
- final aggregate hard-block score.

When a matched pattern is allowed to trade, that `PatternType` and matched level will be passed to the executor and persisted on the position and closed-trade journal. Allowed no-pattern trades remain `pattern=NONE`, which is accurate evidence rather than missing instrumentation.

The event journal and dashboard window outcome must include the policy factors behind `HARD_BLOCKER`, not only the generic label.

## Anti-Overblocking Guardrails

- Missing pattern confirmation cannot block by itself.
- Pattern data failure cannot block by itself.
- Only an explicit opposite-direction pattern can contribute the full threshold alone.
- Pattern scoring cannot create a second terminal gate outside the existing aggregate decision.
- The dashboard will count pattern matches, missing patterns, mismatches, and pattern-contributed hard blocks by trading day.
- If pattern factors contribute to all three terminal window blocks on two consecutive trading days, Monit will send an actionable alert. The policy will not silently disable itself.
- The existing one-per-day minimum-lot canary behavior remains unchanged for candidates that survive the aggregate blocker.

## Deployment and State Repair

Before restarting the live service, the deployment will back up `/var/lib/xauex/risk_state.json` and reconcile the current day's persisted gross PnL and consecutive-loss count from the authoritative closed-trade records already present in `/var/lib/xauex/state.json`. Because this repair is being deployed on Monday, `week_start_balance` will be copied from Monday's preserved `day_start_balance`, and `week_start_date_utc` will be set to the same UTC date. If those source fields no longer identify Monday at deployment time, the repair will stop for manual review instead of guessing a historical balance.

The service will then restart once. Verification will confirm:

- live broker authentication and quote updates;
- risk-state object identity through the new health evidence;
- preserved daily loss and consecutive-loss values;
- correct UTC Monday baseline;
- successful dashboard and Monit checks;
- no order is triggered merely by deployment.

## Testing

Targeted tests will cover:

- restored state is shared by orchestrator, gates, and executor;
- closing a trade updates the same state later persisted and displayed;
- startup and runtime invariant failures are critical and block automation;
- Monday rollover refreshes both date and balance;
- Tuesday startup after downtime still records Monday as the week start;
- same-day restart preserves the existing daily baseline;
- currently forming bars are excluded from pattern evaluation;
- pattern match, missing, neutral inside bar, direction mismatch, and unavailable-data weights;
- missing pattern alone remains allowed at reduced risk;
- missing pattern plus an independent warning reaches `HARD_BLOCKER`;
- a matched pattern is attached to the placed order and journal metadata;
- event and dashboard evidence expose contributing policy factors.

After targeted tests, run the full pytest suite and `make -C xauex lint`.

## Acceptance Criteria

- A live trade close changes the persisted risk PnL and consecutive-loss state used by subsequent gate checks.
- Weekly baseline date and balance correspond to the current UTC week.
- No automated entry is possible while risk-state wiring is invalid.
- A missing pattern never blocks a trade without at least one independent risk factor.
- An allowed matched-pattern trade is no longer journalled as `NONE`.
- Operators can see why the aggregate hard blocker fired.
- Existing services, signal timers, dashboard, and Monit checks remain operational after deployment.
