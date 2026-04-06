"""Gemini-powered macro regime classifier for XAUUSD."""

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
_STATE_DIR = os.path.dirname(STATE_PATH) or "."
NEWS_CACHE_PATH = os.environ.get("XAUEX_NEWS_CACHE", os.path.join(_STATE_DIR, "news_calendar_cache.json"))
OUTPUT_PATH = os.environ.get(
    "XAUEX_MACRO_REGIME_OUTPUT",
    os.environ.get("MACRO_REGIME_PATH", "/var/lib/xauex/macro_regime.json"),
)
STALE_SECONDS = 30 * 60


def _parse_ts(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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
        if currency not in {"USD", "XAU"}:
            continue
        lines.append(
            f"- {ts.strftime('%Y-%m-%d %H:%MZ')} | {currency} | {impact} | {event.get('title', '')}"
        )
    return "\n".join(lines) if lines else "No relevant USD/XAU events in the next 24h."


def build_prompt(state: Optional[dict], news_cache: Optional[dict], now_utc: datetime, stale: bool) -> str:
    trend = (state or {}).get("trend", {})
    signal_history = (state or {}).get("signal_history", [])[:6]
    news_section = build_news_section(news_cache, now_utc)
    stale_note = "State is stale. Use the calendar context conservatively.\n" if stale else ""
    signals_text = "\n".join(
        f"- [{sig.get('time_utc')}] {sig.get('action')} {sig.get('gate_result')} {sig.get('pattern')}"
        for sig in signal_history
    ) or "- No recent signals."
    return f"""You are classifying the short-term macro regime for an automated XAUUSD trading bot.
Return JSON only. Do not wrap in markdown.

{stale_note}Current UTC time: {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}

Current trend snapshot:
- alignment: {trend.get('alignment', 'UNKNOWN')}
- reason: {trend.get('reason', 'UNKNOWN')}
- daily_ema_8: {trend.get('daily_ema_8')}
- daily_ema_21: {trend.get('daily_ema_21')}
- exec_ema_50: {trend.get('exec_ema_50')}
- exec_ema_200: {trend.get('exec_ema_200')}

Recent signals:
{signals_text}

Relevant Forex Factory calendar context:
{news_section}

Classify the next 6-12 hours for gold as one of:
- XAU_BULLISH
- XAU_BEARISH
- NEUTRAL

Use confidence from 0.0 to 1.0.
Set expires_utc no more than 12 hours ahead.

Respond with exact JSON keys:
{{
  "regime": "XAU_BULLISH|XAU_BEARISH|NEUTRAL",
  "confidence": 0.0,
  "block_new_entries_until_utc": null,
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
    output_path: str = OUTPUT_PATH,
) -> None:
    now_utc = datetime.now(timezone.utc)
    state = read_json_file(state_path)
    news_cache = read_json_file(news_cache_path)
    stale = is_state_stale(state, max_age_seconds=STALE_SECONDS)
    prompt = build_prompt(state, news_cache, now_utc, stale=stale)

    logger.info("[MACRO] Calling Gemini (%s)...", MODEL)
    response = call_claude(prompt, MODEL)
    parsed = _extract_json(response)

    expires_utc = parsed.get("expires_utc") or (now_utc + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = {
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "regime": parsed.get("regime", "NEUTRAL"),
        "confidence": float(parsed.get("confidence", 0.0)),
        "block_new_entries_until_utc": parsed.get("block_new_entries_until_utc"),
        "expires_utc": expires_utc,
        "summary": str(parsed.get("summary", "")).strip(),
        "catalysts": parsed.get("catalysts", []),
        "stale_state": stale,
    }
    atomic_write_json(output_path, result)
    logger.info("[MACRO] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as exc:
        logger.error("[MACRO] Failed: %s", exc)
        sys.exit(1)
