from pathlib import Path

from bridge.evidence_writer import write_evidence_pack


def test_write_evidence_pack_creates_json(tmp_path: Path):
    target = tmp_path / "evidence.json"
    write_evidence_pack(
        output_path=target,
        context_summary="Fed dovish, yields softer",
        recent_runs=[{"action": "BUY"}],
    )
    assert target.exists()
