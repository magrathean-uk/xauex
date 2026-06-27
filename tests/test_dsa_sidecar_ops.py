from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PINNED_DSA_COMMIT = "7ff3297050cfebd6f741649d799cb50cad857451"


def test_dsa_sidecar_runner_is_pinned_and_localhost_only():
    runner = (REPO_ROOT / "ops" / "run_dsa_sidecar.sh").read_text(encoding="utf-8")

    assert "https://github.com/ZhuLinsen/daily_stock_analysis.git" in runner
    assert PINNED_DSA_COMMIT in runner
    assert 'DSA_SIDECAR_HOST:-127.0.0.1' in runner
    assert 'DSA_SIDECAR_PORT:-8090' in runner


def test_systemd_installer_installs_dsa_sidecar_without_enabling_by_default():
    installer = (REPO_ROOT / "ops" / "install_systemd.sh").read_text(encoding="utf-8")

    assert "xauex-run-dsa-sidecar" in installer
    assert "dsa-sidecar.service" in installer
    assert "systemctl enable dsa-sidecar.service" not in installer


def test_env_example_documents_disabled_dsa_bridge_defaults():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "XAUEX_DSA_ENABLED=false" in env_example
    assert "XAUEX_DSA_BASE_URL=http://127.0.0.1:8090/api/v1" in env_example
    assert "XAUEX_DSA_SHADOW_ONLY=true" in env_example
