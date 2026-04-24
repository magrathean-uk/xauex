from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = Path("ops/monitoring/check_xauex_runtime.sh")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def test_runtime_monitor_suppresses_recent_transition_alert(tmp_path: Path) -> None:
    systemctl = tmp_path / "systemctl"
    probe = tmp_path / "monit_probe.py"
    uptime = tmp_path / "uptime"

    _write_executable(
        systemctl,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "show" || "${2:-}" != "xauex.service" ]]; then
  exit 99
fi
prop="${4#*=}"
case "$prop" in
  ActiveState) echo "deactivating" ;;
  SubState) echo "stop-sigterm" ;;
  StateChangeTimestampMonotonic) echo "100000000" ;;
  *) echo "" ;;
esac
""",
    )
    _write_executable(
        probe,
        """#!/usr/bin/env python3
raise SystemExit(91)
""",
    )
    uptime.write_text("150.00 0.00\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "SYSTEMCTL_BIN": str(systemctl),
            "MONIT_PROBE_BIN": str(probe),
            "PROC_UPTIME_PATH": str(uptime),
            "XAUEX_MONIT_TRANSITION_GRACE_SECONDS": "120",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert "mode=transition-grace" in result.stdout
    assert "service=deactivating/stop-sigterm" in result.stdout


def test_runtime_monitor_falls_through_when_transition_is_old(tmp_path: Path) -> None:
    systemctl = tmp_path / "systemctl"
    probe = tmp_path / "monit_probe.py"
    uptime = tmp_path / "uptime"

    _write_executable(
        systemctl,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" != "show" || "${2:-}" != "xauex.service" ]]; then
  exit 99
fi
prop="${4#*=}"
case "$prop" in
  ActiveState) echo "deactivating" ;;
  SubState) echo "stop-sigterm" ;;
  StateChangeTimestampMonotonic) echo "100000000" ;;
  *) echo "" ;;
esac
""",
    )
    _write_executable(
        probe,
        """#!/usr/bin/env python3
import sys
print("probe-called")
raise SystemExit(17)
""",
    )
    uptime.write_text("500.00 0.00\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "SYSTEMCTL_BIN": str(systemctl),
            "MONIT_PROBE_BIN": str(probe),
            "PROC_UPTIME_PATH": str(uptime),
            "XAUEX_MONIT_TRANSITION_GRACE_SECONDS": "120",
        }
    )
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 17
    assert "probe-called" in result.stdout
