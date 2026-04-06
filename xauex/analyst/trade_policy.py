"""Gemini-powered trade policy generator for XAUUSD."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from analyst._utils import atomic_write_json, call_claude, is_state_stale, read_json_file, utcnow_str

logger = logging.getLogger(__name__)

MODEL = "gemini-3.1-pro-preview"
STATE_PATH = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
NEWS_CACHE_PATH = os.environ.get("XAUEX_NEWS_CACHE", "/var/lib/xauex/news_calendar_cache.json")
MACRO_PATH = os.environ.get("XAUEX_MACRO_REGIME_OUTPUT", os.environ.get("MACRO_REGIME_PATH", "/var/lib/xauex/macro_regime.json"))
OUTPUT_PATH = os.environ.get("XAUEX_TRADE_POLICY_OUTPUT", os.environ.get("TRADE_POLICY_PATH", "/var/lib/xauex/trade_policy.json"))
STALE_SECONDS = 30 * 60


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _load_json(path: str) -> Optional[dict]:
    return read_json_file(path)


def build_news_section(news_cache: Optional[dict], now_utc: datetime) -> str:
    if not news_cache:
        return "News calendar unavailable."
    events = news_cache.get("events", [])
    horizon = now_utc + timedelta(hours=24)
    lines = []
    for event in events:
        try:
            ts = datetime.fromisoformat(event["time_utc"].replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if ts < now_utc - timedelta(hours=6) or ts > horizon:
            continue
        currency = str(event.get("currency", "")).upper()
        impact = str(event.get("impact", "")).upper()
        lines.append(f"- {ts.strftime('%Y-%m-%d %H:%MZ')} | {currency} | {impact} | {event.get('title', '')}")
    return "\n".join(lines) if lines else "No relevant calendar events in the next 24h."


def build_prompt(state: Optional[dict], news_cache: Optional[dict], macro_regime: Optional[dict], now_utc: datetime, stale: bool) -> str:
    trend = (state or {}).get("trend", {})
    signal_history = (state or {}).get("signal_history", [])[:8]
    news_section = build_news_section(news_cache, now_utc)
    macro_text = json.dumps(macro_regime or {}, indent=2)
    stale_note = "State is stale. Be conservative.\n" if stale else ""
    signals_text = "\n".join(
        f"- [{sig.get('time_utc')}] {sig.get('action')} {sig.get('gate_result')} {sig.get('pattern')}"
        for sig in signal_history
    ) or "- No recent signals."
    return f"""You are a trade policy engine for an automated XAUUSD bot.
Return JSON only.

{stale_note}Current UTC time: {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}

Trend snapshot:
- alignment: {trend.get('alignment', 'UNKNOWN')}
- reason: {trend.get('reason', 'UNKNOWN')}
- daily_ema_8: {trend.get('daily_ema_8')}
- daily_ema_21: {trend.get('daily_ema_21')}
- exec_ema_50: {trend.get('exec_ema_50')}
- exec_ema_200: {trend.get('exec_ema_200')}

Macro regime:
{macro_text}

Recent signals:
{signals_text}

Calendar context:
{news_section}

Create a policy for the next 6-12 hours.
Use these mode values:
- AGGRESSIVE
- NORMAL
- CAUTIOUS
- BLOCK

Use these direction values:
- LONG_ONLY
- SHORT_ONLY
- BOTH
- NONE

Respond with exact JSON keys:
{{
  "mode": "AGGRESSIVE|NORMAL|CAUTIOUS|BLOCK",
  "direction": "LONG_ONLY|SHORT_ONLY|BOTH|NONE",
  "aggressiveness": 0.0,
  "allow_reentry": true,
  "pullback_zone_multiplier": 1.0,
  "sl_buffer_multiplier": 1.0,
  "tp_rr_multiplier": 1.0,
  "block_new_entries_until_utc": "YYYY-MM-DDTHH:MM:SSZ or null",
  "expires_utc": "YYYY-MM-DDTHH:MM:SSZ",
  "summary": "one short sentence",
  "catalysts": ["short phrase", "short phrase"]
}}"""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in LLM response")
    return json.loads(text[start : end + 1])


def run(
    state_path: str = STATE_PATH,
    news_cache_path: str = NEWS_CACHE_PATH,
    macro_regime_path: str = MACRO_PATH,
    output_path: str = OUTPUT_PATH,
) -> None:
    now_utc = datetime.now(timezone.utc)
    state = _load_json(state_path)
    news_cache = _load_json(news_cache_path)
    macro_regime = _load_json(macro_regime_path)
    stale = is_state_stale(state, max_age_seconds=STALE_SECONDS)
    prompt = build_prompt(state, news_cache, macro_regime, now_utc, stale=stale)

    logger.info("[POLICY] Calling Gemini (%s)...", MODEL)
    response = call_claude(prompt, MODEL)
    parsed = _extract_json(response)

    expires_utc = parsed.get("expires_utc") or (now_utc + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "mode": str(parsed.get("mode", "NORMAL")).upper(),
        "direction": str(parsed.get("direction", "BOTH")).upper(),
        "aggressiveness": float(parsed.get("aggressiveness", 0.5)),
        "allow_reentry": bool(parsed.get("allow_reentry", True)),
        "pullback_zone_multiplier": float(parsed.get("pullback_zone_multiplier", 1.0)),
        "sl_buffer_multiplier": float(parsed.get("sl_buffer_multiplier", 1.0)),
        "tp_rr_multiplier": float(parsed.get("tp_rr_multiplier", 1.0)),
        "block_new_entries_until_utc": parsed.get("block_new_entries_until_utc"),
        "expires_utc": expires_utc,
        "summary": str(parsed.get("summary", "")).strip(),
        "catalysts": parsed.get("catalysts", []),
        "stale_state": stale,
    }
    atomic_write_json(output_path, result)
    logger.info("[POLICY] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as exc:
        logger.error("[POLICY] Failed: %s", exc)
        sys.exit(1)
