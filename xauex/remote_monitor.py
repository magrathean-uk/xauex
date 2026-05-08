#!/usr/bin/env python3
"""Remote monitor for XAUEX.

Usage examples:
  python3 remote_monitor.py --health-url http://YOUR_VPS:8051/health
  python3 remote_monitor.py --health-url http://127.0.0.1:8051/health --watch
  python3 remote_monitor.py --health-url http://127.0.0.1:8051/health \
      --ssh-target user@your-vps --watch --notify-macos

Notes:
  - The health endpoint is enough for basic liveness and risk checks.
  - If you also pass --ssh-target, the script will read the bot state file over
    SSH and show richer signal/trend details.
  - On macOS, --notify-macos sends notifications when status changes.
"""

from __future__ import annotations

import argparse
import json
import platform
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional


DEFAULT_HEALTH_URL = "http://127.0.0.1:8051/health"
DEFAULT_STATE_PATH = "/tmp/xauex_test_state.json"


@dataclass
class MonitorResult:
    health: Optional[dict[str, Any]]
    state: Optional[dict[str, Any]]
    errors: list[str]


def fetch_json_http(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "xauex-remote-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        if body:
            return json.loads(body)
        raise


def fetch_state_via_ssh(target: str, path: str, port: int, timeout: float) -> dict[str, Any]:
    remote_cmd = f"cat {shlex.quote(path)}"
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(timeout)}",
        "-p",
        str(port),
        target,
        remote_cmd,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def _fmt_money(value: Any, currency: str = "GBP") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{currency} ?"
    return f"{currency} {number:,.2f}"


def _fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def render_report(result: MonitorResult) -> str:
    lines: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"XAUEX Remote Monitor  {now}")
    lines.append("")

    if result.health:
        health = result.health
        lines.append("Health")
        lines.append(
            "  "
            f"status={health.get('status', '?')}  "
            f"bot_status={health.get('bot_status', '?')}"
        )
        lines.append(
            "  "
            f"uptime={health.get('uptime_seconds', '?')}s  "
            f"tick_age={health.get('last_tick_age_seconds', '?')}s  "
            f"reconnects={health.get('reconnect_count', '?')}  "
            f"open_positions={health.get('open_positions', '?')}"
        )
    else:
        lines.append("Health")
        lines.append("  unavailable")

    if result.state:
        state = result.state
        meta = state.get("meta", {})
        account = state.get("account", {})
        risk = state.get("risk", {})
        trend = state.get("trend", {})
        runtime = state.get("runtime", {})
        strategy = state.get("strategy", {})
        macro_regime = state.get("macro_regime", {})
        signal = state.get("last_signal") or {}
        history = state.get("signal_history", [])
        shadow_history = state.get("shadow_signal_history", [])
        trades = state.get("closed_trades_today", [])
        ccy = account.get("currency", "GBP")
        exec_tf = runtime.get("execution_timeframe", trend.get("execution_timeframe", "H1"))

        lines.append("")
        lines.append("Account")
        lines.append(
            "  "
            f"balance={_fmt_money(account.get('balance'), ccy)}  "
            f"equity={_fmt_money(account.get('equity'), ccy)}  "
            f"open_pnl={_fmt_money(account.get('open_pnl'), ccy)}"
        )

        lines.append("")
        lines.append("Risk")
        lines.append(
            "  "
            f"day_start={_fmt_money(risk.get('day_start_balance'), ccy)}  "
            f"daily_pnl={_fmt_money(risk.get('daily_pnl'), ccy)}  "
            f"weekly_pnl={_fmt_money(risk.get('weekly_pnl'), ccy)}"
        )
        lines.append(
            "  "
            f"losses_today={risk.get('consecutive_losses_today', '?')}  "
            f"daily_halted={risk.get('daily_halted', '?')}  "
            f"weekly_halted={risk.get('weekly_halted', '?')}"
        )

        lines.append("")
        lines.append("Trend")
        if trend:
            lines.append(
                "  "
                f"alignment={trend.get('alignment', '?')}  "
                f"reason={trend.get('reason', '?')}"
            )
            lines.append(
                "  "
                f"D1 8/21={_fmt_num(trend.get('daily_ema_8'))}/{_fmt_num(trend.get('daily_ema_21'))}  "
                f"{exec_tf} 50/200={_fmt_num(trend.get('exec_ema_50'))}/{_fmt_num(trend.get('exec_ema_200'))}"
            )
        else:
            lines.append("  not populated yet")

        lines.append("")
        lines.append("Last Signal")
        if signal:
            lines.append(
                "  "
                f"time={signal.get('time_utc', '?')}  "
                f"pattern={signal.get('pattern') or 'NONE'}  "
                f"level={signal.get('level_checked', '?')}"
            )
            lines.append(
                "  "
                f"gate={signal.get('gate_result', '?')}  "
                f"action={signal.get('action', '?')}"
            )
        else:
            lines.append("  none yet")

        lines.append("")
        lines.append("Runtime")
        lines.append(
            "  "
            f"strategy={strategy.get('active_mode', runtime.get('strategy_mode', '?'))}  "
            f"shadow={strategy.get('shadow_mode', runtime.get('shadow_strategy_mode', 'NONE')) or 'NONE'}  "
            f"macro={macro_regime.get('regime', 'NONE')}@{_fmt_num(macro_regime.get('confidence'))}  "
            f"updated={meta.get('last_updated_utc', '?')}  "
            f"candle_index={runtime.get('candle_index', '?')}  "
            f"pending_inside_bar_pairs={runtime.get('pending_inside_bar_pairs', '?')}"
        )
        lines.append(
            "  "
            f"news_feed_available={runtime.get('news_feed_available', '?')}  "
            f"kill_switch_active={runtime.get('kill_switch_active', '?')}  "
            f"signal_history={len(history)}  "
            f"closed_trades_today={len(trades)}"
        )

        if history:
            lines.append("")
            lines.append("Recent Signals")
            for item in history[:5]:
                lines.append(
                    "  "
                    f"{item.get('time_utc', '?')}  "
                    f"{item.get('pattern') or 'NONE'}  "
                    f"{item.get('gate_result', '?')}  "
                    f"{item.get('action', '?')}"
                )

        if shadow_history:
            lines.append("")
            lines.append("Shadow Signals")
            for item in shadow_history[:5]:
                lines.append(
                    "  "
                    f"{item.get('time_utc', '?')}  "
                    f"{item.get('pattern') or 'NONE'}  "
                    f"{item.get('gate_result', '?')}  "
                    f"{item.get('action', '?')}"
                )

        if trades:
            lines.append("")
            lines.append("Closed Trades")
            for trade in trades[:3]:
                lines.append(
                    "  "
                    f"{trade.get('close_time_utc', '?')}  "
                    f"{trade.get('direction', '?')}  "
                    f"{_fmt_num(trade.get('entry_price'))}->{_fmt_num(trade.get('close_price'))}  "
                    f"pnl={_fmt_money(trade.get('pnl'), ccy)}"
                )

    if result.errors:
        lines.append("")
        lines.append("Errors")
        for error in result.errors:
            lines.append(f"  {error}")

    return "\n".join(lines)


