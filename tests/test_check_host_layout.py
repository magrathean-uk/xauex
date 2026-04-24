from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = Path("ops/check_host_layout.sh")


def _write_file(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _build_base_env(tmp_path: Path) -> dict[str, str]:
    caddy = tmp_path / "bin" / "caddy"
    ss_bin = tmp_path / "bin" / "ss"
    systemd_dir = tmp_path / "systemd"
    caddyfile = tmp_path / "etc" / "caddy" / "Caddyfile"
    caddy_snippet = tmp_path / "etc" / "caddy" / "Caddyfile.d" / "xauex-dashboard.caddy"
    compose_path = tmp_path / "root" / "pihole" / "docker-compose.yml"

    _write_file(
        caddy,
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "validate" ]]; then
  exit 0
fi
exit 99
""",
        executable=True,
    )
    _write_file(
        ss_bin,
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'EOF'
State  Recv-Q Send-Q Local Address:Port Peer Address:Port
LISTEN 0      4096   127.0.0.1:8089   0.0.0.0:*
LISTEN 0      4096   10.8.0.1:80      0.0.0.0:*
LISTEN 0      4096   10.8.0.1:443     0.0.0.0:*
LISTEN 0      4096   10.8.0.1:8089    0.0.0.0:*
LISTEN 0      4096   10.8.0.1:8081    0.0.0.0:*
LISTEN 0      4096   10.9.0.1:80      0.0.0.0:*
LISTEN 0      4096   10.9.0.1:443     0.0.0.0:*
LISTEN 0      4096   10.9.0.1:8089    0.0.0.0:*
LISTEN 0      4096   10.9.0.1:8081    0.0.0.0:*
EOF
""",
        executable=True,
    )
    _write_file(
        caddyfile,
        """{
  auto_https disable_redirects
  servers {
    protocols h1 h2
  }
}

import /etc/caddy/Caddyfile.d/*.caddy
""",
    )
    _write_file(
        caddy_snippet,
        """http://10.8.0.1:8089 {
  redir https://10.8.0.1/
}

http://10.9.0.1:8089 {
  redir https://10.9.0.1/
}
""",
    )
    _write_file(
        compose_path,
        """services:
  pihole:
    ports:
      - "10.8.0.1:8081:80"
      - "10.9.0.1:8081:80"
""",
    )

    def write_timer(name: str, on_calendar: str, unit: str) -> None:
        _write_file(
            systemd_dir / name,
            f"""[Unit]
Description=test

[Timer]
OnCalendar={on_calendar}
Persistent=true
Unit={unit}

[Install]
WantedBy=timers.target
""",
        )

    write_timer(
        "xauex-window-signal@morning.timer",
        "Mon-Fri *-*-* 07:55:00 Europe/London",
        "xauex-window-signal@morning.service",
    )
    write_timer(
        "xauex-window-signal@midday.timer",
        "Mon-Fri *-*-* 11:25:00 Europe/London",
        "xauex-window-signal@midday.service",
    )
    write_timer(
        "xauex-window-signal@us_open.timer",
        "Mon-Fri *-*-* 08:25:00 America/New_York",
        "xauex-window-signal@us_open.service",
    )
    write_timer(
        "xauex-window-confirm@morning.timer",
        "Mon-Fri *-*-* 07:59:00 Europe/London",
        "xauex-window-confirm@morning.service",
    )
    write_timer(
        "xauex-window-confirm@midday.timer",
        "Mon-Fri *-*-* 11:29:00 Europe/London",
        "xauex-window-confirm@midday.service",
    )
    write_timer(
        "xauex-window-confirm@us_open.timer",
        "Mon-Fri *-*-* 08:29:00 America/New_York",
        "xauex-window-confirm@us_open.service",
    )

    env = os.environ.copy()
    env.update(
        {
            "CADDY_BIN": str(caddy),
            "SS_BIN": str(ss_bin),
            "CADDYFILE_PATH": str(caddyfile),
            "CADDY_SNIPPET_PATH": str(caddy_snippet),
            "PIHOLE_COMPOSE_PATH": str(compose_path),
            "SYSTEMD_DIR": str(systemd_dir),
        }
    )
    return env


def test_host_layout_check_accepts_expected_window_timer_layout(tmp_path: Path) -> None:
    env = _build_base_env(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--strict"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert "window timers installed with per-slot schedules" in result.stdout


def test_host_layout_check_rejects_legacy_template_timer_layout(tmp_path: Path) -> None:
    env = _build_base_env(tmp_path)
    systemd_dir = Path(env["SYSTEMD_DIR"])
    _write_file(
        systemd_dir / "xauex-window-signal@.timer",
        """[Timer]
OnCalendar=Mon-Fri *-*-* 07:55:00 Europe/London
OnCalendar=Mon-Fri *-*-* 11:25:00 Europe/London
Unit=xauex-window-signal@%i.service
""",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--strict"],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert "legacy template timer" in result.stderr
