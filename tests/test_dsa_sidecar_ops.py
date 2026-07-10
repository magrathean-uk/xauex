import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PINNED_DSA_COMMIT = "a2f19f65dc881e693f018eb08b889132eeeab659"


def test_dsa_sidecar_runner_is_pinned_and_localhost_only():
    runner = (REPO_ROOT / "ops" / "run_dsa_sidecar.sh").read_text(encoding="utf-8")

    assert "https://github.com/ZhuLinsen/daily_stock_analysis.git" in runner
    assert PINNED_DSA_COMMIT in runner
    assert 'DSA_SIDECAR_HOST:-127.0.0.1' in runner
    assert 'DSA_SIDECAR_PORT:-18090' in runner


def test_dsa_sidecar_runner_can_fall_back_to_docker_for_python313_hosts():
    runner = (REPO_ROOT / "ops" / "run_dsa_sidecar.sh").read_text(encoding="utf-8")

    assert "DSA_SIDECAR_RUNTIME:-auto" in runner
    assert "python_is_compatible" in runner
    assert "run_docker_sidecar" in runner
    assert "docker build" in runner
    assert "docker run" in runner
    assert "DSA_SIDECAR_REQUIREMENT_CONSTRAINTS" in runner
    assert "DSA_SIDECAR_REQUIREMENT_CONSTRAINTS-" in runner
    assert "numpy<2.0" in runner
    assert "127.0.0.1" in runner


def test_dsa_sidecar_docker_healthcheck_targets_the_real_api():
    runner = (REPO_ROOT / "ops" / "run_dsa_sidecar.sh").read_text(encoding="utf-8")

    assert "--health-cmd" in runner
    assert "http://127.0.0.1:$PORT/api/health" in runner
    assert "--health-interval 30s" in runner
    assert "--health-timeout 10s" in runner
    assert "--health-retries 3" in runner
    assert "|| exit 1" in runner


def test_dsa_sidecar_runner_refuses_credentialed_github_urls(tmp_path):
    runner = REPO_ROOT / "ops" / "run_dsa_sidecar.sh"
    bin_dir = tmp_path / "bin"
    base_dir = tmp_path / "base"
    repo_dir = base_dir / "daily_stock_analysis"
    bin_dir.mkdir()

    git_stub = bin_dir / "git"
    git_stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo "git-called $*" >&2
if [[ "${1:-}" == "clone" ]]; then
  mkdir -p "${3:-}/.git"
fi
exit 0
""",
        encoding="utf-8",
    )
    git_stub.chmod(0o755)

    docker_stub = bin_dir / "docker"
    docker_stub.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
echo "docker-called $*" >&2
exit 0
""",
        encoding="utf-8",
    )
    docker_stub.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "DSA_SIDECAR_RUNTIME": "docker",
            "DSA_SIDECAR_REPO_URL": "https://fake-token@github.com/example/private.git",
            "DSA_SIDECAR_BASE_DIR": str(base_dir),
            "DSA_SIDECAR_REPO_DIR": str(repo_dir),
            "DSA_SIDECAR_DOCKER_BUILD": "never",
        }
    )

    result = subprocess.run(
        ["bash", str(runner)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 1
    assert "credential-bearing GitHub value" in combined
    assert "fake-token" not in combined
    assert "git-called" not in combined


def test_systemd_installer_installs_dsa_sidecar_without_enabling_by_default():
    installer = (REPO_ROOT / "ops" / "install_systemd.sh").read_text(encoding="utf-8")

    assert "xauex-run-dsa-sidecar" in installer
    assert "dsa-sidecar.service" in installer
    assert "__DSA_DOCKER_GROUP__" in installer
    assert "SupplementaryGroups=docker" in installer
    assert "systemctl enable dsa-sidecar.service" not in installer


def test_dsa_sidecar_unit_accepts_expected_docker_stop_exit_codes():
    unit = (REPO_ROOT / "ops" / "dsa-sidecar.service").read_text(encoding="utf-8")

    assert "SuccessExitStatus=137 143" in unit


def test_env_example_documents_disabled_dsa_bridge_defaults():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "XAUEX_DSA_ENABLED=false" in env_example
    assert "XAUEX_DSA_BASE_URL=http://127.0.0.1:18090/api/v1" in env_example
    assert "XAUEX_DSA_SHADOW_ONLY=true" in env_example