def macos_notify(title: str, message: str) -> None:
    if platform.system() != "Darwin":
        return
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def monitor_once(args: argparse.Namespace) -> MonitorResult:
    errors: list[str] = []
    health = None
    state = None

    try:
        health = fetch_json_http(args.health_url, args.timeout)
    except urllib.error.URLError as exc:
        errors.append(f"health fetch failed: {exc}")
    except Exception as exc:
        errors.append(f"health fetch failed: {exc}")

    if args.ssh_target:
        try:
            state = fetch_state_via_ssh(args.ssh_target, args.state_path, args.ssh_port, args.timeout)
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            errors.append(f"ssh state fetch failed: {stderr or exc}")
        except Exception as exc:
            errors.append(f"ssh state fetch failed: {exc}")

    return MonitorResult(health=health, state=state, errors=errors)


def make_change_key(result: MonitorResult) -> tuple[Any, ...]:
    health = result.health or {}
    state = result.state or {}
    signal = state.get("last_signal") or {}
    return (
        health.get("status"),
        health.get("bot_status"),
        health.get("open_positions"),
        signal.get("time_utc"),
        signal.get("gate_result"),
        signal.get("action"),
    )


def clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remote monitor for XAUEX")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL, help="Health endpoint URL")
    parser.add_argument("--ssh-target", help="Optional SSH target for full state, e.g. user@host")
    parser.add_argument("--ssh-port", type=int, default=22, help="SSH port")
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH, help="Remote state.json path")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request timeout in seconds")
    parser.add_argument("--watch", action="store_true", help="Poll continuously")
    parser.add_argument("--interval", type=float, default=5.0, help="Polling interval in seconds")
    parser.add_argument("--notify-macos", action="store_true", help="Send macOS notifications on state changes")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    previous_key: Optional[tuple[Any, ...]] = None

    while True:
        result = monitor_once(args)
        report = render_report(result)

        if args.watch:
            clear_screen()
        print(report)

        current_key = make_change_key(result)
        if args.notify_macos and previous_key is not None and current_key != previous_key:
            health = result.health or {}
            state = result.state or {}
            signal = state.get("last_signal") or {}
            message = (
                f"{health.get('bot_status', 'UNKNOWN')} | "
                f"{signal.get('gate_result', health.get('status', 'changed'))}"
            )
            macos_notify("XAUEX Monitor", message)
        previous_key = current_key

        if not args.watch:
            return 0 if not result.errors else 1

        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
