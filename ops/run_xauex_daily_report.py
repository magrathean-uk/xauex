#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.request import urlopen


RECIPIENT = os.environ.get("XAUEX_REPORT_EMAIL", "bolyki@bolyki.eu")
DASHBOARD_URL = os.environ.get("XAUEX_DASHBOARD_URL", "http://127.0.0.1:8089/api/dashboard")
HEALTH_URL = os.environ.get("XAUEX_HEALTH_URL", "http://127.0.0.1:8051/health")
COST_LEDGER_PATH = os.environ.get("XAUEX_SIGNAL_COST_LEDGER_PATH", "/var/lib/xauex/signal_costs.jsonl")
DAILY_COST_CAP_USD = float(os.environ.get("XAUEX_SIGNAL_DAILY_COST_CAP_USD", "0.20"))
SERVICES = [
    "xauex-web.service",
    "xauex.service",
    "xauex-window-signal@morning.timer",
    "xauex-window-signal@midday.timer",
    "xauex-window-signal@us_open.timer",
    "xauex-window-confirm@morning.timer",
    "xauex-window-confirm@midday.timer",
    "xauex-window-confirm@us_open.timer",
    "xauex-shadow-compare.timer",
    "xauex-shadow-evaluate.timer",
    "xauex-shadow-report.timer",
    "xauex-start.timer",
    "xauex-stop.timer",
    "xauex-trade-journal.timer",
    "xauex-weekly-review.timer",
]


@dataclass
class CheckResult:
    ok: bool
    subject: str
    body: str


def _is_critical_issue(issue: Any) -> bool:
    return str(issue.get("severity", "")).lower() == "critical" if isinstance(issue, dict) else False


def _signal_runs_complete(account: Any) -> bool:
    if not isinstance(account, dict):
        return False
    try:
        taken = int(account.get("signal_runs_taken_today", 0) or 0)
        cap = int(account.get("signal_runs_cap", 0) or 0)
    except (TypeError, ValueError):
        return False
    return cap > 0 and taken >= cap


def _is_expected_end_of_day_issue(issue: Any, account: Any) -> bool:
    if not isinstance(issue, dict):
        return False
    return (
        str(issue.get("code") or "").upper() == "SIGNAL_STALE"
        and str(issue.get("component") or "").lower() == "signal"
        and _signal_runs_complete(account)
    )


def _positions_snapshot(diagnostics: Any) -> dict[str, Any]:
    if not isinstance(diagnostics, dict):
        return {}
    components = diagnostics.get("components", {})
    if not isinstance(components, dict):
        return {}
    positions = components.get("positions", {})
    return positions if isinstance(positions, dict) else {}


def _is_expected_blocked_status(status: Any, diagnostics: Any) -> bool:
    normalized = str(status or "").upper()
    if normalized in {"RUNNING", "OK", "OBSERVE_ONLY"}:
        return True
    if normalized != "HALTED_MAX_POSITIONS_REACHED":
        return False
    positions = _positions_snapshot(diagnostics)
    try:
        return int(positions.get("xauex_open", 0) or 0) > 0
    except (TypeError, ValueError):
        return False


def _is_hard_outage_status(status: Any, diagnostics: Any) -> bool:
    normalized = str(status or "").upper()
    if _is_expected_blocked_status(normalized, diagnostics):
        return False
    return normalized in {"DISCONNECTED", "UNKNOWN", "SHUTDOWN", "STOPPED", "OFFLINE"} or normalized.startswith("HALTED")


