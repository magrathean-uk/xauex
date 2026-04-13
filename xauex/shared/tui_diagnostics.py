from __future__ import annotations

import textwrap
from typing import Any, Mapping, Sequence


def _shorten(text: Any, width: int = 108) -> str:
    value = str(text or "").strip()
    if not value:
        return "—"
    return textwrap.shorten(" ".join(value.split()), width=width, placeholder="…")


def _component_value(name: str, payload: Mapping[str, Any]) -> str:
    if name == "signal":
        action = str(payload.get("action") or "").strip().upper()
        if action and action != "HOLD":
            return action
    return str(payload.get("state") or "unknown")


def _component_summary(components: Mapping[str, Any]) -> str:
    ordered = []
    for name in ("quote", "signal", "positions", "manual"):
        payload = components.get(name) or {}
        ordered.append(f"{name}={_component_value(name, payload)}")
    return " ".join(ordered)


def format_diagnostics_panel(
    diagnostics: Mapping[str, Any] | None,
    *,
    transport_errors: Sequence[str] | None = None,
    max_issues: int = 3,
) -> str:
    payload = dict(diagnostics or {})
    status = str(payload.get("overall_status") or "unknown").upper()
    summary = _shorten(payload.get("summary") or "No diagnostics snapshot is available yet.")
    reply = _shorten(payload.get("reply") or "No operator reply is available yet.")
    next_action = _shorten(payload.get("recommended_action") or "Inspect the latest state and logs.")
    components = payload.get("components") or {}
    issues = list(payload.get("current_issues") or [])

    lines = [
        f"DIAGNOSTICS [{status}]",
        f"  Components: {_component_summary(components)}",
        f"  Summary: {summary}",
        f"  Reply: {reply}",
        f"  Next: {next_action}",
    ]

    if issues:
        lines.append("  Issues:")
        for issue in issues[:max_issues]:
            component = str(issue.get("component") or "diagnostics").replace("_", " ").title()
            lines.append(f"    - {component}: {_shorten(issue.get('summary'))}")

    if transport_errors:
        lines.append("  Transport:")
        for error in list(transport_errors)[:max_issues]:
            lines.append(f"    - {_shorten(error)}")

    return "\n".join(lines)

