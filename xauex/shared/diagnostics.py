from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from xauex.live_windows import active_entry_slot


LONDON = ZoneInfo("Europe/London")


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


def _first_present(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return default


def count_london_signal_runs(risk: Mapping[str, Any], runtime: Mapping[str, Any] | None = None) -> int:
    runtime_dict = _safe_dict(runtime)
    signal_runs = _safe_int(
        _first_present(
            runtime_dict,
            "xauex_signal_runs_taken_london",
            default=0,
        )
    )
    runtime_signal_runs = _first_present(
        runtime_dict,
        "xauex_signal_runs_london",
        default=None,
    )
    if isinstance(runtime_signal_runs, list):
        signal_runs = len([item for item in runtime_signal_runs if bool(item.get("terminal", True))])

    risk_signal_runs = _first_present(
        risk,
        "xauex_signal_runs_london",
        default=None,
    )
    if isinstance(risk_signal_runs, list):
        signal_runs = len([item for item in risk_signal_runs if bool(item.get("terminal", True))])
    elif signal_runs == 0:
        signal_runs = _safe_int(
            _first_present(
                risk,
                "xauex_signal_runs_taken_london",
                default=0,
            )
        )

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


def _is_london_trade_window(reference_time: datetime | None = None) -> bool:
    now = reference_time or datetime.now(timezone.utc)
    return active_entry_slot(now.astimezone(timezone.utc)) is not None


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
    signal_issue_severity = "critical" if _is_london_trade_window(reference_time) else "warning"
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
            stale_details = "XAUEX should refresh before relying on this signal for a new trade."
            stale_next_action = "Run the signal pipeline to refresh the signal before the next trade window."
            if signal_issue_severity != "critical":
                stale_details = "The London session is closed, so this stale signal is expected until the next run."
                stale_next_action = "Refresh the signal before the next London entry window."
            issues.append(
                _issue(
                    component="signal",
                    severity=signal_issue_severity,
                    code="SIGNAL_STALE",
                    summary=f"Signal is stale at {age}s old.",
                    details=stale_details,
                    evidence={"generated_at_utc": generated_at, "confidence": confidence},
                    next_action=stale_next_action,
                )
            )
    else:
        state = "missing"
        missing_details = "The dashboard cannot show a current BUY/SELL/HOLD decision yet."
        missing_next_action = "Run the signal pipeline to generate a fresh signal."
        if signal_issue_severity != "critical":
            missing_details = "The London session is closed, so a live signal is not expected yet."
            missing_next_action = "Wait for the next London entry window and run the signal pipeline."
        issues.append(
            _issue(
                component="signal",
                severity=signal_issue_severity,
                code="SIGNAL_MISSING",
                summary="No live XAUEX signal is present.",
                details=missing_details,
                next_action=missing_next_action,
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
    xauex_positions = [p for p in open_positions if str(p.get("owner", "") or "").lower() == "xauex"]
    manual_positions = [p for p in open_positions if str(p.get("owner", "") or "").lower() == "manual"]
    events: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    if xauex_positions:
        events.append(
            {
                "component": "xauex",
                "severity": "info",
                "code": "XAUEX_POSITION_OPEN",
                "summary": f"XAUEX has {len(xauex_positions)} managed position(s) open.",
                "details": "The XAUEX session manager will continue to protect and trail these positions.",
                "evidence": {"position_ids": [p.get("position_id") for p in xauex_positions]},
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
                "details": "Manual positions stay outside XAUEX session management and do not count toward XAUEX auto-entry limits.",
                "evidence": {"position_ids": [p.get("position_id") for p in manual_positions]},
                "updated_at_utc": _now_utc(),
            }
        )
    if not xauex_positions and not manual_positions:
        events.append(
            {
                "component": "positions",
                "severity": "info",
                "code": "NO_OPEN_POSITIONS",
                "summary": "No positions are open right now.",
                "details": "The dashboard is idle and waiting for the next XAUEX or manual trade.",
                "evidence": {},
                "updated_at_utc": _now_utc(),
            }
        )

    return (
        {
            "xauex_open": len(xauex_positions),
            "manual_open": len(manual_positions),
            "total_open": len(open_positions),
        },
        issues,
        events,
    )


def _manual_section(runtime: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    payload = _safe_dict(runtime.get("manual_trade_status"))
    state = "idle"
    if payload.get("ok") is True:
        state = "ready"
    elif payload:
        state = str(payload.get("state") or "idle")
    section = {
        "state": state,
        "reason": str(payload.get("reason") or ""),
        "updated_at_utc": payload.get("updated_at_utc"),
    }
    return section, [], []


def _risk_section(risk: Mapping[str, Any], runtime: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    trades_taken = _safe_int(_first_present(risk, "xauex_trades_taken_london", default=0))
    run_cap = _safe_int(_first_present(runtime, "xauex_max_trades_per_day", default=3), 3)
    signal_runs = count_london_signal_runs(risk, runtime)
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [
        {
            "component": "risk",
            "severity": "info",
            "code": "RISK_STATE",
            "summary": f"Today's XAUEX slot usage is {signal_runs}/{run_cap}.",
            "details": "The XAUEX signal engine has consumed this many run slots today.",
            "evidence": {
                "signal_runs_taken_today": signal_runs,
                "trade_cap": run_cap,
            },
            "updated_at_utc": _now_utc(),
        }
    ]
    if bool(risk.get("daily_halted")):
        issues.append(
            _issue(
                component="risk",
                severity="critical",
                code="DAILY_HALTED",
                summary="Daily risk halt is active.",
                details="New XAUEX trades are blocked until the daily halt clears.",
                next_action="Review the risk state before forcing another XAUEX entry.",
            )
        )
    wiring = _safe_dict(runtime.get("risk_state_wiring"))
    wiring_valid = wiring.get("valid") is not False
    if not wiring_valid:
        issues.append(
            _issue(
                component="risk",
                severity="critical",
                code="RISK_STATE_WIRING_INVALID",
                summary="XAUEX risk-state wiring is invalid.",
                details="Risk limits cannot be trusted until the bot is restarted with a coherent state graph.",
                evidence={"error": str(wiring.get("error") or "RISK_STATE_WIRING_INVALID")},
                next_action="Restart XAUEX and inspect risk-state restoration logs.",
            )
        )
    return (
        {
            "daily_halted": bool(risk.get("daily_halted")),
            "weekly_halted": bool(risk.get("weekly_halted")),
            "trades_taken_today": trades_taken,
            "signal_runs_taken_today": signal_runs,
            "trade_cap": run_cap,
            "risk_state_wiring_valid": wiring_valid,
            "risk_state_wiring_error": str(wiring.get("error") or ""),
        },
        issues,
        events,
    )


def build_diagnostics_snapshot(
    state: Mapping[str, Any] | None,
    *,
    oracle_signal: Mapping[str, Any] | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    state_dict = _safe_dict(state)
    runtime = _safe_dict(state_dict.get("runtime"))
    quote = _safe_dict(runtime.get("latest_quote"))
    risk = _safe_dict(state_dict.get("risk"))
    open_positions = list(state_dict.get("open_positions") or [])
    last_signal = _safe_dict(oracle_signal) or _safe_dict(state_dict.get("last_signal"))

    quote_section, quote_issues, quote_events = _quote_section(quote, reference_time=reference_time)
    signal_section, signal_issues, signal_events = _signal_section(last_signal, reference_time=reference_time)
    positions_section, positions_issues, positions_events = _positions_section(open_positions)
    manual_section, manual_issues, manual_events = _manual_section(runtime)
    risk_section, risk_issues, risk_events = _risk_section(risk, runtime)

    issues = quote_issues + signal_issues + positions_issues + manual_issues + risk_issues
    events = quote_events + signal_events + positions_events + manual_events + risk_events
    overall_status = "healthy"
    if any(item["severity"] == "critical" for item in issues):
        overall_status = "blocked"
    elif issues:
        overall_status = "degraded"

    signal_action = str(signal_section["action"] or "HOLD").upper()
    if signal_action == "HOLD":
        signal_summary = "Latest XAUEX decision is HOLD."
    else:
        signal_summary = f"Latest XAUEX decision is {signal_action} at {signal_section['confidence']:.0%} confidence."

    if positions_section["xauex_open"]:
        positions_summary = f"XAUEX has {positions_section['xauex_open']} managed position(s) open."
    elif positions_section["manual_open"]:
        positions_summary = "Manual positions are open while XAUEX has no managed trade."
    else:
        positions_summary = "No positions are open right now."

    summary = f"{signal_summary} {positions_summary}"
    reply = issues[0]["summary"] if issues else "XAUEX is live and no immediate operator action is required."
    recommended_action = issues[0]["next_action"] if issues and issues[0]["next_action"] else "Monitor the next signal window and live quote."

    return {
        "schema_version": 1,
        "generated_at_utc": _now_utc(),
        "overall_status": overall_status,
        "summary": summary,
        "reply": reply,
        "recommended_action": recommended_action,
        "current_issues": issues,
        "recent_events": events[-8:],
        "components": {
            "quote": quote_section,
            "signal": signal_section,
            "positions": positions_section,
            "manual": manual_section,
            "risk": risk_section,
        },
    }
