# GoldOracle

GoldOracle is a repo-packaged London-open XAUUSD demo trading system built from three parts:

- `backend/`: MiroFish swarm simulation backend
- `bridge/`: market-context fetcher and signal parser
- `xauex/`: cTrader execution bot

The production path is:

1. `mirofish-backend.service` keeps the backend API up.
2. `mirofish-bridge.timer` runs before London open on weekdays and generates a signal.
3. `xauex.service` polls `/var/lib/xauex/cmd.json` and executes one London-morning trade per day.
4. journal and weekly-review timers write trade summaries after the session.

## What Is In This Repo

- Current live service units, wrappers, and ops scripts in [ops/](ops)
- Frontend source in [frontend/](frontend)
- Backend source in [backend/](backend)
- Bridge source in [bridge/](bridge)
- Trading bot source in [xauex/](xauex)
- Rebuild and deployment documentation in [docs/REBUILD.md](docs/REBUILD.md)
- Operating instructions in [ops/RUNBOOK.md](ops/RUNBOOK.md)

This repo is intended to be sufficient to rebuild the application and redeploy it on a fresh Linux machine. It does not include machine-local secrets, logs, uploaded simulation data, or virtual environments.

## Current Trading Model

- Trade instrument: `XAUUSD`
- Trade window: London open
- One MiroFish trade per London day
- Fixed cash target/stop in XAUEX config
- Forced flat before late London morning cutoff
- Weekends off

## Rebuild From Scratch

Read [docs/REBUILD.md](docs/REBUILD.md). The short version is:

```bash
git clone <your-repo-url>
cd GoldOracle
cp .env.example .env
cp xauex/.env.example xauex/.env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
sudo bash ops/install_systemd.sh
```

Then fill in:

- root `.env` for backend/bridge LLM and Zep settings
- `xauex/.env` for cTrader credentials and execution settings

## Key Paths

- Signal file: `/var/lib/xauex/cmd.json`
- XAUEX state: `/var/lib/xauex/state.json`
- XAUEX log: `/var/log/xauex/xauex.log`
- Backend and bridge logs: `logs/`
- Frontend build output: `frontend/dist/`

## Repo Hygiene

Ignored from git:

- `.env` and `xauex/.env`
- virtualenvs
- logs
- frontend build output
- backend uploads/history artifacts
- local caches and compiled files

That keeps the repo safe to upload while still preserving everything needed to rebuild it.
