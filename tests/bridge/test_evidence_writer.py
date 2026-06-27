import json
from pathlib import Path

from xauex.signal.evidence_writer import write_evidence_pack


def test_write_evidence_pack_creates_json(tmp_path: Path):
    target = tmp_path / "evidence.json"
    sibling = tmp_path / "other.json"
    sibling.write_text("keep", encoding="utf-8")
    write_evidence_pack(
        output_path=target,
        context_summary="Fed dovish, yields softer",
        recent_runs=[{"action": "BUY"}],
    )
    assert target.exists()
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_write_evidence_pack_persists_validator_and_market_snapshot(tmp_path: Path):
    target = tmp_path / "evidence.json"
    write_evidence_pack(
        output_path=target,
        context_summary="Fed dovish, yields softer",
        recent_runs=[{"action": "BUY"}],
        market_snapshot={"series": {"us10y_yield": {"value": 4.2, "bias": "BUY"}}},
        input_freshness={"context_age_seconds": 900},
        validator={"status": "reviewed", "consensus_state": "aligned"},
        estimated_total_cost_usd=0.00123,
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["market_snapshot"]["series"]["us10y_yield"]["bias"] == "BUY"
    assert payload["input_freshness"]["context_age_seconds"] == 900
    assert payload["validator"]["consensus_state"] == "aligned"
    assert payload["estimated_total_cost_usd"] == 0.00123


def test_write_evidence_pack_persists_dsa_sidecar_snapshot(tmp_path: Path):
    target = tmp_path / "evidence.json"
    write_evidence_pack(
        output_path=target,
        context_summary="Fed dovish, yields softer",
        recent_runs=[],
        dsa_sidecar={
            "enabled": True,
            "status": "ok",
            "symbol": "AAPL",
            "shadow_signal": {"action": "BUY", "shadow_only": True},
        },
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["dsa_sidecar"]["symbol"] == "AAPL"
    assert payload["dsa_sidecar"]["shadow_signal"]["action"] == "BUY"
