# Daily Stock Analysis Sidecar

XAUEX can read `ZhuLinsen/daily_stock_analysis` as a localhost-only equity research sidecar. This integration is disabled by default and shadow-only.

## Safety Boundary

- DSA output is written only into XAUEX signal evidence as `dsa_sidecar`.
- DSA output does not write `/var/lib/xauex/cmd.json`.
- DSA output is not added to the XAUEX parser action stream.
- XAUEX kill switch, confirmation, risk caps, symbol checks, and execution gates remain authoritative.
- DSA is equity-focused and should not be treated as a replacement for the current XAUUSD live signal path.

## XAUEX Environment

In the XAUEX root `.env`:

```bash
XAUEX_DSA_ENABLED=false
XAUEX_DSA_BASE_URL=http://127.0.0.1:18090/api/v1
XAUEX_DSA_SYMBOL=AAPL
XAUEX_DSA_ADMIN_COOKIE=
XAUEX_DSA_TIMEOUT_SECONDS=5
XAUEX_DSA_MIN_CONFIDENCE=0.65
XAUEX_DSA_SHADOW_ONLY=true
```

Set `XAUEX_DSA_ENABLED=true` only after the DSA sidecar is running and reachable on loopback.

## Optional Sidecar Service

`ops/run_dsa_sidecar.sh` clones and runs DSA pinned to:

```text
7ff3297050cfebd6f741649d799cb50cad857451
```

The runner defaults `DSA_SIDECAR_RUNTIME=auto`. It uses a local virtualenv when
Python 3.10, 3.11, or 3.12 is available. On Python 3.13-only hosts, it falls
back to the upstream Dockerfile, which is pinned to Python 3.11.

`sudo bash ops/install_systemd.sh` installs but does not enable `dsa-sidecar.service`. Configure DSA-specific secrets in `/etc/xauex/dsa-sidecar.env`, then start it explicitly:

```bash
sudo systemctl start dsa-sidecar.service
curl -fsS http://127.0.0.1:18090/api/health || curl -fsS http://127.0.0.1:18090/health
```

The service defaults to `127.0.0.1:18090`. Do not bind it publicly unless DSA authentication and network exposure are reviewed separately.

Useful service environment overrides in `/etc/xauex/dsa-sidecar.env`:

```bash
DSA_SIDECAR_RUNTIME=auto
DSA_SIDECAR_PYTHON=/usr/bin/python3.11
DSA_SIDECAR_DOCKER_BUILD=auto
DSA_SIDECAR_CONTAINER_NAME=xauex-dsa-sidecar
```

The default Docker build constraints are `numpy<2.0` and `pandas<3.0`, which
avoid newer NumPy wheels that require x86-64-v2 CPU instructions. Set
`DSA_SIDECAR_REQUIREMENT_CONSTRAINTS=` only on hosts where the unconstrained
upstream Docker build is known to work.

## Verification

Run the adapter and ops tests:

```bash
python3 -m pytest tests/bridge/test_dsa_sidecar.py tests/bridge/test_evidence_writer.py tests/bridge/test_run_mode_selection.py tests/test_dsa_sidecar_ops.py -q
```

When enabled, the latest evidence file should include:

```json
{
  "dsa_sidecar": {
    "enabled": true,
    "status": "ok",
    "symbol": "AAPL",
    "shadow_signal": {
      "action": "BUY",
      "shadow_only": true
    }
  }
}
```
