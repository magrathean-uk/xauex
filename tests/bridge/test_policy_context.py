from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from xauex.signal.policy_context import (
    _derive_fomc_window_state,
    _parse_next_fomc_date,
    _normalize_policy_context,
    fetch_policy_context,
)


def test_derive_fomc_window_state_maps_distance_to_label():
    assert _derive_fomc_window_state(3) == "approaching"
    assert _derive_fomc_window_state(2) == "approaching"
    assert _derive_fomc_window_state(1) == "approaching"
    assert _derive_fomc_window_state(0) == "today"
    assert _derive_fomc_window_state(-1) == "recent"
    assert _derive_fomc_window_state(4) == "normal"


def test_normalize_policy_context_derives_normal_case_fields():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    next_fomc_date = datetime(2026, 5, 5, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=next_fomc_date,
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["next_fomc_date"] == "2026-05-05"
    assert payload["effective_fed_funds_rate"] == 4.5
    assert payload["target_mid"] == 4.375
    assert payload["dff_minus_upper_bps"] == 0.0
    assert payload["dff_minus_lower_bps"] == 25.0
    assert payload["dff_minus_target_mid_bps"] == 12.5
    assert payload["fomc_window_state"] == "normal"


def test_normalize_policy_context_marks_one_day_approaching():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=datetime(2026, 4, 26, tzinfo=timezone.utc),
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["fomc_window_state"] == "approaching"


def test_normalize_policy_context_marks_two_days_approaching():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=datetime(2026, 4, 27, tzinfo=timezone.utc),
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["fomc_window_state"] == "approaching"


def test_normalize_policy_context_marks_three_days_approaching():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=datetime(2026, 4, 28, tzinfo=timezone.utc),
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["fomc_window_state"] == "approaching"


def test_normalize_policy_context_marks_today():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=datetime(2026, 4, 25, tzinfo=timezone.utc),
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["fomc_window_state"] == "today"


def test_normalize_policy_context_marks_recent():
    now = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    payload = _normalize_policy_context(
        next_fomc_date=datetime(2026, 4, 24, tzinfo=timezone.utc),
        effective_rate=4.5,
        target_lower=4.25,
        target_upper=4.5,
        now=now,
    )

    assert payload["fomc_window_state"] == "recent"


def test_fetch_policy_context_uses_fred_and_calendar_when_available(monkeypatch):
    calendar_html = """
    <html>
      <body>
        <h4>2026 FOMC Meetings</h4>
        <p>January</p>
        <p>27-28</p>
      </body>
    </html>
    """
    fred_csv_by_series = {
        "DFF": "DATE,DFF\n2026-01-27,4.50\n2026-01-28,4.50\n",
        "DFEDTARU": "DATE,DFEDTARU\n2026-01-27,4.50\n2026-01-28,4.50\n",
        "DFEDTARL": "DATE,DFEDTARL\n2026-01-27,4.25\n2026-01-28,4.25\n",
    }

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.requested_urls: list[str] = []

        def get(self, url: str):
            self.requested_urls.append(url)
            if "fomccalendars.htm" in url:
                return FakeResponse(calendar_html)
            for series_id, csv_text in fred_csv_by_series.items():
                if f"id={series_id}" in url:
                    return FakeResponse(csv_text)
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 1, 26, 12, 0, tzinfo=timezone.utc))

    assert payload["status"] == "available"
    assert payload["source"] == "fed_fomc_calendar+fred"
    assert payload["next_fomc_date"] == "2026-01-28"
    assert payload["effective_fed_funds_rate"] == 4.5
    assert payload["target_mid"] == 4.375
    assert payload["dff_minus_upper_bps"] == 0.0
    assert payload["dff_minus_lower_bps"] == 25.0
    assert payload["dff_minus_target_mid_bps"] == 12.5
    assert payload["fomc_window_state"] == "approaching"


def test_fetch_policy_context_uses_second_month_day_for_cross_month_meeting(monkeypatch):
    calendar_html = """
    <html>
      <body>
        <h4>2024 FOMC Meetings</h4>
        <p>Apr/May</p>
        <p>30-1</p>
      </body>
    </html>
    """
    fred_csv_by_series = {
        "DFF": "DATE,DFF\n2024-05-01,5.25\n",
        "DFEDTARU": "DATE,DFEDTARU\n2024-05-01,5.50\n",
        "DFEDTARL": "DATE,DFEDTARL\n2024-05-01,5.25\n",
    }

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                return FakeResponse(calendar_html)
            for series_id, csv_text in fred_csv_by_series.items():
                if f"id={series_id}" in url:
                    return FakeResponse(csv_text)
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2024, 4, 29, 12, 0, tzinfo=timezone.utc))

    assert payload["status"] == "available"
    assert payload["next_fomc_date"] == "2024-05-01"


def test_parse_next_fomc_date_keeps_same_us_policy_date_after_midnight_utc():
    calendar_html = """
    <html>
      <body>
        <h4>2026 FOMC Meetings</h4>
        <p>April</p>
        <p>27</p>
      </body>
    </html>
    """

    next_fomc_date = _parse_next_fomc_date(
        calendar_html,
        now=datetime(2026, 4, 28, 0, 30, tzinfo=timezone.utc),
    )

    assert next_fomc_date.date() == datetime(2026, 4, 27, tzinfo=timezone.utc).date()


