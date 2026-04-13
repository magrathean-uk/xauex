# XAUEX

XAUEX is the canonical repo-packaged London-open XAUUSD demo trading system.

The retained live architecture is:

1. `xauex-web.service` serves the operator dashboard on `8089`.
2. `xauex-signal.timer` runs the signal generator before the London entry windows and writes the latest command bundle.
3. `xauex.service` polls `/var/lib/xauex/cmd.json` and executes the XAUEX trading workflow.
4. `xauex-trade-journal.timer` and `xauex-weekly-review.timer` generate the post-session reporting artifacts.
5. `/etc/cron.d/xauex-daily-report` emails a daily health summary at 20:00 GMT.

Documentation in this repo describes only the retained XAUEX runtime.

Start with [docs/CODEX_DISCOVERY.md](docs/CODEX_DISCOVERY.md) if you need a fast repo map for editing or debugging.

## Repository Layout

- `xauex/signal/`: market-context fetchers, direct predictor, and signal-writing logic
- `xauex/`: cTrader execution bot, dashboard integration, and XAUEX runtime code
- `ops/`: systemd units, wrapper scripts, and runbook helpers
- `docs/`: rebuild and operational documentation

## Operator Docs

- [docs/CODEX_DISCOVERY.md](docs/CODEX_DISCOVERY.md): current repo map, file ownership, common edit paths
- [docs/REBUILD.md](docs/REBUILD.md): fresh-host rebuild
- [ops/RUNBOOK.md](ops/RUNBOOK.md): live host operations
- [ops/Caddyfile.root.example](ops/Caddyfile.root.example): example root Caddy layout for the VPN dashboard plus optional public relay split
- [ops/pihole-compose.override.example.yml](ops/pihole-compose.override.example.yml): example Pi-hole port mapping that keeps VPN `:80` free for HTTPS redirect

## Current Runtime Model

- Trade instrument: `XAUUSD`
- Trading windows: London morning and late-morning sessions
- Signal generation: direct predictor using fresh context, price structure, and recent local memory
- Session management: staged lifecycle with `OBSERVE`, `PROTECT`, and `TRAIL`
- Risk: cash risk stays capped while stops may widen when the session manager deems it necessary
- Manual trades from the dashboard remain separate from XAUEX-owned positions

## Rebuild

Read [docs/REBUILD.md](docs/REBUILD.md) for the complete rebuild flow. The short version is:

```bash
git clone <your-repo-url>
cd xauex
cp .env.example .env
cp xauex/.env.example xauex/.env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo bash ops/install_systemd.sh
```

For a fresh host, also review the prerequisites in [docs/REBUILD.md](docs/REBUILD.md): the dashboard is VPN-only over Caddy, and Pi-hole admin must already be moved to `:8081` if it is installed on the same machine.

## Key Paths

- Signal file: `/var/lib/xauex/cmd.json`
- XAUEX state: `/var/lib/xauex/state.json`
- XAUEX log: `/var/log/xauex/xauex.log`

## Hygiene

Ignored from git:

- `.env` and `xauex/.env`
- virtualenvs
- logs
- local caches and compiled files