def _fetch_json(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


def _service_status(unit: str) -> str:
    completed = subprocess.run(
        ["systemctl", "is-active", unit],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return "active" if completed.returncode == 0 else "inactive"


def _render_report() -> CheckResult:
    failures: list[str] = []
    service_lines = []
    for unit in SERVICES:
        state = _service_status(unit)
        service_lines.append(f"{unit}: {state}")
        if state != "active":
            failures.append(f"{unit} is {state}")

    dashboard: dict[str, Any] = {}
    health: dict[str, Any] = {}

    try:
        dashboard = _fetch_json(DASHBOARD_URL)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        failures.append(f"dashboard unavailable: {exc}")

    try:
        health = _fetch_json(HEALTH_URL)
    except Exception as exc:  # pragma: no cover - defensive runtime path
        failures.append(f"health unavailable: {exc}")

    payload = dashboard.get("data", {}) if isinstance(dashboard, dict) else {}
    signal = payload.get("signal", {}) if isinstance(payload, dict) else {}
    diagnostics = payload.get("diagnostics", {}) if isinstance(payload, dict) else {}
    account = payload.get("account", {}) if isinstance(payload, dict) else {}
    components = diagnostics.get("components", {}) if isinstance(diagnostics, dict) else {}
    bot = components.get("bot", {}) if isinstance(components, dict) else {}
    quote = components.get("quote", {}) if isinstance(components, dict) else {}

    signal_action = signal.get("action") or "UNKNOWN"
    signal_confidence = signal.get("confidence") or "UNKNOWN"
    signal_generated_at = signal.get("generated_at_utc") or "-"
    signal_cost = ((signal.get("llm_usage") or {}) if isinstance(signal, dict) else {}).get("estimated_total_cost_usd")
    candidate_metrics = payload.get("candidate_metrics", {}) if isinstance(payload, dict) else {}
    bot_state = bot.get("state") or "-"
    quote_state = quote.get("state") or "-"
    summary = diagnostics.get("summary") if isinstance(diagnostics, dict) else None
    health_status = health.get("status", "unknown") if isinstance(health, dict) else "unknown"
    bot_status = health.get("bot_status", "unknown") if isinstance(health, dict) else "unknown"
    diagnostic_issues = diagnostics.get("current_issues", []) if isinstance(diagnostics, dict) else []
    if not isinstance(diagnostic_issues, list):
        diagnostic_issues = []
    warning_issues = [
        issue
        for issue in diagnostic_issues
        if not _is_critical_issue(issue) or _is_expected_end_of_day_issue(issue, account)
    ]
    critical_issues = [
        issue
        for issue in diagnostic_issues
        if _is_critical_issue(issue) and not _is_expected_end_of_day_issue(issue, account)
    ]
    cost_summary = _daily_cost_summary()
    budget_warning = None
    if cost_summary["daily_cap_usd"] > 0 and cost_summary["total_cost_usd"] / cost_summary["daily_cap_usd"] >= 0.8:
        budget_warning = (
            f"daily cost is at {int(round(cost_summary['total_cost_usd'] / cost_summary['daily_cap_usd'] * 100))}% of cap"
        )

    if health_status == "disconnected" or not isinstance(health, dict):
        failures.append(f"bot health is {health_status}")
    if _is_hard_outage_status(bot_status, diagnostics):
        failures.append(f"bot status is {bot_status}")
    for issue in critical_issues:
        component = str(issue.get("component") or "diagnostics")
        summary_text = str(issue.get("summary") or "critical issue")
        failures.append(f"{component}: {summary_text}")

    ok = not failures
    status_line = "OK" if ok else "FAILED"
    subject = f"XAUEX daily status {status_line}"
    failure_block = "\n".join(f"- {item}" for item in failures) if failures else "- none"
    warning_block = (
        "\n".join(
            f"- {str(issue.get('component') or 'diagnostics')}: {str(issue.get('summary') or 'warning issue')}"
            for issue in warning_issues
        )
        if warning_issues
        else "- none"
    )
    if budget_warning:
        warning_block = f"{warning_block}\n- budget warning: {budget_warning}"

    body = "\n".join(
        [
            f"XAUEX daily status report - {status_line}",
            "",
            f"Timestamp (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
            "Services:",
            *[f"- {line}" for line in service_lines],
            "",
            "Dashboard:",
            f"- signal: {signal_action}",
            f"- confidence: {signal_confidence}",
            f"- generated_at_utc: {signal_generated_at}",
            f"- bot state: {bot_state}",
            f"- quote state: {quote_state}",
            f"- signal runs: {account.get('signal_runs_taken_today', 0)}/{account.get('signal_runs_cap', 3)}",
            f"- trades used: {account.get('trades_taken_today', 0)}/{account.get('trade_cap', 3)}",
            f"- candidate lane: total {candidate_metrics.get('total', 0)} / completed {candidate_metrics.get('completed', 0)} / false-negative wins {candidate_metrics.get('false_negative_wins', 0)}",
            f"- latest run cost: {float(signal_cost or 0.0):.6f} USD",
            f"- daily LLM cost: {cost_summary['total_cost_usd']:.4f} USD across {cost_summary['run_count']} run(s)",
            f"- health: {health_status}",
            f"- bot health: {bot_status}",
            f"- diagnostics: {summary or '-'}",
            f"- diagnostics overall: {diagnostics.get('overall_status', 'unknown') if isinstance(diagnostics, dict) else 'unknown'}",
            "",
            "Checks:",
            failure_block,
            "",
            "Warnings:",
            warning_block,
            "",
            f"Dashboard URL: {DASHBOARD_URL}",
            f"Health URL: {HEALTH_URL}",
        ]
    )

    return CheckResult(ok=ok, subject=subject, body=body)


def _daily_cost_summary() -> dict[str, float | int]:
    total_cost_usd = 0.0
    run_count = 0
    today = datetime.now(timezone.utc).date()
    path = Path(COST_LEDGER_PATH)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_text = str(row.get("timestamp_utc") or "").strip()
                try:
                    ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.astimezone(timezone.utc).date() != today:
                    continue
                total_cost_usd += float(row.get("estimated_total_cost_usd", 0.0) or 0.0)
                run_count += 1
    return {
        "total_cost_usd": round(total_cost_usd, 4),
        "daily_cap_usd": DAILY_COST_CAP_USD,
        "run_count": run_count,
    }


def _send_mail(subject: str, body: str) -> None:
    message = EmailMessage()
    message["To"] = RECIPIENT
    message["From"] = RECIPIENT
    message["Subject"] = subject
    message.set_content(body)

    subprocess.run(
        ["/usr/sbin/sendmail", "-t", "-oi"],
        input=message.as_bytes(),
        check=True,
    )


def main() -> int:
    result = _render_report()
    print(result.body)
    _send_mail(result.subject, result.body)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
