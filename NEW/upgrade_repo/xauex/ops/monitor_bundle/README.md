# XAUEX Remote Monitor

This bundle is for read-only monitoring from a Mac over your VPN.

## Files
- `remote_dashboard.py`: full-screen Textual dashboard over HTTP
- `remote_monitor.py`: terminal monitor script
- `run_dashboard.command`: double-click launcher for the full dashboard
- `run_monitor.command`: double-click launcher for macOS Terminal

## Fast Start
1. Copy the bundle to your Mac and unzip it.
2. Double-click `run_dashboard.command`.

That connects to:

```text
http://10.8.0.1:8051/health
http://10.8.0.1:8051/state
```

and opens the full remote dashboard.

If you just want a lightweight terminal watch loop with macOS notifications,
use `run_monitor.command`.

## Manual Run

```bash
python3 remote_dashboard.py \
  --health-url http://10.8.0.1:8051/health \
  --state-url http://10.8.0.1:8051/state
```

```bash
python3 remote_monitor.py --health-url http://10.8.0.1:8051/health --watch --notify-macos
```

## Requirements
- `python3`
- `textual`

If Textual is missing on the Mac:

```bash
python3 -m pip install textual
```
