#!/usr/bin/env python3
"""
MiroFish Gold Oracle — Main Entry Point
========================================
Single command to run the full XAUUSD trading pipeline:
  1. Start MiroFish backend (swarm intelligence engine)
  2. Start XAUEX trading bot (cTrader execution)
  3. Run bridge pipeline (fetch news → simulate → generate signal)
  4. Show live dashboard with trade history and signal status

Usage:
  python main.py                    # Run once now
  python main.py --schedule         # Run daily at configured Asia time
  python main.py --dashboard-only   # Just show dashboard (services already running)
  python main.py --once             # Single pipeline run then exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Rich imports for TUI ──────────────────────────────────────────
try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich.layout import Layout
    from rich.text import Text
    from rich import box
except ImportError:
    print("ERROR: 'rich' package not installed. Run: pip install rich")
    sys.exit(1)

try:
    import schedule
except ImportError:
    schedule = None

# ── Project paths ────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
BRIDGE_DIR = ROOT / "bridge"
XAUEX_DIR = ROOT / "xauex"
LOG_DIR = ROOT / "logs"
SIGNAL_PATH = Path(os.getenv("SIGNAL_OUTPUT_PATH", "/var/lib/xauex/cmd.json"))

console = Console()

def _setup_logging() -> None:
    """Configure root logger: DEBUG to file, INFO to stderr."""
    import os
    from logging.handlers import RotatingFileHandler
    log_level = getattr(logging, os.environ.get('LOG_LEVEL', 'DEBUG'), logging.DEBUG)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # File handler — full DEBUG
    fh = RotatingFileHandler(LOG_DIR / 'run.log', maxBytes=10*1024*1024, backupCount=3, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
    root.addHandler(fh)
    # Console handler — configurable
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    ch.setFormatter(logging.Formatter('%(asctime)s %(name)s %(levelname)s %(message)s'))
    root.addHandler(ch)

_setup_logging()
logger = logging.getLogger("mirofish")

# ── Process holders ──────────────────────────────────────────────
_backend_proc: subprocess.Popen | None = None
_xauex_proc: subprocess.Popen | None = None


# ═══════════════════════════════════════════════════════════════════
#  SERVICE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

def _find_venv_python(base_dir: Path) -> str:
    """Find the Python binary in a local .venv."""
    venv = base_dir / ".venv"
    for candidate in [venv / "bin" / "python", venv / "Scripts" / "python.exe"]:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def start_backend(progress: Progress, task_id) -> bool:
    """Start the MiroFish Flask backend."""
    global _backend_proc
    progress.update(task_id, description="[cyan]Starting MiroFish backend...")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_DIR / "mirofish_backend.log", "a")

    py = _find_venv_python(BACKEND_DIR)
    env = {**os.environ, "FLASK_DEBUG": "false"}

    _backend_proc = subprocess.Popen(
        [py, "run.py"],
        cwd=str(BACKEND_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )

    # Wait for backend to be ready
    import httpx
    for i in range(30):
        time.sleep(2)
        progress.update(task_id, advance=3)
        try:
            r = httpx.get("http://localhost:5001/api/report/list", timeout=5)
            if r.status_code == 200:
                progress.update(task_id, description="[green]✓ MiroFish backend ready")
                return True
        except Exception:
            pass

    progress.update(task_id, description="[red]✗ Backend failed to start")
    return False


def start_xauex(progress: Progress, task_id) -> bool:
    """Start the XAUEX trading bot."""
    global _xauex_proc
    progress.update(task_id, description="[cyan]Starting XAUEX trading bot...")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(LOG_DIR / "xauex.log", "a")

    py = _find_venv_python(XAUEX_DIR)
    _xauex_proc = subprocess.Popen(
        [py, "main.py"],
        cwd=str(XAUEX_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    # Give it time to connect
    for i in range(15):
        time.sleep(2)
        progress.update(task_id, advance=6)
        if _xauex_proc.poll() is not None:
            progress.update(task_id, description="[red]✗ XAUEX crashed on startup")
            return False

    progress.update(task_id, description="[green]✓ XAUEX trading bot running")
    return True


def stop_services():
    """Gracefully stop all services."""
    global _backend_proc, _xauex_proc
    for name, proc in [("XAUEX", _xauex_proc), ("Backend", _backend_proc)]:
        if proc and proc.poll() is None:
            console.print(f"  Stopping {name} (PID {proc.pid})...")
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    _backend_proc = None
    _xauex_proc = None


# ═══════════════════════════════════════════════════════════════════
#  BRIDGE PIPELINE (with progress)
# ═══════════════════════════════════════════════════════════════════

def run_pipeline(progress: Progress, task_id) -> dict | None:
    """
    Run the full bridge pipeline with step-by-step progress updates.
    Returns the signal dict or None on failure.
    """
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")

    sys.path.insert(0, str(ROOT))
    from bridge.config import BridgeConfig
    from bridge.context_builder import ContextBuilder
    from bridge.market_oracle import MarketOracle
    from bridge.signal_parser import parse_signal
    from bridge.signal_writer import write_signal
    from bridge.assets import resolve_asset

    config = BridgeConfig.from_env()
    asset_profile = resolve_asset("XAUUSD")

    # ── Step 1: Fetch live market data ────────────────────────────
    progress.update(task_id, description="[cyan]Fetching live market data...", completed=2)
    try:
        builder = ContextBuilder(config)
        bundle = builder.build(asset_profile, lookback_hours=72, max_items_per_source=3)
        builder.close()
        news_text = bundle.markdown
        progress.update(task_id, completed=8,
                        description=f"[green]✓ Fetched {bundle.item_count} items from {bundle.source_count} sources")
    except Exception as exc:
        progress.update(task_id, description=f"[red]✗ Context fetch failed: {exc}")
        return None

    # ── Step 2-7: Run MiroFish simulation via MarketOracle ────────
    # Hook into oracle logging to update progress bar
    oracle = MarketOracle(config, asset_profile)

    # Use a logging handler to track progress from oracle log messages
    class ProgressHandler(logging.Handler):
        _step_map = {
            'Ontology generated': (15, '[green]✓ Ontology generated'),
            'Graph built': (30, '[green]✓ Knowledge graph built'),
            'Simulation created': (33, '[green]✓ Simulation created'),
            'Simulation prepared': (40, '[green]✓ Agents prepared'),
            'Simulation completed': (75, '[green]✓ Simulation complete'),
            'Report generated': (85, '[green]✓ Report generated'),
            'Collected': (90, '[green]✓ Results collected'),
        }
        def emit(self, record):
            msg = record.getMessage()
            for key, (pct, desc) in self._step_map.items():
                if key in msg:
                    progress.update(task_id, completed=pct, description=desc)
                    return
            # Show intermediate polling status
            if 'Run status' in msg:
                progress.update(task_id, description=f"[cyan]Simulating... {msg.split('status:')[1].strip()[:40] if 'status:' in msg else ''}")
            elif 'Prepare status' in msg:
                progress.update(task_id, description="[cyan]Preparing agents...")
            elif 'Task' in msg and 'status' in msg:
                progress.update(task_id, description="[cyan]Building knowledge graph...")

    ph = ProgressHandler()
    logging.getLogger('bridge.market_oracle').addHandler(ph)
    progress.update(task_id, completed=10, description="[cyan]Running MiroFish simulation pipeline...")

    try:
        results = oracle.run(news_text)
    except Exception as exc:
        progress.update(task_id, description=f"[red]✗ Pipeline failed: {exc}")
        logging.getLogger('bridge.market_oracle').removeHandler(ph)
        oracle.close()
        return None

    logging.getLogger('bridge.market_oracle').removeHandler(ph)
    oracle.close()

    actions_list = results.get("actions", [])
    report_md = results.get("report_markdown", "")

    # ── Step 8: Parse signal with DeepSeek ────────────────────────
    progress.update(task_id, completed=90, description="[cyan]Parsing signal with DeepSeek...")
    try:
        sig = parse_signal(asset=asset_profile, actions=actions_list, report_markdown=report_md, config=config)
    except Exception as exc:
        progress.update(task_id, description=f"[red]✗ Signal parse failed: {exc}")
        return None
    progress.update(task_id, completed=95,
                    description=f"[green]✓ Signal: {sig['action']} (confidence {sig['confidence']:.2f})")

    # ── Step 9: Write signal to cmd.json ──────────────────────────
    write_signal(sig, str(SIGNAL_PATH))
    progress.update(task_id, completed=100,
                    description=f"[green]✓ Signal written → {sig['action']} @ {sig['confidence']:.2f}")

    return sig


# ═══════════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════════

def read_signal() -> dict:
    """Read the current signal file."""
    try:
        with open(SIGNAL_PATH) as f:
            data = json.load(f)
        return data.get("mirofish_signal", data)
    except Exception:
        return {}


def read_xauex_trades() -> list[str]:
    """Read recent trade lines from XAUEX log."""
    log_path = LOG_DIR / "xauex.log"
    if not log_path.exists():
        # Also check the standard location
        log_path = Path("/var/log/xauex/xauex.log")
    if not log_path.exists():
        return ["No trade log found"]
    lines = log_path.read_text().splitlines()
    trade_lines = [l for l in lines if any(k in l for k in ["[MIROFISH]", "Order placed", "EXECUTE", "position opened", "TRADE"])]
    return trade_lines[-15:] if trade_lines else ["No trades yet"]


def read_xauex_status() -> str:
    """Get XAUEX connection status from log."""
    log_path = LOG_DIR / "xauex.log"
    if not log_path.exists():
        log_path = Path("/var/log/xauex/xauex.log")
    if not log_path.exists():
        return "Unknown"
    lines = log_path.read_text().splitlines()[-30:]
    for line in reversed(lines):
        if "Connected and authenticated" in line:
            return "🟢 Connected"
        if "Reconnect" in line and "failed" in line:
            return "🔴 Disconnected"
        if "Not connected" in line:
            return "🔴 Disconnected"
        if "Subscribed to XAUUSD" in line:
            return "🟢 Connected (XAUUSD)"
    return "⚪ Unknown"


def build_dashboard() -> Layout:
    """Build the terminal dashboard layout."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="signal", ratio=1),
        Layout(name="trades", ratio=2),
    )

    # Header
    layout["header"].update(
        Panel(
            Text("🐟 MiroFish Gold Oracle — XAUUSD Trading Dashboard", style="bold cyan", justify="center"),
            box=box.DOUBLE,
        )
    )

    # Signal panel
    sig = read_signal()
    action = sig.get("action", "N/A")
    confidence = sig.get("confidence", 0)
    reasoning = sig.get("reasoning", "")
    ts = sig.get("timestamp_utc", "")
    sl = sig.get("stop_loss_distance", sig.get("stop_loss_usd", 0))
    tp = sig.get("take_profit_distance", sig.get("take_profit_usd", 0))

    action_color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(action, "white")

    signal_table = Table(show_header=False, box=box.SIMPLE, expand=True)
    signal_table.add_column("Key", style="dim")
    signal_table.add_column("Value")
    signal_table.add_row("Action", f"[bold {action_color}]{action}[/]")
    signal_table.add_row("Confidence", f"{confidence:.0%}")
    signal_table.add_row("SL / TP", f"${sl:.1f} / ${tp:.1f}")
    signal_table.add_row("Time", ts[:19] if ts else "N/A")
    signal_table.add_row("XAUEX", read_xauex_status())
    signal_table.add_row("", "")
    signal_table.add_row("Reasoning", reasoning[:120] + ("..." if len(reasoning) > 120 else ""))

    layout["signal"].update(Panel(signal_table, title="📊 Current Signal", border_style="cyan"))

    # Trades panel
    trades = read_xauex_trades()
    trade_text = Text()
    for line in trades:
        if "BUY" in line or "LONG" in line:
            trade_text.append(line + "\n", style="green")
        elif "SELL" in line or "SHORT" in line:
            trade_text.append(line + "\n", style="red")
        elif "HOLD" in line:
            trade_text.append(line + "\n", style="yellow")
        else:
            trade_text.append(line + "\n", style="dim")

    layout["trades"].update(Panel(trade_text, title="📈 Trade Activity", border_style="cyan"))

    # Footer
    backend_status = "🟢 Running" if _backend_proc and _backend_proc.poll() is None else "🔴 Stopped"
    xauex_status = "🟢 Running" if _xauex_proc and _xauex_proc.poll() is None else "🔴 Stopped"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    layout["footer"].update(
        Panel(
            Text(f"  Backend: {backend_status}  |  XAUEX: {xauex_status}  |  Updated: {now}  |  Press Ctrl+C to exit", justify="center"),
            box=box.ROUNDED,
        )
    )
    return layout


