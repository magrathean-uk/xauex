from pathlib import Path

from bridge.history_cache import load_recent_signal_history


def test_load_recent_signal_history_reads_latest_entries(tmp_path: Path):
    cmd = tmp_path / "cmd_history.json"
    cmd.write_text(
        '[{"action":"BUY","confidence":0.61},{"action":"SELL","confidence":0.55}]',
        encoding="utf-8",
    )
    rows = load_recent_signal_history(cmd)
    assert len(rows) == 2
    assert rows[0]["action"] == "BUY"
