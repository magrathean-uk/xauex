from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, abort, jsonify, render_template, request

from diagnostics import build_diagnostics_snapshot


STATE_PATH = Path(os.getenv("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
CMD_PATH = Path(os.getenv("CMD_FILE_PATH", "/var/lib/xauex/cmd.json"))
MANUAL_CMD_PATH = Path(os.getenv("MIROFISH_MANUAL_COMMAND_PATH", "/var/lib/xauex/manual_trade_cmd.json"))
JOURNAL_PATH = Path(os.getenv("TRADE_JOURNAL_PATH", "/var/lib/xauex/trade_journal.json"))
REVIEW_PATH = Path(os.getenv("WEEKLY_REVIEW_PATH", "/var/lib/xauex/weekly_review.json"))
RISK_PATH = Path(os.getenv("RISK_STATE_PATH", "/var/lib/xauex/risk_state.json"))
BRIEF_PATH = Path(os.getenv("BRIDGE_BRIEF_OUTPUT_PATH", "/var/lib/xauex/latest_signal_brief.md"))
BRIEF_META_PATH = BRIEF_PATH.with_suffix(".json")
EVIDENCE_PATH = Path(os.getenv("BRIDGE_EVIDENCE_OUTPUT_PATH", "/var/lib/xauex/latest_signal_evidence.json"))
MIROFISH_URL = os.getenv("MIROFISH_URL", "http://10.8.0.1:8088").rstrip("/")


def _load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_ts(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return value


def _extract_signal(cmd: dict[str, Any]) -> dict[str, Any]:
    signal = cmd.get("mirofish_signal", {}) or {}
    sl = _safe_float(signal.get("stop_loss_usd", signal.get("stop_loss_distance")))
    tp = _safe_float(signal.get("take_profit_usd", signal.get("take_profit_distance")))
    return {
        "action": signal.get("action", "HOLD"),
        "symbol": signal.get("symbol", "XAUUSD"),
        "confidence": _safe_float(signal.get("confidence")),
        "reasoning": signal.get("reasoning", ""),
        "generated_at_utc": _fmt_ts(cmd.get("generated_at_utc") or signal.get("timestamp_utc")),
        "stop_loss_distance": sl,
        "take_profit_distance": tp,
        "risk_reward": round(tp / sl, 2) if sl > 0 else None,
        "llm_usage": signal.get("llm_usage") or {},
        "source": signal.get("source") or {},
        "brief": signal.get("brief") or {},
    }


def _normalise_trade(entry: dict[str, Any]) -> dict[str, Any]:
    trade = entry.get("entry", entry)
    return {
        "trade_id": entry.get("trade_id") or trade.get("position_id") or "",
        "direction": trade.get("direction", ""),
        "entry_price": _safe_float(trade.get("entry_price")),
        "close_price": _safe_float(trade.get("close_price")),
        "stop_loss": _safe_float(trade.get("stop_loss")),
        "take_profit": _safe_float(trade.get("take_profit")),
        "lot_size": _safe_float(trade.get("lot_size")),
        "pnl": _safe_float(trade.get("pnl")),
        "pattern": trade.get("pattern", ""),
        "close_time_utc": _fmt_ts(trade.get("close_time_utc")),
        "journalled_at_utc": _fmt_ts(entry.get("journalled_at_utc")),
        "journal": entry.get("journal", ""),
    }


def _daily_metrics(account: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    balance = _safe_float(account.get("balance"))
    equity = _safe_float(account.get("equity"))
    open_pnl = _safe_float(account.get("open_pnl"))
    day_start = _safe_float(risk.get("day_start_balance"), balance)
    week_start = _safe_float(risk.get("week_start_balance"), balance)
    return {
        "balance": balance,
        "equity": equity,
        "open_pnl": open_pnl,
        "currency": account.get("currency", "GBP"),
        "daily_pnl": _safe_float(risk.get("daily_pnl"), balance - day_start),
        "weekly_pnl": _safe_float(risk.get("weekly_pnl"), balance - week_start),
        "day_start_balance": day_start,
        "week_start_balance": week_start,
        "daily_halted": bool(risk.get("daily_halted")),
        "weekly_halted": bool(risk.get("weekly_halted")),
        "trades_taken_today": int(risk.get("mirofish_trades_taken_london", 0) or 0),
    }


def _load_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return default


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    tmp_path.replace(path)


def _parse_numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _request_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if isinstance(payload, dict):
        return payload
    form = request.form.to_dict(flat=True)
    return form if form else {}


def _dashboard_auth_payload() -> dict[str, Any]:
    return {
        "configured": False,
        "authenticated": True,
        "username": None,
        "controls_enabled": True,
        "csrf_token": None,
    }


def _manual_controls_allowed() -> tuple[bool, str | None]:
    if not request.is_json:
        return False, "JSON body required."
    return True, None


def _manual_trade_command(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("command") not in (None, "", "open"):
        return None
    side = str(payload.get("side") or payload.get("action") or "").upper()
    if side not in {"BUY", "SELL"}:
        return None
    lot = _parse_numeric(payload.get("lot_size") if payload.get("lot_size") is not None else payload.get("lot"))
    if lot is None or lot <= 0:
        return None
    stop_loss = _parse_numeric(payload.get("stop_loss"))
    take_profit = _parse_numeric(payload.get("take_profit"))
    if stop_loss is None or take_profit is None:
        return None
    return {
        "command": "open",
        "action": side,
        "lot_size": lot,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def _manual_close_command(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("command") not in (None, "", "close"):
        return None
    position_id = str(payload.get("position_id") or payload.get("trade_id") or "").strip()
    if not position_id:
        return None
    return {
        "command": "close",
        "position_id": position_id,
    }


def _build_report_url(signal: dict[str, Any], brief_meta: dict[str, Any]) -> str | None:
    source = signal.get("source") or {}
    report_id = source.get("report_id") or brief_meta.get("report_id")
    if not report_id:
        return None
    return f"{MIROFISH_URL}/api/report/{report_id}/download"


def _build_direct_report_url() -> str:
    return "/report/direct"


def _build_brief_meta() -> dict[str, Any]:
    meta = _load_json(BRIEF_META_PATH, {})
    return {
        "exists": BRIEF_PATH.exists(),
        "path": str(BRIEF_PATH),
        "updated_at_utc": _fmt_ts(meta.get("updated_at_utc")),
        "report_id": meta.get("report_id"),
        "simulation_id": meta.get("simulation_id"),
        "usage": meta.get("usage") or {},
        "title": meta.get("title") or "Latest Brief",
        "download_url": "/brief/latest.md",
        "view_url": "/brief",
    }


def _format_manual_reply(command: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    quote = (diagnostics.get("components", {}) or {}).get("quote", {}) or {}
    bid = quote.get("bid")
    ask = quote.get("ask")
    action = str(command.get("action") or command.get("side") or "MANUAL").upper()
    cmd = str(command.get("command") or "open").lower()
    if cmd == "close":
        position_id = command.get("position_id") or command.get("trade_id") or "-"
        return f"Manual close queued for #{position_id}. {diagnostics.get('reply') or diagnostics.get('summary') or 'Waiting for bot confirmation.'}"
    lot_size = command.get("lot_size")
    if bid is not None and ask is not None:
        return (
            f"Manual {action} queued for {float(lot_size):.2f} lot(s). "
            f"Live quote bid {float(bid):.2f} / ask {float(ask):.2f}. "
            "The bot will confirm or reject it on the next execution poll."
        )
    return f"Manual {action} queued for {float(lot_size):.2f} lot(s). Waiting for the next live quote."


def _trade_explanation(signal: dict[str, Any], open_positions: list[dict[str, Any]], account: dict[str, Any]) -> str:
    action = str(signal.get("action") or "HOLD").upper()
    oracle_positions = [
        position for position in open_positions
        if str(position.get("owner", "") or "").lower() == "oracle"
    ]
    manual_positions = [
        position for position in open_positions
        if str(position.get("owner", "") or "").lower() == "manual"
    ]
    if oracle_positions:
        return "The bot has live positions open, so the signal is already in market."
    if manual_positions:
        return "Manual positions are open, but Oracle has no live managed trade right now."
    if action == "HOLD":
        return "No trade is open because the latest oracle decision is HOLD."
    trades_taken = int(account.get("trades_taken_today", 0) or 0)
    if trades_taken >= 1:
        return "No trade is open because today’s single London trade has already been used or closed."
    return "There is a directional signal, but no live position is open right now. That usually means the entry window was missed, the trade already closed, or execution conditions blocked it."


def _build_evidence() -> dict[str, Any]:
    payload = _load_json(EVIDENCE_PATH, {})
    if not isinstance(payload, dict):
        payload = {}
    return {
        'exists': EVIDENCE_PATH.exists(),
        'prediction_mode': payload.get('prediction_mode', 'unknown'),
        'context_summary': payload.get('context_summary', ''),
        'recent_runs': payload.get('recent_runs', []) or [],
        'weights': payload.get('weights', {}) or {},
        'price_features': payload.get('price_features', {}) or {},
    }


def _build_direct_report_context() -> dict[str, Any]:
    payload = _build_payload()
    brief_content = _load_text(BRIEF_PATH)
    evidence = payload.get("evidence", {}) or {}
    signal = payload.get("signal", {}) or {}
    return {
        "dashboard": payload,
        "brief_content": brief_content,
        "has_brief_content": bool(brief_content),
        "report_title": "Direct Report",
        "report_mode": "direct" if not payload.get("links", {}).get("report_download") else "hybrid",
        "report_direct_url": _build_direct_report_url(),
        "signal_summary": signal,
        "evidence_summary": evidence,
    }


def _build_payload() -> dict[str, Any]:
    state = _load_json(STATE_PATH, {})
    cmd = _load_json(CMD_PATH, {})
    journal = _load_json(JOURNAL_PATH, [])
    review = _load_json(REVIEW_PATH, {})
    risk_state = _load_json(RISK_PATH, {})
    if isinstance(state, dict):
        diagnostics = state.get("diagnostics", {}) or {}
    else:
        diagnostics = {}
    if not diagnostics:
        diagnostics = build_diagnostics_snapshot(state)

    account = state.get("account", {}) or {}
    risk = state.get("risk", {}) or {}
    meta = state.get("meta", {}) or {}
    open_positions = state.get("open_positions", []) or []
    recent_trades = [_normalise_trade(item) for item in journal[-20:]][::-1]
    closed_today = [_normalise_trade(item) for item in state.get("closed_trades_today", [])]
    signal = _extract_signal(cmd)
    account_payload = _daily_metrics(account, risk)
    brief_meta = _build_brief_meta()
    evidence = _build_evidence()
    runtime = state.get("runtime", {}) or {}
    manual_trade_status = runtime.get("manual_trade_status", {}) or {}
    latest_quote = runtime.get("latest_quote", {}) or {}
    return {
        "meta": {
            "bot_status": meta.get("bot_status", "UNKNOWN"),
            "last_updated_utc": _fmt_ts(meta.get("last_updated_utc")),
            "active_mode": (state.get("strategy") or {}).get("active_mode"),
            "shadow_mode": (state.get("strategy") or {}).get("shadow_mode"),
        },
        "account": account_payload,
        "signal": signal,
        "open_positions": open_positions,
        "manual_positions": [
            position for position in open_positions
            if str(position.get("owner", "") or "").lower() == "manual"
        ],
        "closed_trades_today": closed_today,
        "recent_trades": recent_trades,
        "signal_history": state.get("signal_history", []) or [],
        "shadow_signal_history": state.get("shadow_signal_history", []) or [],
        "recent_h1_closes": state.get("recent_h1_closes", []) or [],
        "trade_entries_on_chart": state.get("trade_entries_on_chart", []) or [],
        "chart": {
            "recent_h1_closes": state.get("recent_h1_closes", []) or [],
            "trade_entries": state.get("trade_entries_on_chart", []) or [],
            "quote": latest_quote,
        },
        "quote": latest_quote,
        "diagnostics": diagnostics,
        "levels": state.get("levels", {}) or {},
        "weekly_review": review,
        "risk_state": risk_state,
        "brief": brief_meta,
        "evidence": evidence,
        "manual_trade_status": manual_trade_status,
        "manual_command_pending": MANUAL_CMD_PATH.exists(),
        "auth": _dashboard_auth_payload(),
        "links": {
            "brief": "/brief",
            "brief_download": "/brief/latest.md",
            "report_direct": _build_direct_report_url(),
            "report_download": _build_report_url(signal, brief_meta),
            "report": _build_report_url(signal, brief_meta) or _build_direct_report_url(),
        },
        "trade_explanation": _trade_explanation(signal, open_positions, account_payload),
        "health": {
            "state_file": str(STATE_PATH),
            "cmd_file": str(CMD_PATH),
            "journal_entries": len(recent_trades),
            "positions_open": len(open_positions),
            "closed_today": len(closed_today),
        },
    }


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.get("/")
    def index() -> str:
        return render_template("index.html", dashboard_auth=_dashboard_auth_payload())

    @app.get("/brief")
    def brief_view() -> str:
        content = _load_text(BRIEF_PATH)
        if not content:
            abort(404)
        return render_template("brief.html", markdown_content=content, brief_meta=_build_brief_meta())

    @app.get("/brief/latest.md")
    def brief_download() -> Response:
        content = _load_text(BRIEF_PATH)
        if not content:
            abort(404)
        return Response(
            content,
            mimetype="text/markdown; charset=utf-8",
            headers={"Content-Disposition": "inline; filename=latest_signal_brief.md"},
        )

    @app.get("/report/direct")
    def direct_report() -> str:
        return render_template("report.html", **_build_direct_report_context())

    @app.get("/api/dashboard")
    def dashboard_data():
        return jsonify({"success": True, "data": _build_payload()})

    @app.get("/api/diagnostics")
    def diagnostics_data():
        payload = _build_payload()
        return jsonify({"success": True, "data": payload.get("diagnostics", {}) or {}})

    @app.post("/api/manual-trade")
    def manual_trade():
        allowed, reason = _manual_controls_allowed()
        if not allowed:
            payload = _build_payload()
            return jsonify({
                "success": False,
                "error": reason,
                "reply": reason,
                "diagnostics": payload.get("diagnostics", {}) or {},
            }), 400
        command = _manual_trade_command(_request_payload())
        if command is None:
            payload = _build_payload()
            diagnostics = payload.get("diagnostics", {}) or {}
            return jsonify({
                "success": False,
                "error": "Invalid manual trade command",
                "reply": diagnostics.get("reply") or "Invalid manual trade command.",
                "diagnostics": diagnostics,
            }), 400
        _write_json_atomic(MANUAL_CMD_PATH, command)
        payload = _build_payload()
        diagnostics = payload.get("diagnostics", {}) or {}
        return jsonify({
            "success": True,
            "status": "queued",
            "reply": _format_manual_reply(command, diagnostics),
            "manual_command_pending": True,
            "data": command,
            "diagnostics": diagnostics,
            "auth": _dashboard_auth_payload(),
        })

    @app.post("/api/manual-close")
    def manual_close():
        allowed, reason = _manual_controls_allowed()
        if not allowed:
            payload = _build_payload()
            return jsonify({
                "success": False,
                "error": reason,
                "reply": reason,
                "diagnostics": payload.get("diagnostics", {}) or {},
            }), 400
        command = _manual_close_command(_request_payload())
        if command is None:
            payload = _build_payload()
            diagnostics = payload.get("diagnostics", {}) or {}
            return jsonify({
                "success": False,
                "error": "Invalid manual close command",
                "reply": diagnostics.get("reply") or "Invalid manual close command.",
                "diagnostics": diagnostics,
            }), 400
        _write_json_atomic(MANUAL_CMD_PATH, command)
        payload = _build_payload()
        diagnostics = payload.get("diagnostics", {}) or {}
        return jsonify({
            "success": True,
            "status": "queued",
            "reply": _format_manual_reply(command, diagnostics),
            "manual_command_pending": True,
            "data": command,
            "diagnostics": diagnostics,
            "auth": _dashboard_auth_payload(),
        })

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("ORACLE_DASHBOARD_PORT", "8089")))