def show_dashboard():
    """Show live-updating dashboard."""
    console.print("\n[bold cyan]Dashboard active — refreshing every 10s. Press Ctrl+C to exit.[/]\n")
    try:
        with Live(build_dashboard(), refresh_per_second=0.1, console=console) as live:
            while True:
                time.sleep(10)
                live.update(build_dashboard())
    except KeyboardInterrupt:
        pass


# ═══════════════════════════════════════════════════════════════════
#  ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════

def ensure_dirs():
    """Create required directories."""
    for d in [LOG_DIR, SIGNAL_PATH.parent]:
        d.mkdir(parents=True, exist_ok=True)


def full_run(show_dash: bool = True):
    """Full pipeline: start services → run pipeline → show dashboard."""
    ensure_dirs()

    console.print(Panel(
        "[bold cyan]🐟 MiroFish Gold Oracle[/]\n"
        "XAUUSD AI Trading System — Swarm Intelligence + Live Execution",
        box=box.DOUBLE,
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:

        # Step 1: Start backend
        t1 = progress.add_task("Starting backend...", total=100)
        backend_ok = start_backend(progress, t1)
        if not backend_ok:
            console.print("[red]Failed to start MiroFish backend. Check logs/mirofish_backend.log[/]")
            return False

        # Step 2: Start XAUEX
        t2 = progress.add_task("Starting XAUEX...", total=100)
        xauex_ok = start_xauex(progress, t2)
        if not xauex_ok:
            console.print("[yellow]XAUEX failed to start (markets may be closed). Pipeline will still run.[/]")

        # Step 3: Run bridge pipeline
        t3 = progress.add_task("Running pipeline...", total=100)
        sig = run_pipeline(progress, t3)

    if sig:
        action = sig.get("action", "?")
        confidence = sig.get("confidence", 0)
        console.print(f"\n[bold]Signal: [{{'BUY':'green','SELL':'red','HOLD':'yellow'}}.get(action,'white')]"
                      f"{action}[/] confidence={confidence:.0%}[/]")
        console.print(f"[dim]Reasoning: {sig.get('reasoning', 'N/A')}[/]\n")
    else:
        console.print("\n[red]Pipeline failed — check logs for details[/]\n")

    if show_dash:
        show_dashboard()

    return sig is not None


def scheduled_run():
    """Run pipeline without restarting services (for scheduled runs)."""
    console.print(f"\n[cyan]{'='*60}[/]")
    console.print(f"[bold cyan]🐟 Scheduled pipeline run — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/]")
    console.print(f"[cyan]{'='*60}[/]\n")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        t = progress.add_task("Pipeline...", total=100)
        sig = run_pipeline(progress, t)

    if sig:
        console.print(f"[green]✓ Signal: {sig['action']} @ {sig['confidence']:.0%}[/]")
    else:
        console.print("[red]✗ Pipeline failed[/]")


def run_with_schedule(schedule_time: str = "08:00", timezone_name: str = "Asia/Singapore"):
    """Start services and run on a daily schedule."""
    if schedule is None:
        console.print("[red]'schedule' package not installed. Run: pip install schedule[/]")
        return

    # First run immediately
    full_run(show_dash=False)

    # Then schedule daily
    schedule.every().day.at(schedule_time).do(scheduled_run)
    console.print(f"\n[bold green]📅 Scheduled daily at {schedule_time} ({timezone_name})[/]")
    console.print("[dim]Set your VPS timezone with: sudo timedatectl set-timezone Asia/Singapore[/]\n")

    show_dashboard()

    # Keep running and check schedule
    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        pass


# ═══════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🐟 MiroFish Gold Oracle — XAUUSD AI Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                     Run pipeline once, then show dashboard
  python main.py --schedule          Run daily at 08:00 Asia/Singapore
  python main.py --schedule-time 09:00  Custom schedule time
  python main.py --once              Run pipeline once, no dashboard
  python main.py --dashboard-only    Show dashboard only (services already running)
        """,
    )
    parser.add_argument("--schedule", action="store_true", help="Run on daily schedule")
    parser.add_argument("--schedule-time", default="08:00", help="Daily run time HH:MM (default: 08:00)")
    parser.add_argument("--timezone", default="Asia/Singapore", help="Timezone for schedule (default: Asia/Singapore)")
    parser.add_argument("--once", action="store_true", help="Run pipeline once then exit (no dashboard)")
    parser.add_argument("--dashboard-only", action="store_true", help="Show dashboard only")
    parser.add_argument("--no-xauex", action="store_true", help="Skip starting XAUEX (run pipeline only)")

    args = parser.parse_args()

    # Graceful shutdown
    def _shutdown(signum, frame):
        console.print("\n[yellow]Shutting down...[/]")
        stop_services()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        if args.dashboard_only:
            show_dashboard()
        elif args.schedule:
            run_with_schedule(args.schedule_time, args.timezone)
        elif args.once:
            full_run(show_dash=False)
        else:
            full_run(show_dash=True)
    finally:
        stop_services()


if __name__ == "__main__":
    main()
