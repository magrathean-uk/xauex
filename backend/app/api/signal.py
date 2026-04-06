"""Signal API — exposes the latest MiroFish trading signal from cmd.json."""

import json
import os
from datetime import datetime, timezone

from flask import jsonify

from . import report_bp  # reuse existing blueprint

SIGNAL_PATH = os.getenv("SIGNAL_OUTPUT_PATH", "/var/lib/xauex/cmd.json")


@report_bp.route("/signal", methods=["GET"])
def get_signal():
    """Return the latest trading signal written by the bridge."""
    try:
        with open(SIGNAL_PATH, "r", encoding="utf-8") as f:
            cmd = json.load(f)
    except FileNotFoundError:
        return jsonify({"status": "no_signal", "message": "No signal yet — pipeline has not run today."}), 200
    except json.JSONDecodeError:
        return jsonify({"status": "error", "message": "Signal file is corrupt."}), 500

    sig = cmd.get("mirofish_signal", {})
    generated_at = cmd.get("generated_at_utc", "")

    # Compute staleness
    stale = False
    try:
        generated_dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - generated_dt).total_seconds() / 3600
        stale = age_hours > 30
    except Exception:
        pass

    action = sig.get("action", "HOLD")
    sl_dist = float(sig.get("stop_loss_usd", sig.get("stop_loss_distance", 0.0)) or 0.0)
    tp_dist = float(sig.get("take_profit_usd", sig.get("take_profit_distance", 0.0)) or 0.0)

    return jsonify({
        "status": "ok",
        "stale": stale,
        "generated_at_utc": generated_at,
        "symbol": sig.get("symbol", "XAUUSD"),
        "action": action,
        "confidence": sig.get("confidence", 0.0),
        "rationale": sig.get("reasoning", sig.get("rationale", "")),
        "stop_loss_distance": sl_dist,
        "take_profit_distance": tp_dist,
        "risk_reward": round(tp_dist / sl_dist, 2) if sl_dist > 0 else None,
        "llm_usage": sig.get("llm_usage"),
        "source": sig.get("source"),
        "kill_switch": cmd.get("kill_switch", False),
    })
