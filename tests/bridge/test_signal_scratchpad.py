import json

from xauex.signal.scratchpad import (
    record_final_decision,
    record_stage_error,
    record_stage_result,
    record_stage_start,
)


def test_scratchpad_writes_valid_jsonl(tmp_path):
    path = tmp_path / "scratchpad.jsonl"

    record_stage_start(path, stage="market_analyst", prompt="Summarize the packet.")
    record_stage_result(
        path,
        stage="market_analyst",
        parsed={"summary": "Fresh bullish structure."},
        response_mode="json_schema",
        usage={"total_tokens": 42},
        retries=1,
    )
    record_final_decision(path, decision={"action": "BUY", "confidence": 0.62})

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [row["event"] for row in rows] == ["stage_start", "stage_result", "final_decision"]
    assert rows[0]["stage"] == "market_analyst"
    assert rows[1]["parsed"]["summary"] == "Fresh bullish structure."
    assert rows[1]["usage"]["total_tokens"] == 42
    assert rows[2]["decision"]["action"] == "BUY"


def test_scratchpad_logging_failure_is_non_fatal(tmp_path):
    directory_path = tmp_path / "not_a_file"
    directory_path.mkdir()

    record_stage_start(directory_path, stage="market_analyst", prompt="This path cannot be opened as a file.")
    record_stage_result(directory_path, stage="market_analyst", parsed={"summary": "ignored"})

    assert directory_path.is_dir()


def test_scratchpad_records_stage_errors(tmp_path):
    path = tmp_path / "scratchpad.jsonl"

    record_stage_error(path, stage="risk_reviewer", error="provider timeout", response_mode="json_object", retries=2)

    row = json.loads(path.read_text(encoding="utf-8").strip())

    assert row["event"] == "stage_error"
    assert row["stage"] == "risk_reviewer"
    assert row["error"] == "provider timeout"
    assert row["response_mode"] == "json_object"
    assert row["retries"] == 2
