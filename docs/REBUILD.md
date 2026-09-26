# Rebuild a demo host

This guide describes the installation path in the checkout. It does not establish the state of any deployed host.

## Prerequisites

Use a Debian/Ubuntu-style host with Python 3.11+, virtualenv support, Git, Bash, curl, systemd and sudo access. The installer also uses host utilities including `ss`, `getent`, `logrotate` and `journalctl`. Configure cTrader demo application/account credentials and the signal provider credentials needed by your chosen configuration.

Caddy and Monit are optional host integrations. The checked-in notification configuration and Caddy snippets contain deployment-specific values. Review and adapt them privately before installation. A sendmail-compatible transport and the host's `systemd-email-alert@.service` are separate dependencies for the referenced notifications; this repository does not install that alert service.

The optional DSA sidecar has its own dependencies and remains opt-in. See [DSA_SIDECAR.md](DSA_SIDECAR.md).

## Prepare the checkout

```bash
git clone https://github.com/magrathean-uk/xauex.git
cd xauex
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Create `.env` from [.env.example](../.env.example) and `xauex/.env` from [xauex/.env.example](../xauex/.env.example) if they do not already exist. Preserve existing values when refreshing a host. Fill in demo credentials and provider configuration privately; template values do not establish a working account.

The root environment configures signal sources and shared runtime paths. The component environment configures the broker runtime. Bot, dashboard and confirmation units load both files with the component file second; the window signal unit loads the root file. Keep shared paths consistent, especially `SIGNAL_OUTPUT_PATH`, `CMD_FILE_PATH` and `STATE_FILE_PATH`.

The runtime normally reads and writes command/state files under `/var/lib/xauex` and logs under `/var/log/xauex`. Keep those files, both environments, tokens and `.venv` outside version control. Configure dashboard and manual-command authentication as described in [SECURITY.md](../SECURITY.md).

The interactive OAuth helper is [xauex/auth.py](../xauex/auth.py). It opens a browser, listens for a callback on localhost port 8050 and writes tokens to `.env` in its working directory. Run it only in the intended environment directory with the correct demo application settings. It is not a connectivity probe.

## Install host integration

Review [ops/install_systemd.sh](../ops/install_systemd.sh) before running it:

```bash
sudo bash ops/install_systemd.sh
```

This is a host-changing operation. It creates runtime directories, installs wrappers, units and monitoring files, renders window timers from `xauex/live_windows.py`, updates log retention, removes retired integration files, reloads host services and enables maintained timers. It restarts the dashboard and can start or restart the demo bot. Check the broker's open positions and the current session before running it.

The installer installs the DSA service without enabling or starting it. It does not force an already-enabled DSA service back to disabled. Inspect its actual state on an existing host.

Use this explicit path instead of the Makefile's stale `deploy` and `setup-dirs` targets. `setup.sh` also creates directories and environment symlinks; it is not needed for the separate-environment procedure above.

## Verify the target host

```bash
systemctl --failed --no-pager
systemctl status xauex.service xauex-web.service --no-pager
systemctl status xauex-window-signal@morning.timer xauex-window-signal@midday.timer xauex-window-signal@us_open.timer --no-pager
systemctl status xauex-window-confirm@morning.timer xauex-window-confirm@midday.timer xauex-window-confirm@us_open.timer --no-pager
systemctl status xauex-start.timer xauex-stop.timer xauex-trade-journal.timer xauex-decision-ledger.timer xauex-weekly-review.timer --no-pager
systemctl is-enabled dsa-sidecar.service
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
sudo /usr/local/bin/xauex-check-host-layout --strict
```

Run `monit summary` when Monit is installed. Inspect Caddy configuration and the intended private ingress on that host. An HTTP response alone does not prove current signal data, broker connectivity or correct execution behavior. Reconcile the dashboard, logs and broker state using [the runbook](../ops/RUNBOOK.md).

For code validation in an isolated development environment, install `requirements-dev.txt` and follow [DEVELOPMENT.md](DEVELOPMENT.md). Runtime dependencies alone do not include pytest.
