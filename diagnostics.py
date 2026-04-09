from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def count_london_signal_runs(risk: Mapping[str, Any], runtime: Mapping[str, Any] | None = None) -> int:
    runtime_dict = _safe_dict(runtime)
    signal_runs = _safe_int(runtime_dict.get("mirofish_signal_runs_taken_london"))
    runtime_signal_runs = runtime_dict.get("mirofish_signal_runs_london")
    if isinstance(runtime_signal_runs, list):
        signal_runs = len([item for item in runtime_signal_runs if bool(item.get("terminal", True))])

    risk_signal_runs = risk.get("mirofish_signal_runs_london")
    if isinstance(risk_signal_runs, list):
        signal_runs = len([item for item in risk_signal_runs if bool(item.get("terminal", True))])
    elif signal_runs == 0:
        signal_runs = _safe_int(risk.get("mirofish_signal_runs_taken_london"))

    return signal_runs


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_seconds(value: Any, reference_time: datetime | None = None) -> int | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    now = reference_time or datetime.now(timezone.utc)
    return max(0, int((now - dt.astimezone(timezone.utc)).total_seconds()))


def _issue(
    *,
    component: str,
    severity: str,
    code: str,
    summary: str,
    details: str = "",
    evidence: Mapping[str, Any] | None = None,
    next_action: str = "",
) -> dict[str, Any]:
    return {
        "component": component,
        "severity": severity,
        "code": code,
        "summary": summary,
        "details": details,
        "evidence": dict(evidence or {}),
        "next_action": next_action,
        "updated_at_utc": _now_utc(),
    }


