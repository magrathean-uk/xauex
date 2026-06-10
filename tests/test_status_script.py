from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_status_script_uses_passwordless_sudo_for_monit_summary(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dashboard_payload = {
        "data": {
            "signal": {"action": "HOLD", "generated_at_utc": "2026-06-10T12:00:00Z"},
            "diagnostics": {
                "overall_status": "healthy",
                "summary": "ok",
                "components": {"bot": {"state": "RUNNING"}, "quote": {"state": "live"}},
            },
            "account": {"signal_runs_taken_today": 1, "signal_runs_cap": 3, "trades_taken_today": 0, "trade_cap": 3},
        }
    }
    _write_executable(bin_dir / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(bin_dir / "journalctl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(bin_dir / "curl", f"#!/usr/bin/env bash\nprintf '%s\\n' {json.dumps(json.dumps(dashboard_payload))}\n")
    _write_executable(bin_dir / "monit", "#!/usr/bin/env bash\nexit 1\n")
    _write_executable(
        bin_dir / "sudo",
        """#!/usr/bin/env bash
if [[ "$1" == "-n" && "$2" == "true" ]]; then
  exit 0
fi
if [[ "$1" == "-n" && "$2" == "monit" && "$3" == "summary" ]]; then
  printf ' xauex-signal-stall               OK                          Program\\n'
  exit 0
fi
exit 1
""",
    )

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
    }
    proc = subprocess.run(
        ["bash", "status.sh"],
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )

    assert proc.returncode == 0
    assert "xauex-signal-stall" in proc.stdout
    assert "No XAUEX Monit checks found." not in proc.stdout
