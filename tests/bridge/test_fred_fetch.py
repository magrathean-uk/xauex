from datetime import datetime, timezone

import pytest

from xauex.signal.fred_fetch import (
    fetch_fred_rows,
    fred_fetch_retries,
    fred_request_url,
    parse_fred_rows,
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.requested_urls: list[str] = []

    def get(self, url: str):
        self.requested_urls.append(url)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_fred_request_url_bounds_lookback_with_cosd():
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    url = fred_request_url("DGS10", now_utc=now)
    assert url.startswith("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10")
    assert "&cosd=2026-04-27" in url


def test_fred_request_url_switches_to_api_endpoint_with_key():
    now = datetime(2026, 6, 11, tzinfo=timezone.utc)
    url = fred_request_url("DGS10", api_key="secret", now_utc=now)
    assert url.startswith("https://api.stlouisfed.org/fred/series/observations")
    assert "series_id=DGS10" in url
    assert "api_key=secret" in url
    assert "observation_start=2026-04-27" in url


def test_parse_fred_rows_csv_skips_missing_values():
    text = "DATE,DGS10\n2026-06-09,4.21\n2026-06-10,.\n2026-06-11,4.25\n"
    rows = parse_fred_rows(text, "DGS10", api_payload=False)
    assert rows == [("2026-06-09", 4.21), ("2026-06-11", 4.25)]


def test_parse_fred_rows_api_payload():
    text = (
        '{"observations": ['
        '{"date": "2026-06-10", "value": "4.21"},'
        '{"date": "2026-06-11", "value": "."}'
        "]}"
    )
    rows = parse_fred_rows(text, "DGS10", api_payload=True)
    assert rows == [("2026-06-10", 4.21)]


def test_fetch_fred_rows_retries_once_then_succeeds(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("FRED_FETCH_RETRIES", "1")
    sleeps: list[float] = []
    monkeypatch.setattr("xauex.signal.fred_fetch.time.sleep", sleeps.append)
    client = _FakeClient(
        [
            RuntimeError("timeout"),
            _FakeResponse("DATE,DGS10\n2026-06-11,4.25\n"),
        ]
    )

    rows = fetch_fred_rows(client, "DGS10")

    assert rows == [("2026-06-11", 4.25)]
    assert len(client.requested_urls) == 2
    assert len(sleeps) == 1
    assert 2.0 <= sleeps[0] <= 5.0


def test_fetch_fred_rows_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setenv("FRED_FETCH_RETRIES", "1")
    monkeypatch.setattr("xauex.signal.fred_fetch.time.sleep", lambda _value: None)
    client = _FakeClient([RuntimeError("down"), RuntimeError("still down")])

    with pytest.raises(RuntimeError, match="FRED fetch failed for DGS10"):
        fetch_fred_rows(client, "DGS10")
    assert len(client.requested_urls) == 2


def test_fred_fetch_retries_defaults_to_one(monkeypatch):
    monkeypatch.delenv("FRED_FETCH_RETRIES", raising=False)
    assert fred_fetch_retries() == 1
    monkeypatch.setenv("FRED_FETCH_RETRIES", "0")
    assert fred_fetch_retries() == 0
    monkeypatch.setenv("FRED_FETCH_RETRIES", "junk")
    assert fred_fetch_retries() == 1