def _signal_section(signal: dict[str, Any], *, reference_time: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    action = str(signal.get("action") or "HOLD").upper()
    confidence = _safe_float(signal.get("confidence"))
    reasoning = str(signal.get("reasoning") or "").strip()
    generated_at = signal.get("generated_at_utc")
    age = _age_seconds(generated_at, reference_time)
    state = "live" if action in {"BUY", "SELL", "HOLD"} else "unknown"
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    if generated_at:
        events.append(
            {
                "component": "signal",
                "severity": "info",
                "code": "SIGNAL_ISSUED",
                "summary": f"Latest signal is {action} at {confidence:.0%} confidence.",
                "details": reasoning or "No signal rationale was provided.",
                "evidence": {"generated_at_utc": generated_at, "age_seconds": age},
                "updated_at_utc": _now_utc(),
            }
        )
        if age is not None and age > 600:
            issues.append(
                _issue(
                    component="signal",
                    severity="warning",
                    code="SIGNAL_STALE",
                    summary=f"Signal is stale at {age}s old.",
                    details="Oracle should refresh before relying on this signal for a new trade.",
                    evidence={"generated_at_utc": generated_at, "confidence": confidence},
                    next_action="Run the bridge to refresh the signal before the next trade window.",
                )
            )
    else:
        state = "missing"
        issues.append(
            _issue(
                component="signal",
                severity="warning",
                code="SIGNAL_MISSING",
                summary="No live oracle signal is present.",
                details="The dashboard cannot show a current BUY/SELL/HOLD decision yet.",
                next_action="Run the bridge to generate a fresh signal.",
            )
        )

    return (
        {
            "state": state,
            "action": action,
            "confidence": confidence,
            "generated_at_utc": generated_at,
            "reasoning": reasoning,
            "age_seconds": age,
        },
        issues,
        events,
    )


def _quote_section(quote: dict[str, Any], *, reference_time: datetime | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    bid = quote.get("bid")
    ask = quote.get("ask")
    mid = quote.get("mid")
    updated_at = quote.get("updated_at_utc")
    age = _age_seconds(updated_at, reference_time)
    state = "missing"
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    if any(v is not None for v in (bid, ask, mid)):
        if age is not None and age <= 15:
            state = "live"
        elif age is not None:
            state = "stale"
        else:
            state = "live"
        events.append(
            {
                "component": "quote",
                "severity": "info",
                "code": "QUOTE_REFRESHED",
                "summary": f"Live quote bid {bid} / ask {ask} / mid {mid}.",
                "details": "Manual levels and the chart are using the latest cached broker quote.",
                "evidence": {"updated_at_utc": updated_at, "age_seconds": age},
                "updated_at_utc": _now_utc(),
            }
        )
        if state == "stale":
            issues.append(
                _issue(
                    component="quote",
                    severity="warning",
                    code="QUOTE_STALE",
                    summary=f"Quote is stale at {age}s old.",
                    details="The cached bid/ask is still present, but it has not refreshed recently.",
                    evidence={"updated_at_utc": updated_at, "bid": bid, "ask": ask, "mid": mid},
                    next_action="Wait for a fresh tick or reconnect the feed before placing a new trade.",
                )
            )
    else:
        issues.append(
            _issue(
                component="quote",
                severity="critical",
                code="QUOTE_MISSING",
                summary="No live quote is available.",
                details="Manual trade validation and the live chart need a broker quote.",
                next_action="Check the cTrader feed and wait for the next tick update.",
            )
        )

    return (
        {
            "state": state,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "updated_at_utc": updated_at,
            "age_seconds": age,
        },
        issues,
        events,
    )


def _positions_section(open_positions: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    oracle_positions = [p for p in open_positions if str(p.get("owner", "") or "").lower() == "oracle"]
    manual_positions = [p for p in open_positions if str(p.get("owner", "") or "").lower() == "manual"]
    events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if oracle_positions:
        events.append(
            {
                "component": "oracle",
                "severity": "info",
                "code": "ORACLE_POSITION_OPEN",
                "summary": f"Oracle has {len(oracle_positions)} managed position(s) open.",
                "details": "Oracle session manager will continue to protect and trail these positions.",
                "evidence": {"position_ids": [p.get("position_id") for p in oracle_positions]},
                "updated_at_utc": _now_utc(),
            }
        )
    if manual_positions:
        events.append(
            {
                "component": "manual",
                "severity": "info",
                "code": "MANUAL_POSITION_OPEN",
                "summary": f"Manual lane has {len(manual_positions)} open position(s).",
                "details": "Oracle ignores manual positions for risk gates and session management.",
                "evidence": {"position_ids": [p.get("position_id") for p in manual_positions]},
                "updated_at_utc": _now_utc(),
            }
        )

    if not oracle_positions and not manual_positions:
        events.append(
            {
                "component": "positions",
                "severity": "info",
                "code": "NO_OPEN_POSITIONS",
                "summary": "No positions are open right now.",
                "details": "The dashboard is idle and waiting for the next Oracle or manual trade.",
                "evidence": {},
                "updated_at_utc": _now_utc(),
            }
        )

    return (
        {
            "oracle_open": len(oracle_positions),
            "manual_open": len(manual_positions),
            "total_open": len(open_positions),
        },
        issues,
        events,
    )


def _risk_section(risk: dict[str, Any], runtime: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    daily_halted = bool(risk.get("daily_halted"))
    weekly_halted = bool(risk.get("weekly_halted"))
    runtime = runtime or {}
    trades_taken = _safe_int(risk.get("mirofish_trades_taken_london"))
    signal_runs = count_london_signal_runs(risk, runtime)
    run_cap = _safe_int(runtime.get("mirofish_max_trades_per_day"), 2)
    if run_cap <= 0:
        run_cap = 2

    if daily_halted:
        issues.append(
            _issue(
                component="risk",
                severity="warning",
                code="DAILY_HALTED",
                summary="Daily risk gate is halted.",
                details="Oracle should not open new trades until the daily halt clears.",
                evidence={"daily_pnl": risk.get("daily_pnl"), "losses_today": risk.get("consecutive_losses_today")},
                next_action="Review the risk state before trying to force another Oracle entry.",
            )
        )
    if weekly_halted:
        issues.append(
            _issue(
                component="risk",
                severity="warning",
                code="WEEKLY_HALTED",
                summary="Weekly risk gate is halted.",
                details="Oracle should not open new trades this week until the weekly halt clears.",
                evidence={"weekly_pnl": risk.get("weekly_pnl")},
                next_action="Review weekly drawdown and only resume after manual clearance.",
            )
        )
    if signal_runs >= 1:
        events.append(
            {
                "component": "risk",
                "severity": "info",
                "code": "DAILY_SIGNAL_SLOT_USED",
                "summary": f"Today's Oracle slot usage is {signal_runs}/{run_cap}.",
                "details": "The Oracle signal engine has consumed this many run slots today.",
                "evidence": {
                    "mirofish_signal_runs_london": risk.get("mirofish_signal_runs_london", []),
                    "mirofish_trade_date_london": risk.get("mirofish_trade_date_london"),
                },
                "updated_at_utc": _now_utc(),
            }
        )
    if signal_runs >= run_cap and trades_taken < run_cap:
        events.append(
            {
                "component": "risk",
                "severity": "info",
                "code": "DAILY_SIGNAL_SLOT_LIMIT_REACHED",
                "summary": f"Today's Oracle run slot budget is fully used ({signal_runs}/{run_cap}).",
                "details": "The oracle will skip further slots today until next London weekday.",
                "evidence": {
                    "mirofish_signal_runs_london": risk.get("mirofish_signal_runs_london", []),
                    "mirofish_max_trades_per_day": run_cap,
                },
                "updated_at_utc": _now_utc(),
            }
        )
    if trades_taken >= run_cap:
        events.append(
            {
                "component": "risk",
                "severity": "info",
                "code": "DAILY_TRADE_LIMIT_REACHED",
                "summary": f"Today's London trade counter is {trades_taken}/{run_cap}.",
                "details": "The Oracle lane already used its daily trade budget.",
                "evidence": {"mirofish_trade_date_london": risk.get("mirofish_trade_date_london")},
                "updated_at_utc": _now_utc(),
            }
        )

    return (
        {
            "daily_pnl": _safe_float(risk.get("daily_pnl")),
            "weekly_pnl": _safe_float(risk.get("weekly_pnl")),
            "daily_halted": daily_halted,
            "weekly_halted": weekly_halted,
            "trades_taken_today": trades_taken,
            "signal_runs_taken_today": signal_runs,
            "run_cap": run_cap,
            "day_start_balance": _safe_float(risk.get("day_start_balance")),
            "week_start_balance": _safe_float(risk.get("week_start_balance")),
        },
        issues,
        events,
    )


def _manual_section(runtime: dict[str, Any], quote: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manual = _safe_dict(runtime.get("manual_trade_status"))
    state = str(manual.get("state") or "").lower()
    reason = str(manual.get("reason") or "").strip()
    ok = bool(manual.get("ok"))
    events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if ok:
        events.append(
            {
                "component": "manual",
                "severity": "info",
                "code": "MANUAL_STATUS_OK",
                "summary": f"Manual command {manual.get('command', 'open')} is queued/confirmed.",
                "details": f"Position reference: {manual.get('position_id') or manual.get('trade_id') or '-'}.",
                "evidence": manual,
                "updated_at_utc": _now_utc(),
            }
        )
    elif reason:
        issues.append(
            _issue(
                component="manual",
                severity="warning",
                code="MANUAL_BLOCKED",
                summary=reason,
                details="Manual controls are available, but the last manual command was rejected or not ready yet.",
                evidence={"manual_trade_status": manual, "quote": quote},
                next_action="Adjust the form or wait for the bot to confirm a queued command.",
            )
        )
    else:
        events.append(
            {
                "component": "manual",
                "severity": "info",
                "code": "MANUAL_IDLE",
                "summary": "Manual lane is idle.",
                "details": "No manual command is currently queued.",
                "evidence": manual,
                "updated_at_utc": _now_utc(),
            }
        )

    return (
        {
            "state": state or ("confirmed" if ok else "idle"),
            "ok": ok,
            "reason": reason,
            "position_id": manual.get("position_id"),
            "command": manual.get("command"),
            "action": manual.get("action"),
            "updated_at_utc": manual.get("updated_at_utc"),
        },
        issues,
        events,
    )


def _bot_section(meta: dict[str, Any], last_error: Any) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    status = str(meta.get("bot_status") or "UNKNOWN").upper()
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    if status == "SHUTDOWN":
        events.append(
            {
                "component": "bot",
                "severity": "info",
                "code": "BOT_SHUTDOWN",
                "summary": "Bot is currently shut down.",
                "details": "This is expected after a timer stop or manual shutdown.",
                "evidence": {"bot_status": status},
                "updated_at_utc": _now_utc(),
            }
        )
    elif status.startswith("HALTED_"):
        reason = status.removeprefix("HALTED_")
        issues.append(
            _issue(
                component="bot",
                severity="warning",
                code=status,
                summary=f"Bot is halted by {reason.lower().replace('_', ' ')}.",
                details="New Oracle trades are blocked until the condition clears.",
                evidence={"bot_status": status, "last_error": last_error},
                next_action="Check the dashboard and logs for the halt reason.",
            )
        )
    else:
        events.append(
            {
                "component": "bot",
                "severity": "info",
                "code": "BOT_RUNNING",
                "summary": f"Bot status is {status}.",
                "details": "The engine is active and writing live state.",
                "evidence": {"bot_status": status},
                "updated_at_utc": _now_utc(),
            }
        )
    if last_error:
        issues.append(
            _issue(
                component="bot",
                severity="critical",
                code="LAST_ERROR",
                summary="The bot recorded a recent error.",
                details=str(last_error),
                evidence={"bot_status": status},
                next_action="Inspect the latest error in the logs and dashboard diagnostics.",
            )
        )
    return {"state": status, "last_error": last_error}, issues, events, status


def _strategy_section(runtime: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    current = _safe_dict(runtime.get("strategy_data_status"))
    shadow = _safe_dict(runtime.get("shadow_strategy_data_status"))
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for name, data in (("active", current), ("shadow", shadow)):
        if not data:
            continue
        ready = bool(data.get("data_ready", True))
        reason = str(data.get("reason") or "").strip()
        if ready:
            events.append(
                {
                    "component": f"strategy:{name}",
                    "severity": "info",
                    "code": "STRATEGY_READY",
                    "summary": f"{name.title()} strategy data is ready.",
                    "details": reason or "No blocking issue reported.",
                    "evidence": data,
                    "updated_at_utc": _now_utc(),
                }
            )
        else:
            issues.append(
                _issue(
                    component=f"strategy:{name}",
                    severity="warning",
                    code="STRATEGY_DATA_NOT_READY",
                    summary=f"{name.title()} strategy data is not ready.",
                    details=reason or "Strategy data source has not finished preparing.",
                    evidence=data,
                    next_action="Wait for the data source to refresh or inspect the upstream failure.",
                )
            )

    return {"active": current, "shadow": shadow}, issues, events


def build_diagnostics_snapshot(
    state: Mapping[str, Any],
    *,
    oracle_signal: Mapping[str, Any] | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    state_dict = dict(state or {})
    meta = _safe_dict(state_dict.get("meta"))
    account = _safe_dict(state_dict.get("account"))
    risk = _safe_dict(state_dict.get("risk"))
    runtime = _safe_dict(state_dict.get("runtime"))
    open_positions = state_dict.get("open_positions") or []
    last_signal = _safe_dict(oracle_signal) or _safe_dict(state_dict.get("last_signal"))
    signal_history = state_dict.get("signal_history") or []

    if not last_signal and signal_history:
        candidate = signal_history[-1]
        last_signal = candidate if isinstance(candidate, dict) else {}

    bot_section, bot_issues, bot_events, bot_status = _bot_section(meta, state_dict.get("last_error"))
    quote_section, quote_issues, quote_events = _quote_section(_safe_dict(runtime.get("latest_quote")), reference_time=reference_time)
    signal_section, signal_issues, signal_events = _signal_section(last_signal, reference_time=reference_time)
    positions_section, position_issues, position_events = _positions_section(open_positions if isinstance(open_positions, list) else [])
    risk_section, risk_issues, risk_events = _risk_section(risk, runtime=runtime)
    manual_section, manual_issues, manual_events = _manual_section(runtime, _safe_dict(runtime.get("latest_quote")))
    strategy_section, strategy_issues, strategy_events = _strategy_section(runtime)

    issues = bot_issues + quote_issues + signal_issues + position_issues + risk_issues + manual_issues + strategy_issues
    events = bot_events + quote_events + signal_events + position_events + risk_events + manual_events + strategy_events

    severity_rank = {"critical": 3, "warning": 2, "info": 1}
    worst = max((severity_rank.get(issue["severity"], 0) for issue in issues), default=0)
    overall_status = "healthy"
    if worst >= 3:
        overall_status = "blocked"
    elif worst >= 2:
        overall_status = "degraded"

    quote_state = quote_section["state"]
    signal_action = signal_section["action"]
    if quote_state == "live":
        quote_summary = f"Live quote {quote_section['bid']} / {quote_section['ask']} / {quote_section['mid']}."
    elif quote_state == "stale":
        quote_summary = f"Quote is stale at {quote_section['age_seconds']}s old."
    else:
        quote_summary = "No live quote yet."

    if signal_action == "HOLD":
        signal_summary = "Latest oracle decision is HOLD."
    else:
        signal_summary = f"Latest oracle decision is {signal_action} at {signal_section['confidence']:.0%} confidence."

    if positions_section["oracle_open"]:
        positions_summary = f"Oracle has {positions_section['oracle_open']} managed position(s) open."
    elif positions_section["manual_open"]:
        positions_summary = f"Manual lane has {positions_section['manual_open']} open position(s)."
    else:
        positions_summary = "No positions are open."

    if manual_section["ok"]:
        manual_summary = f"Manual command {manual_section['command'] or 'open'} is queued or confirmed."
    elif manual_section["reason"]:
        manual_summary = f"Manual lane warning: {manual_section['reason']}."
    else:
        manual_summary = "Manual lane is idle."

    summary = " | ".join(
        part for part in [
            f"{bot_status}",
            signal_summary,
            quote_summary,
            positions_summary,
            manual_summary,
        ] if part
    )

    if overall_status == "blocked":
        reply = issues[0]["summary"] if issues else "Diagnostics indicate a blocked state."
        next_action = issues[0]["next_action"] if issues else "Inspect the dashboard diagnostics."
    elif overall_status == "degraded":
        reply = issues[0]["summary"] if issues else "Diagnostics indicate a degraded but usable state."
        next_action = issues[0]["next_action"] if issues else "Review the warning details before placing a trade."
    else:
        reply = f"{signal_summary} {quote_summary} {positions_summary}".strip()
        next_action = "No immediate action required."

    return {
        "schema_version": 1,
        "generated_at_utc": _now_utc(),
        "overall_status": overall_status,
        "summary": summary,
        "reply": reply,
        "recommended_action": next_action,
        "current_issues": issues,
        "components": {
            "bot": bot_section,
            "quote": quote_section,
            "signal": signal_section,
            "positions": positions_section,
            "risk": risk_section,
            "manual": manual_section,
            "strategy": strategy_section,
            "account": {
                "balance": _safe_float(account.get("balance")),
                "equity": _safe_float(account.get("equity")),
                "open_pnl": _safe_float(account.get("open_pnl")),
                "currency": account.get("currency", "GBP"),
            },
        },
        "recent_events": events[-25:],
    }
