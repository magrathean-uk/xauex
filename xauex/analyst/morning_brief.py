"""Morning brief — runs at 07:45 Mon-Fri via cron."""

import logging
import os
import sys
from datetime import datetime, timezone

from xauex.analyst._utils import (
    default_model,
    atomic_write_json,
    call_claude,
    is_state_stale,
    read_json_file,
    utcnow_str,
)

logger = logging.getLogger(__name__)

MODEL = default_model()
STATE_PATH = os.environ.get("XAUEX_STATE_FILE", os.environ.get("STATE_FILE_PATH", "/var/lib/xauex/state.json"))
_STATE_DIR = os.path.dirname(STATE_PATH) or "."
NEWS_CACHE_PATH = os.environ.get(
    "XAUEX_NEWS_CACHE", os.path.join(_STATE_DIR, "news_calendar_cache.json")
)
OUTPUT_PATH = os.environ.get(
    "XAUEX_BRIEF_OUTPUT", "/var/lib/xauex/morning_brief.json"
)
STALE_SECONDS = 30 * 60  # 30 minutes
NEWS_STALE_HOURS = 24


def build_news_section(news_cache_path: str) -> str:
    """Read news_calendar_cache.json and return a text section of today's high-impact USD events."""
    cache = read_json_file(news_cache_path)
    if cache is None:
        return "News calendar: unavailable (cache file missing)."

    refresh_date = cache.get("last_refresh_date", "")
    try:
        refresh_dt = datetime.strptime(refresh_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - refresh_dt).total_seconds() / 3600
        if age_hours > NEWS_STALE_HOURS:
            return f"News calendar: stale (last refreshed {refresh_date})."
    except ValueError:
        return "News calendar: stale (refresh date unparseable)."

    events = cache.get("events", [])
    usd_high = [e for e in events if e.get("currency") == "USD" and e.get("impact", "").upper() == "HIGH"]
    if not usd_high:
        return "No high-impact USD events today."

    lines = ["High-impact USD events today:"]
    for e in sorted(usd_high, key=lambda x: x.get("time_utc", "")):
        lines.append(f"  - {e['title']} at {e['time_utc']}")
    return "\n".join(lines)


def build_prompt(state: dict, news_section: str, stale: bool = False) -> str:
    """Build the analyst prompt for the morning brief."""
    meta = state.get("meta", {})
    account = state.get("account", {})
    risk = state.get("risk", {})
    levels = state.get("levels", {})
    positions = state.get("open_positions", [])
    signals = state.get("signal_history", [])[:5]

    stale_warning = (
        "\n⚠️ WARNING: Bot state data is STALE (>30 minutes old). Bot may be stopped.\n"
        if stale else ""
    )

    positions_text = "None" if not positions else "\n".join(
        f"  - {p['direction']} {p['lot_size']} lots @ {p['entry_price']} "
        f"SL={p['stop_loss']} TP={p['take_profit']} PnL={p.get('unrealised_pnl', 0):.2f} "
        f"Pattern={p['pattern']} Level={p['level']}"
        for p in positions
    )

    signals_text = "None" if not signals else "\n".join(
        f"  - [{s['time_utc']}] {s.get('pattern','?')} @ {s.get('level_checked','?')} "
        f"→ {s.get('action','?')} ({s.get('gate_result','?')})"
        for s in signals
    )

    wk = levels.get("weekly", {})
    mn = levels.get("monthly", {})

    return f"""You are a trading operations analyst for an automated XAUUSD bot. Produce a concise morning brief covering the key points an operator needs before the London session opens.{stale_warning}

BOT STATUS: {meta.get('bot_status', 'UNKNOWN')}
ACCOUNT: balance={account.get('balance', '?')} equity={account.get('equity', '?')}
RISK: consecutive_losses_today={risk.get('consecutive_losses_today', 0)} weekly_pnl={risk.get('weekly_pnl', 0):.2f} weekly_halted={risk.get('weekly_halted', False)} daily_halted={risk.get('daily_halted', False)}

HTF LEVELS:
  Weekly: O={wk.get('open')} H={wk.get('high')} L={wk.get('low')} C={wk.get('close')}
  Monthly: O={mn.get('open')} H={mn.get('high')} L={mn.get('low')} C={mn.get('close')}

OPEN POSITIONS:
{positions_text}

LAST 5 SIGNALS:
{signals_text}

{news_section}

Produce a short morning brief (5-10 sentences) covering: current positioning, key levels to watch, risk status, and any upcoming news events that could affect XAUUSD today. Be direct and operational."""


def run(
    state_path: str = STATE_PATH,
    news_cache_path: str = NEWS_CACHE_PATH,
    output_path: str = OUTPUT_PATH,
) -> None:
    """Generate morning brief and write to output file."""
    state = read_json_file(state_path)
    stale = is_state_stale(state, max_age_seconds=STALE_SECONDS)

    if state is None:
        logger.warning("[BRIEF] state.json missing at %s — generating stale-warning brief.", state_path)
        state = {"meta": {"bot_status": "UNKNOWN", "last_updated_utc": "N/A"}, "account": {}, "risk": {}, "levels": {}, "open_positions": [], "signal_history": []}
        stale = True

    news_section = build_news_section(news_cache_path)
    prompt = build_prompt(state, news_section, stale=stale)

    logger.info("[BRIEF] Calling analyst model (%s)...", MODEL)
    brief_text = call_claude(prompt, MODEL)

    result = {
        "generated_at_utc": utcnow_str(),
        "model": MODEL,
        "stale_state": stale,
        "brief": brief_text,
    }
    atomic_write_json(output_path, result)
    logger.info("[BRIEF] Written to %s", output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run()
    except Exception as e:
        logger.error("[BRIEF] Failed: %s", e)
        sys.exit(1)
