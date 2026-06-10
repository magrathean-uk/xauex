# XAUEX

XAUEX is the repo-packaged XAUUSD demo trading runtime for the `magrathean-uk/xauex` repository. Version `v1.0` is the current stable baseline.

The live host is intentionally simple:

1. Scheduled signal jobs build market context and write `/var/lib/xauex/cmd.json`.
2. `xauex.service` reads the command file, connects to cTrader demo, and manages XAUEX-owned positions.
3. `xauex-web.service` serves the operator dashboard locally on `127.0.0.1:8089`.
4. Caddy exposes the dashboard only on VPN interfaces over HTTPS.
5. Monit sends actionable alert emails for morning decisions, trade opens, runtime health, and service failures.
6. Post-session jobs maintain the trade journal and weekly review artifacts.

No scheduled daily report email or weekly shadow report email is part of v1.0. Those paths were retired in favor of Monit alerts plus the dashboard.

## Repository Layout

- `xauex/main.py`: live bot orchestrator and cTrader execution loop.
- `xauex/bot/`: broker API, execution, filters, risk, state writing, and strategy helpers.
- `xauex/signal/`: market context, signal generation, evidence, confirmation, and command-file output.
- `xauex/app/`: Flask dashboard, JSON API, operator controls, and templates.
- `xauex/analyst/`: post-trade journal and weekly review jobs.
- `xauex/shared/`: diagnostics, event journal, manual command helpers, and shared UI support.
- `ops/`: systemd units, wrappers, Monit checks, Caddy snippet, log hygiene, and runbook.
- `tests/` and `xauex/tests/`: dashboard, signal, runtime, and bot tests.
- `docs/`: rebuild, discovery, export, and host documentation.

Compatibility shims remain at repo root for old imports: `config.py`, `auth.py`, and `bot/__init__.py`. New code should import from `xauex.*`.

## How The Trading Flow Works

XAUEX trades `XAUUSD` on a cTrader demo account. The bot is allowed to trade when `XAUEX_MODE=true` and the latest command bundle passes the runtime gates.

The live windows are defined in `xauex/live_windows.py`:

- Morning London: signal `07:55`, confirm `07:59`, entry `08:00-08:05 Europe/London`.
- Midday London: signal `11:25`, confirm `11:29`, entry `11:30-11:35 Europe/London`.
- US open: signal `08:25`, confirm `08:29`, entry `08:30-08:35 America/New_York`.

For each window:

1. `xauex-window-signal@<window>.timer` starts `xauex-window-signal@<window>.service`.
2. The signal pipeline fetches context, runs the predictor, writes evidence under `/var/lib/xauex/signal_runs/`, and updates `/var/lib/xauex/cmd.json`.
3. `xauex-window-confirm@<window>.timer` runs the confirmation pass shortly before entry.
4. `xauex.service` polls `/var/lib/xauex/cmd.json`.
5. If the signal is actionable, fresh, confirmed, inside the entry window, and passes risk gates, XAUEX places/manages the position.
6. Runtime state is written to `/var/lib/xauex/state.json` and the dashboard reads that state.

Manual dashboard trades are separate from XAUEX-owned trades and do not count toward XAUEX signal/session limits.

## Active Services

- `xauex-web.service`: dashboard app on `127.0.0.1:8089`.
- `xauex.service`: cTrader execution runtime, started by `xauex-start.timer`.
- `xauex-window-signal@morning.timer`, `@midday.timer`, `@us_open.timer`: scheduled signal generation.
- `xauex-window-confirm@morning.timer`, `@midday.timer`, `@us_open.timer`: pre-entry confirmation.
- `xauex-shadow-compare.timer`: baseline-vs-debate shadow comparison.
- `xauex-shadow-evaluate.timer`: resolves shadow trials after the holding window.
- `xauex-start.timer`: starts the bot before the morning session.
- `xauex-stop.timer` and `xauex-stop.service`: stop/force-flat boundary on Friday.
- `xauex-trade-journal.timer`: post-session trade journal.
- `xauex-weekly-review.timer`: Friday weekly review.

Monit owns alert-style email checks:

- `xauex-morning-summary`: one decision email per morning cycle.
- `xauex-trade-alerts`: trade-open alerts.
- `xauex-signal-stall`: repeated source-blocked HOLD and degraded source fallback alerts.
- runtime/dashboard/service checks configured outside this repo plus the repo Monit snippets under `ops/monitoring/`.

## Host And Dashboard Model

- The dashboard app itself listens only on loopback: `127.0.0.1:8089`.
- Caddy binds VPN addresses `10.8.0.1` and `10.9.0.1`.
- VPN HTTP redirects to VPN HTTPS.
- The public relay, if present, is separate from the XAUEX dashboard.
- Pi-hole admin, if installed on the same host, must not occupy the standard VPN dashboard ports. The reference split is in `ops/pihole-compose.override.example.yml`.

Reference Caddy layout: `ops/Caddyfile.root.example` and `ops/xauex-dashboard.caddy`.

## Runtime Paths

- Command bundle: `/var/lib/xauex/cmd.json`
- Bot state: `/var/lib/xauex/state.json`
- Risk state: `/var/lib/xauex/risk_state.json`
- Latest brief: `/var/lib/xauex/latest_signal_brief.md`
- Latest evidence: `/var/lib/xauex/latest_signal_evidence.json`
- Signal archives: `/var/lib/xauex/signal_runs/`
- Shadow trials: `/var/lib/xauex/shadow_trials/`
- Trade journal: `/var/lib/xauex/trade_journal.json`
- Weekly review: `/var/lib/xauex/weekly_review.json` and `/var/lib/xauex/weekly_review.md`
- Bot log: `/var/log/xauex/xauex.log`
- Service wrapper logs: `logs/`

## Operator Docs

- `docs/CODEX_DISCOVERY.md`: fastest repo map for editing and debugging.
- `ops/RUNBOOK.md`: live host operating guide.
- `docs/REBUILD.md`: fresh-host rebuild.
- `docs/GITHUB_EXPORT.md`: publish/export notes for GitHub.
- `docs/HOST_AUDIT_2026-04-11.md`: historical host audit.

Historical plans and specs under `docs/superpowers/` are design history, not current runtime truth.

## Quick Setup

For a fresh host, read `docs/REBUILD.md`. Short version:

```bash
git clone https://github.com/magrathean-uk/xauex.git
cd xauex
cp .env.example .env
cp xauex/.env.example xauex/.env
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
sudo bash ops/install_systemd.sh
```

## Quick Checks

```bash
bash status.sh
curl -fsS http://127.0.0.1:8051/health
curl -fsS http://127.0.0.1:8089/api/dashboard
systemctl --failed --no-pager
monit summary
```

Targeted tests used for dashboard/runtime checks:

```bash
python3 -m pytest tests/test_xauex_trade_alerts.py tests/test_xauex_signal_stall_alerts.py tests/test_xauex_runtime_monitor.py tests/dashboard/test_manual_controls.py tests/bridge/test_signal_writer.py -q
```

## Security And Hygiene

Do not commit:

- `.env`
- `xauex/.env`
- credentials or broker tokens
- virtualenvs
- logs
- runtime state
- local caches and compiled artifacts
