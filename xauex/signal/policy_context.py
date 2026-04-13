"""Official Fed policy context adapter for XAUEX signal decisions."""

from __future__ import annotations

from csv import DictReader
from datetime import date, datetime, timezone
from io import StringIO
import html
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_FRED_SERIES_IDS = {
    "effective_rate": "DFF",
    "target_upper": "DFEDTARU",
    "target_lower": "DFEDTARL",
}

_FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
_US_POLICY_TIMEZONE = ZoneInfo("America/New_York")

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def fetch_policy_context(*, config: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    current_time = now or datetime.now(timezone.utc)
    timeout_seconds = float(getattr(config, "source_timeout_seconds", 20.0) or 20.0)
    user_agent = str(getattr(config, "source_user_agent", "XAUEX-Signal/2.0 (+https://localhost)") or "XAUEX-Signal/2.0 (+https://localhost)")
    client = httpx.Client(
        timeout=timeout_seconds,
        headers={"User-Agent": user_agent},
        follow_redirects=True,
    )
    calendar_html: str | None = None
    effective_rate: float | None = None
    target_upper: float | None = None
    target_lower: float | None = None
    errors: list[str] = []
    try:
        try:
            calendar_html = _fetch_text(client, _FOMC_CALENDAR_URL)
        except Exception as exc:  # pragma: no cover - network/provider dependent
            errors.append(f"calendar: {exc}")
            logger.warning("[POLICY] Calendar fetch failed: %s", exc)

        for field, series_id in (
            ("effective_rate", _FRED_SERIES_IDS["effective_rate"]),
            ("target_upper", _FRED_SERIES_IDS["target_upper"]),
            ("target_lower", _FRED_SERIES_IDS["target_lower"]),
        ):
            try:
                value = _fetch_fred_latest_value(client, series_id)
            except Exception as exc:  # pragma: no cover - network/provider dependent
                errors.append(f"{field}: {exc}")
                logger.warning("[POLICY] FRED fetch failed for %s: %s", field, exc)
                continue
            if field == "effective_rate":
                effective_rate = value
            elif field == "target_upper":
                target_upper = value
            else:
                target_lower = value

        if calendar_html is None and effective_rate is None and target_upper is None and target_lower is None:
            return {
                "status": "unavailable",
                "source": "fed_fomc_calendar+fred",
                "summary": "Official Fed policy context is unavailable.",
            }

        next_fomc_date: datetime | None = None
        if calendar_html is not None:
            try:
                next_fomc_date = _parse_next_fomc_date(calendar_html, now=current_time)
            except Exception as exc:  # pragma: no cover - network/provider dependent
                errors.append(f"calendar_parse: {exc}")
                logger.warning("[POLICY] Calendar parse failed: %s", exc)
        payload = _build_policy_context_payload(
            next_fomc_date=next_fomc_date,
            effective_rate=effective_rate,
            target_lower=target_lower,
            target_upper=target_upper,
            now=current_time,
        )
        if not payload:
            return {
                "status": "unavailable",
                "source": "fed_fomc_calendar+fred",
                "summary": "Official Fed policy context is unavailable.",
            }
        payload["source"] = "fed_fomc_calendar+fred"
        if errors:
            payload["status"] = "warning"
            payload["available"] = True
            payload["summary"] = _policy_context_partial_summary(payload, errors)
        else:
            payload["status"] = "available"
            payload["available"] = True
            payload["summary"] = (
                f"Official Fed policy context available for {payload['next_fomc_date']}."
            )
        return payload
    finally:
        client.close()


def _derive_fomc_window_state(days_to_fomc: int) -> str:
    if days_to_fomc == 0:
        return "today"
    if days_to_fomc == -1:
        return "recent"
    if 1 <= days_to_fomc <= 3:
        return "approaching"
    return "normal"


def _build_policy_context_payload(
    *,
    next_fomc_date: datetime | date | str | None,
    effective_rate: float | None,
    target_lower: float | None,
    target_upper: float | None,
    now: datetime,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    if next_fomc_date is not None:
        if isinstance(next_fomc_date, str):
            next_date = datetime.strptime(next_fomc_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        elif isinstance(next_fomc_date, date) and not isinstance(next_fomc_date, datetime):
            next_date = datetime.combine(next_fomc_date, datetime.min.time(), tzinfo=timezone.utc)
        else:
            next_date = next_fomc_date.astimezone(timezone.utc)
        payload["next_fomc_date"] = next_date.strftime("%Y-%m-%d")
        days_to_fomc = (next_date.date() - _policy_date_basis(now)).days
        payload["days_to_fomc"] = days_to_fomc
        payload["fomc_window_state"] = _derive_fomc_window_state(days_to_fomc)

    if effective_rate is not None:
        payload["effective_fed_funds_rate"] = round(float(effective_rate), 4)
    if target_lower is not None:
        payload["target_lower"] = round(float(target_lower), 4)
    if target_upper is not None:
        payload["target_upper"] = round(float(target_upper), 4)
    if target_lower is not None and target_upper is not None:
        target_lower_value = round(float(target_lower), 4)
        target_upper_value = round(float(target_upper), 4)
        payload["target_mid"] = round((target_lower_value + target_upper_value) / 2.0, 4)
        if effective_rate is not None:
            effective_value = round(float(effective_rate), 4)
            payload["dff_minus_upper_bps"] = round((effective_value - target_upper_value) * 100.0, 1)
            payload["dff_minus_lower_bps"] = round((effective_value - target_lower_value) * 100.0, 1)
            payload["dff_minus_target_mid_bps"] = round((effective_value - payload["target_mid"]) * 100.0, 1)

    return payload


def _policy_context_partial_summary(payload: dict[str, Any], errors: list[str]) -> str:
    available_fields = []
    for key in ("next_fomc_date", "effective_fed_funds_rate", "target_upper", "target_lower"):
        if key in payload:
            available_fields.append(key)
    missing_components = []
    for error in errors:
        component, _, _ = error.partition(":")
        component = component.strip()
        if component:
            missing_components.append(component)
    available_part = ", ".join(available_fields) if available_fields else "no structured fields"
    missing_part = ", ".join(missing_components) if missing_components else "some upstream fields"
    return f"Official Fed policy context is partially available ({available_part}); missing {missing_part}."


def _normalize_policy_context(
    *,
    next_fomc_date: datetime | date | str,
    effective_rate: float,
    target_lower: float,
    target_upper: float,
    now: datetime,
) -> dict[str, Any]:
    if isinstance(next_fomc_date, str):
        next_date = datetime.strptime(next_fomc_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif isinstance(next_fomc_date, date) and not isinstance(next_fomc_date, datetime):
        next_date = datetime.combine(next_fomc_date, datetime.min.time(), tzinfo=timezone.utc)
    else:
        next_date = next_fomc_date.astimezone(timezone.utc)

    payload = _build_policy_context_payload(
        next_fomc_date=next_date,
        effective_rate=effective_rate,
        target_lower=target_lower,
        target_upper=target_upper,
        now=now,
    )

    return payload | {
        "next_fomc_date": next_date.strftime("%Y-%m-%d"),
        "effective_fed_funds_rate": payload.get("effective_fed_funds_rate", round(float(effective_rate), 4)),
        "target_lower": payload.get("target_lower", round(float(target_lower), 4)),
        "target_upper": payload.get("target_upper", round(float(target_upper), 4)),
        "target_mid": payload.get("target_mid", round((float(target_lower) + float(target_upper)) / 2.0, 4)),
        "dff_minus_upper_bps": payload.get(
            "dff_minus_upper_bps",
            round((float(effective_rate) - float(target_upper)) * 100.0, 1),
        ),
        "dff_minus_lower_bps": payload.get(
            "dff_minus_lower_bps",
            round((float(effective_rate) - float(target_lower)) * 100.0, 1),
        ),
        "dff_minus_target_mid_bps": payload.get(
            "dff_minus_target_mid_bps",
            round((float(effective_rate) - payload.get("target_mid", round((float(target_lower) + float(target_upper)) / 2.0, 4))) * 100.0, 1),
        ),
    }


def _policy_date_basis(now: datetime) -> date:
    return now.astimezone(_US_POLICY_TIMEZONE).date()


def _fetch_text(client: httpx.Client, url: str) -> str:
    response = client.get(url)
    response.raise_for_status()
    return response.text


def _fetch_fred_latest_value(client: httpx.Client, series_id: str) -> float:
    response = client.get(_FRED_CSV_URL.format(series_id=series_id))
    response.raise_for_status()
    reader = DictReader(StringIO(response.text))
    rows: list[tuple[str, float]] = []
    for row in reader:
        raw_value = str(row.get(series_id, "") or "").strip()
        date_value = str(row.get("DATE", row.get("observation_date", "")) or "").strip()
        if not raw_value or raw_value == ".":
            continue
        try:
            rows.append((date_value, float(raw_value)))
        except ValueError:
            continue
    if not rows:
        raise RuntimeError(f"Insufficient FRED rows for {series_id}")
    return rows[-1][1]


def _parse_next_fomc_date(calendar_html: str, *, now: datetime) -> datetime:
    lines = _html_to_lines(calendar_html)
    policy_date = _policy_date_basis(now)
    current_year: int | None = None
    current_month: int | None = None
    current_month_secondary: int | None = None
    candidates: list[datetime] = []

    for line in lines:
        year_match = re.search(r"(\d{4})", line)
        if year_match:
            current_year = int(year_match.group(1))
            month_primary, month_secondary = _parse_month_label(line)
            if month_primary is not None:
                current_month = month_primary
                current_month_secondary = month_secondary
            else:
                current_month = None
                current_month_secondary = None
            continue

        month_primary, month_secondary = _parse_month_label(line)
        if month_primary is not None:
            current_month = month_primary
            current_month_secondary = month_secondary
            continue

        if current_year is None or current_month is None:
            continue
        date_match = re.fullmatch(r"(\d{1,2})(?:-(\d{1,2}))?\*?", line)
        if not date_match:
            continue

        start_day = int(date_match.group(1))
        end_day = int(date_match.group(2) or date_match.group(1))
        if current_month_secondary is not None and end_day < start_day:
            candidate_month = current_month_secondary
            candidate_day = end_day
        else:
            candidate_month = current_month
            candidate_day = end_day
        candidate = datetime(current_year, candidate_month, candidate_day, tzinfo=timezone.utc)
        if candidate.date() >= policy_date:
            candidates.append(candidate)

    if not candidates:
        raise RuntimeError("No future FOMC dates found in Fed calendar")

    return min(candidates, key=lambda item: item.date())


def _parse_month_label(line: str) -> tuple[int | None, int | None]:
    tokens = [token for token in re.findall(r"[A-Za-z]+", line.lower()) if token]
    months: list[int] = []
    for token in tokens:
        month = _MONTHS.get(token)
        if month is not None and (not months or months[-1] != month):
            months.append(month)
    if not months:
        return None, None
    if len(months) == 1:
        return months[0], None
    return months[0], months[1]


def _html_to_lines(html_text: str) -> list[str]:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|h1|h2|h3|h4|h5|h6|li|tr|td|th|section|article)>", "\n", text)
    text = re.sub(r"(?i)<[^>]+>", " ", text)
    text = html.unescape(text)
    lines = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return lines
