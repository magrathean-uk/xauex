# GoldOracle

GoldOracle is a repo-packaged London-open XAUUSD demo trading system built from three parts:

- `backend/`: legacy MiroFish backend kept for compatibility and research workflows
- `bridge/`: live market-context fetcher, direct predictor, brief writer, and signal parser
- `xauex/`: cTrader execution bot

The current live production path is:

1. `mirofish-backend.service` keeps the backend API up.
2. `mirofish-bridge.timer` runs before London open on weekdays and generates a fresh direct predictor signal from curated context, price structure, and recent local trade memory.
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
- Phase-1 live path uses a direct weighted predictor, not a daily Zep graph build
- Weighted signal blend:
  - price action / market structure: `45%`
  - macro / news sentiment: `35%`
  - recent oracle / trade memory: `20%`
- Optional local memory layer:
  - enable with `QDRANT_ENABLED=1`
  - store path: `QDRANT_PATH=/var/lib/xauex/qdrant_local`
  - uses embedded `qdrant-client` local storage, so there is no separate Qdrant service to run
- One trade max per London day in phase 1
- Oracle uses a staged session manager:
  - `OBSERVE` after entry
  - `PROTECT` after the move proves itself
  - `TRAIL` after stronger follow-through
- Cash risk stays capped while the live stop can widen beyond the raw signal stop using structure/ATR logic
- Forced flat before late London morning cutoff
- Weekends off
- Rare `HOLD`, reserved for hard blockers or genuinely strong conflict
- Dashboard manual trades are fully independent from Oracle:
  - same broker/account
  - separate command path
  - ignored by Oracle limits, memory, and management logic
  - dashboard manual controls are exposed without app-level login
  - access is expected to be restricted by VPN/localhost-only network rules

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

- root `.env` for backend/bridge LLM settings and optional memory settings
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