def test_fetch_policy_context_accepts_config(monkeypatch):
    captured = {}

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                return FakeResponse("<h4>2026 FOMC Meetings</h4><p>Apr/May</p><p>27-28</p>")
            if "id=DFF" in url:
                return FakeResponse("DATE,DFF\n2026-04-25,4.50\n")
            if "id=DFEDTARU" in url:
                return FakeResponse("DATE,DFEDTARU\n2026-04-25,4.50\n")
            if "id=DFEDTARL" in url:
                return FakeResponse("DATE,DFEDTARL\n2026-04-25,4.25\n")
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(
        config=SimpleNamespace(source_timeout_seconds=7, source_user_agent="PolicyTest/1.0"),
        now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
    )

    assert captured["timeout"] == 7
    assert captured["headers"]["User-Agent"] == "PolicyTest/1.0"
    assert payload["status"] == "available"
    assert payload["next_fomc_date"] == "2026-04-28"


def test_fetch_policy_context_returns_warning_for_partial_upstream_failure(monkeypatch):
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                raise RuntimeError("calendar temporarily unavailable")
            if "id=DFF" in url:
                return FakeResponse("DATE,DFF\n2026-04-25,4.50\n")
            if "id=DFEDTARU" in url:
                return FakeResponse("DATE,DFEDTARU\n2026-04-25,4.50\n")
            if "id=DFEDTARL" in url:
                return FakeResponse("DATE,DFEDTARL\n2026-04-25,4.25\n")
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc))

    assert payload["status"] == "warning"
    assert payload["source"] == "fed_fomc_calendar+fred"
    assert payload["summary"]
    assert payload["effective_fed_funds_rate"] == 4.5
    assert payload["target_mid"] == 4.375


def test_fetch_policy_context_keeps_fomc_day_on_us_date_before_midnight_utc(monkeypatch):
    calendar_html = """
    <html>
      <body>
        <h4>2026 FOMC Meetings</h4>
        <p>April</p>
        <p>28</p>
      </body>
    </html>
    """
    fred_csv_by_series = {
        "DFF": "DATE,DFF\n2026-04-28,4.50\n",
        "DFEDTARU": "DATE,DFEDTARU\n2026-04-28,4.50\n",
        "DFEDTARL": "DATE,DFEDTARL\n2026-04-28,4.25\n",
    }

    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                return FakeResponse(calendar_html)
            for series_id, csv_text in fred_csv_by_series.items():
                if f"id={series_id}" in url:
                    return FakeResponse(csv_text)
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 28, 0, 30, tzinfo=timezone.utc))

    assert payload["status"] == "available"
    assert payload["next_fomc_date"] == "2026-04-28"
    assert payload["days_to_fomc"] == 1
    assert payload["fomc_window_state"] == "approaching"


def test_fetch_policy_context_returns_warning_when_only_target_bounds_succeed(monkeypatch):
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                raise RuntimeError("calendar temporarily unavailable")
            if "id=DFF" in url:
                raise RuntimeError("dff temporarily unavailable")
            if "id=DFEDTARU" in url:
                return FakeResponse("DATE,DFEDTARU\n2026-04-25,4.50\n")
            if "id=DFEDTARL" in url:
                return FakeResponse("DATE,DFEDTARL\n2026-04-25,4.25\n")
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc))

    assert payload["status"] == "warning"
    assert payload["source"] == "fed_fomc_calendar+fred"
    assert payload["target_lower"] == 4.25
    assert payload["target_upper"] == 4.5
    assert payload["target_mid"] == 4.375
    assert "partial" in payload["summary"]


def test_fetch_policy_context_unparseable_calendar_html_does_not_crash(monkeypatch):
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                return FakeResponse("<html><body>no usable meeting dates here</body></html>")
            if "id=DFF" in url:
                return FakeResponse("DATE,DFF\n2026-04-25,4.50\n")
            if "id=DFEDTARU" in url:
                return FakeResponse("DATE,DFEDTARU\n2026-04-25,4.50\n")
            if "id=DFEDTARL" in url:
                return FakeResponse("DATE,DFEDTARL\n2026-04-25,4.25\n")
            raise AssertionError(f"unexpected url: {url}")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc))

    assert payload["status"] == "warning"
    assert payload["source"] == "fed_fomc_calendar+fred"
    assert payload["effective_fed_funds_rate"] == 4.5
    assert payload["target_mid"] == 4.375
    assert "summary" in payload


def test_fetch_policy_context_unparseable_calendar_without_fred_returns_exact_fallback(monkeypatch):
    class FakeResponse:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            if "fomccalendars.htm" in url:
                return FakeResponse("<html><body>no usable meeting dates here</body></html>")
            raise RuntimeError("fred unavailable")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FakeClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc))

    assert payload == {
        "status": "unavailable",
        "source": "fed_fomc_calendar+fred",
        "summary": "Official Fed policy context is unavailable.",
    }


def test_fetch_policy_context_returns_graceful_fallback_when_unavailable(monkeypatch):
    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, url: str):
            raise RuntimeError("network down")

        def close(self):
            return None

    monkeypatch.setattr("xauex.signal.policy_context.httpx.Client", FailingClient)

    payload = fetch_policy_context(now=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc))

    assert payload == {
        "status": "unavailable",
        "source": "fed_fomc_calendar+fred",
        "summary": "Official Fed policy context is unavailable.",
    }
