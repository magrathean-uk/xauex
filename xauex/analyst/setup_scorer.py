"""Setup scorer — polls every 5 min Mon-Fri via cron."""

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

from xauex.analyst._utils import (
    default_model,
    append_to_json_list,
    call_claude,
    is_state_stale,
    load_cursor,
    read_json_file,
    save_cursor,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = default_model()
STALE_SECONDS = 10 * 60

STATE_PATH = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
CURSOR_PATH = os.environ.get("XAUEX_SCORER_CURSOR", "/var/lib/xauex/scorer_cursor.json")
SCORES_PATH = os.environ.get("XAUEX_SCORES_OUTPUT", "/var/lib/xauex/setup_scores.json")

_DEFAULT_CURSOR = {"last_signal_ts": None}


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def find_new_signals(signals: List[Dict], cursor: Dict) -> List[Dict]:
    """Return signals with time_utc strictly after cursor['last_signal_ts'], sorted oldest-first."""
    last_ts = _parse_ts(cursor.get("last_signal_ts"))
    result = []
    for s in signals:
        sig_ts = _parse_ts(s.get("time_utc"))
        if sig_ts is None:
            continue
        if last_ts is None or sig_ts > last_ts:
            result.append(s)
    return sorted(result, key=lambda x: x.get("time_utc", ""))


def build_score_prompt(signal: Dict, trend: Optional[Dict] = None) -> str:
    """Build the analyst prompt for scoring a single setup."""
    pattern = signal.get("pattern", "UNKNOWN")
    level = signal.get("level_checked", "?")
    gate = signal.get("gate_result", "?")
    action = signal.get("action", "?")
    ts = signal.get("time_utc", "?")
    strategy = signal.get("strategy_mode", "?")
    trend_bias = (trend or {}).get("bias", "UNKNOWN")

    return f"""You are a trading setup quality analyst for an automated XAUUSD bot. Score this trade setup using the rubric below.

SETUP:
  Time: {ts}
  Pattern: {pattern}
  HTF Level: {level}
  Gate result: {gate}
  Action taken: {action}
  Strategy: {strategy}
  Trend bias (D1/H1 EMA): {trend_bias}

RUBRIC (score each dimension 0-2, max total 10):
  1. Pattern quality — clean textbook {pattern} vs marginal/borderline
  2. HTF level quality — major weekly/monthly confluence vs minor level
  3. Session alignment — London or NY open vs mid-session
  4. Trend alignment — pattern direction matches {trend_bias} bias
  5. Gate clarity — clean pass (OK) vs marginal or blocked

Respond in this format:
Score: X/10
Breakdown: [dimension-by-dimension brief explanation]
Summary: [one sentence overall assessment]"""


def run(
    state_path: str = STATE_PATH,
    cursor_path: str = CURSOR_PATH,
    scores_path: str = SCORES_PATH,
) -> None:
    state = read_json_file(state_path)
    if is_state_stale(state, max_age_seconds=STALE_SECONDS):
        logger.warning("[SCORER] state.json stale or missing — skipping.")
        return

    signals = state.get("signal_history", [])
    if not signals:
        logger.info("[SCORER] No signals in history.")
        return

    cursor = load_cursor(cursor_path, default=_DEFAULT_CURSOR)
    new_signals = find_new_signals(signals, cursor)
    if not new_signals:
        logger.info("[SCORER] No new signals to score.")
        return

    trend = state.get("trend", {})
    latest_ts = cursor.get("last_signal_ts")

    for signal in new_signals:
        ts = signal.get("time_utc", "")
        logger.info("[SCORER] Scoring signal at %s...", ts)
        prompt = build_score_prompt(signal, trend=trend)
        score_text = call_claude(prompt, MODEL)

        entry = {
            "signal_ts": ts,
            "scored_at_utc": utcnow_str(),
            "model": MODEL,
            "signal": signal,
            "breakdown": score_text,
        }
        append_to_json_list(scores_path, entry)

        if not latest_ts or ts > latest_ts:
            latest_ts = ts

    save_cursor(cursor_path, {"last_signal_ts": latest_ts})
    logger.info("[SCORER] Scored %d new signal(s).", len(new_signals))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[SCORER] Failed: %s", e)
        sys.exit(1)
