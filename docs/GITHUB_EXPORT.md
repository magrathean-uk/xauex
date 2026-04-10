# GitHub Export Notes

This directory is designed to become its own standalone git repository.

## Why A Separate Repo Is Needed

The machine previously had a parent git root outside this project directory. That is not suitable for publishing this project because it would mix unrelated files and machine state into one repository.

## Safe Publish Scope

Included:

- source code
- service unit templates in `ops/`
- rebuild and run documentation
- dependency lock files such as `frontend/package-lock.json` and `backend/uv.lock`
- config templates such as `.env.example` and `xauex/.env.example`

Excluded:

- `.env`
- `xauex/.env`
- logs
- uploaded backend reports/history
- frontend build output
- node modules
- virtual environments

## Recommended Publish Steps

```bash
cd /path/to/xauex
git init
git add .
git commit -m "Initial XAUEX import"
git branch -M main
git remote add origin git@github.com:<user>/xauex.git
git push -u origin main
```

If using HTTPS with a PAT:

```bash
git remote add origin https://github.com/<user>/xauex.git
git push -u origin main
```

## Before Pushing

Check these commands:

```bash
git status --short
git check-ignore -v .env xauex/.env logs/ backend/uploads/ frontend/node_modules/ frontend/dist/
```

Make sure no live credentials appear in:

- committed `.env.example`
- committed `xauex/.env.example`
- scripts in `ops/`
- documentation
