#!/usr/bin/env python3
"""Remote Textual dashboard for XAUEX over HTTP.

This is a read-only dashboard intended for use from a Mac or another remote
machine connected to the server over VPN.
"""

from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.shared.diagnostics import build_diagnostics_snapshot
from xauex.shared.tui_diagnostics import format_diagnostics_panel

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Static

DEFAULT_HEALTH_URL = "http://127.0.0.1:8051/health"
DEFAULT_STATE_URL = "http://127.0.0.1:8051/state"

_STATUS_COLOURS = {
    "RUNNING": "green",
    "RECONNECTING": "yellow",
    "HALTED_WEEKLY_DRAWDOWN": "red",
    "HALTED_DAILY_LOSSES": "red",
    "HALTED_AUTH_FAILURE": "red",
    "HALTED_KILL_SWITCH": "red",
    "HALTED_NEWS_FEED": "red",
    "SHUTDOWN": "grey",
}

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


@dataclass
class RemoteSnapshot:
    health: dict[str, Any] | None
    state: dict[str, Any] | None
    errors: list[str]


def _fetch_json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "xauex-remote-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        if body:
            return json.loads(body)
        raise


def fetch_snapshot(health_url: str, state_url: str, timeout: float) -> RemoteSnapshot:
    errors: list[str] = []
    health = None
    state = None

    try:
        health = _fetch_json(health_url, timeout)
    except urllib.error.URLError as exc:
        errors.append(f"health fetch failed: {exc}")
    except Exception as exc:
        errors.append(f"health fetch failed: {exc}")

    try:
        state = _fetch_json(state_url, timeout)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            msg = payload.get("error", str(exc))
        except Exception:
            msg = str(exc)
        errors.append(f"state fetch failed: {msg}")
    except urllib.error.URLError as exc:
        errors.append(f"state fetch failed: {exc}")
    except Exception as exc:
        errors.append(f"state fetch failed: {exc}")

    return RemoteSnapshot(health=health, state=state, errors=errors)


def _fmt_float(value: Any, digits: int = 2, fallback: str = "?") -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return fallback


def _render_chart(closes: list[Any], entries: list[dict[str, Any]], htf_levels: list[Any]) -> str:
    usable = []
    for close in closes:
        try:
            usable.append(float(close))
        except (TypeError, ValueError):
            continue
    if len(usable) < 2:
        return "  (no price data yet)"

    width = 60
    height = 8
    y_min = min(usable)
    y_max = max(usable)
    y_range = y_max - y_min or 1.0

    def price_to_row(price: float) -> int:
        normalised = (price - y_min) / y_range
        return height - 1 - int(normalised * (height - 1))

    grid = [[" "] * width for _ in range(height)]

    for level in htf_levels:
        try:
            level_value = float(level)
        except (TypeError, ValueError):
            continue
        if y_min <= level_value <= y_max:
            row = price_to_row(level_value)
            for col in range(width):
                if grid[row][col] == " ":
                    grid[row][col] = "·"

    for i, close in enumerate(usable):
        col = int((i / max(len(usable) - 1, 1)) * (width - 1))
        row = price_to_row(close)
        grid[row][col] = "─"

    for entry in entries:
        idx = entry.get("bar_index", 0)
        try:
            idx_value = int(idx)
            price = float(entry.get("price", 0.0))
        except (TypeError, ValueError):
            continue
        col = int((idx_value / max(len(usable) - 1, 1)) * (width - 1))
        col = max(0, min(width - 1, col))
        row = price_to_row(price)
        marker = "*" if entry.get("direction") == "LONG" else "v"
        grid[row][col] = marker

    lines = []
    for row_idx in range(height):
        label_price = y_max - (row_idx / (height - 1)) * y_range
        lines.append(f"{label_price:>7.0f} ┤" + "".join(grid[row_idx]))
    lines.append("        └" + "─" * width)
    return "\n".join(lines)


def _sparkline(values: list[Any], width: int = 24) -> str:
    usable = []
    for value in values[-width:]:
        try:
            usable.append(float(value))
        except (TypeError, ValueError):
            continue
    if len(usable) < 2:
        return "(no data)"
    lo = min(usable)
    hi = max(usable)
    span = hi - lo or 1.0
    blocks = []
    for value in usable:
        idx = int(((value - lo) / span) * (len(_SPARK_BLOCKS) - 1))
        blocks.append(_SPARK_BLOCKS[max(0, min(len(_SPARK_BLOCKS) - 1, idx))])
    return "".join(blocks)


def _fmt_delta(current: Any, baseline: Any) -> str:
    try:
        return f"{float(current) - float(baseline):+,.2f}"
    except (TypeError, ValueError):
        return "?"


def _policy_summary(policy: dict[str, Any] | None) -> str:
    if not policy:
        return "NONE"
    return f"{policy.get('mode', '—')} {policy.get('direction', '—')} x{policy.get('aggressiveness', '?')}"


class RemoteXAUEXDashboard(App):
    CSS = """
    #status-bar { height: 2; }
    #overview-panel { height: 3; }
    #account-panel { width: 25; }
    #trend-panel { width: 26; }
    #levels-panel { width: 1fr; }
    #runtime-panel { width: 30; }
    #top-row { height: 9; }
    #positions-panel { height: 6; }
    #signal-panel { height: 6; }
    #diagnostics-panel { height: 9; }
    #chart-panel { height: 10; }
    #history-panel { height: 11; }
    #risk-panel { height: 3; }
    """

    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(self, health_url: str, state_url: str, refresh_seconds: float, timeout: float):
        super().__init__()
        self.health_url = health_url
        self.state_url = state_url
        self.refresh_seconds = refresh_seconds
        self.timeout = timeout
        self._refresh_in_flight = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            yield Static("● CONNECTING TO REMOTE BOT...", id="status-bar")
            yield Static("", id="overview-panel")
            with Horizontal(id="top-row"):
                yield Static("", id="account-panel")
                yield Static("", id="trend-panel")
                yield Static("", id="levels-panel")
                yield Static("", id="runtime-panel")
            yield Static("", id="positions-panel")
            yield Static("", id="signal-panel")
            yield Static("", id="diagnostics-panel")
            yield Static("", id="chart-panel")
            yield Static("", id="history-panel")
            yield Static("", id="risk-panel")
        yield Footer()

    def on_mount(self) -> None:
        self._schedule_refresh()
        self.set_interval(self.refresh_seconds, self._schedule_refresh)

    def _schedule_refresh(self) -> None:
        if self._refresh_in_flight:
            return
        self._refresh_in_flight = True
        self.run_worker(self._refresh_remote(), exclusive=True)

    async def _refresh_remote(self) -> None:
        snapshot = await asyncio.to_thread(
            fetch_snapshot,
            self.health_url,
            self.state_url,
            self.timeout,
        )
        self._update_ui(snapshot)
        self._refresh_in_flight = False

    def on_worker_state_changed(self, event) -> None:
        if event.worker.is_finished:
            self._refresh_in_flight = False

    def _update_ui(self, snapshot: RemoteSnapshot) -> None:
        try:
            status_bar = self.query_one("#status-bar", Static)
        except NoMatches:
            return

        health = snapshot.health or {}
        state = snapshot.state or {}
        meta = state.get("meta", {})
        account = state.get("account", {})
        risk = state.get("risk", {})
        levels = state.get("levels", {})
        trend = state.get("trend", {})
        runtime = state.get("runtime", {})
        strategy = state.get("strategy", {})
        macro_regime = state.get("macro_regime", {})
        trade_policy = state.get("trade_policy", {})
        positions = state.get("open_positions", [])
        trades = state.get("closed_trades_today", [])
        closes = state.get("recent_h1_closes", [])
        entries = state.get("trade_entries_on_chart", [])
        signal = state.get("last_signal") or {}
        signal_history = state.get("signal_history", [])
        shadow_signal_history = state.get("shadow_signal_history", [])
        diagnostics = state.get("diagnostics", {}) or {}
        exec_tf = runtime.get("execution_timeframe", trend.get("execution_timeframe", "H1"))

        if not diagnostics:
            diagnostics = build_diagnostics_snapshot(state)

        bot_status = health.get("bot_status") or meta.get("bot_status") or "UNKNOWN"
        updated = meta.get("last_updated_utc") or health.get("timestamp_utc") or "—"
        colour = _STATUS_COLOURS.get(bot_status, "white")
        strategy_ready = runtime.get("strategy_data_ready", True)
        ready_colour = "green" if strategy_ready else "red"
        ready_label = "READY" if strategy_ready else "WAITING"
        policy_label = _policy_summary(trade_policy)
        balance = account.get("balance", 0.0)
        equity = account.get("equity", 0.0)
        day_start = risk.get("day_start_balance", balance)
        week_start = risk.get("week_start_balance", balance)
        endpoint_note = f"health={self.health_url}  state={self.state_url}"
        status_bar.update(
            f"[{colour}]● {bot_status}[/{colour}]  {updated}\n{endpoint_note}"
        )
        self.query_one("#overview-panel", Static).update(
            f"[{ready_colour}]{ready_label}[/{ready_colour}] "
            f"Strategy:{strategy.get('active_mode', runtime.get('strategy_mode', '—'))}  "
            f"Shadow:{strategy.get('shadow_mode', runtime.get('shadow_strategy_mode', '—')) or 'NONE'}  "
            f"Macro:{macro_regime.get('regime', 'NONE')}  "
            f"Policy:{policy_label}\n"
            f"BalanceΔ day {_fmt_delta(balance, day_start)}  "
            f"week {_fmt_delta(balance, week_start)}  "
            f"Equity-balance {_fmt_delta(equity, balance)}  "
            f"Signals live/shadow {len(signal_history)}/{len(shadow_signal_history)}  "
            f"Closed {len(trades)}"
        )

        ccy = account.get("currency", "GBP")
        self.query_one("#account-panel", Static).update(
            f"ACCOUNT\n"
            f"  Balance:  {ccy} {_fmt_float(balance)}\n"
            f"  Equity:   {ccy} {_fmt_float(equity)}\n"
            f"  Open P&L: {ccy} {_fmt_float(account.get('open_pnl'))}"
        )

        self.query_one("#trend-panel", Static).update(
            f"TREND FILTER\n"
            f"  Align: {trend.get('alignment', 'UNKNOWN')}\n"
            f"  D1  8/21: {_fmt_float(trend.get('daily_ema_8'))} / {_fmt_float(trend.get('daily_ema_21'))}\n"
            f"  {exec_tf} 50/200: {_fmt_float(trend.get('exec_ema_50'))} / {_fmt_float(trend.get('exec_ema_200'))}\n"
            f"  Reason: {trend.get('reason', '—')}\n"
            f"  EMA spread: {trend.get('ema_spread', '?')}  "
            f"Slope: {trend.get('ema_slope', '?')}"
        )

        mn = levels.get("monthly", {})
        wk = levels.get("weekly", {})
        refreshed = levels.get("last_refresh_utc", "—")
        self.query_one("#levels-panel", Static).update(
            f"HTF LEVELS\n"
            f"  MN  O:{_fmt_float(mn.get('open'), 0)}  H:{_fmt_float(mn.get('high'), 0)}"
            f"  L:{_fmt_float(mn.get('low'), 0)}  C:{_fmt_float(mn.get('close'), 0)}\n"
            f"  WK  O:{_fmt_float(wk.get('open'), 0)}  H:{_fmt_float(wk.get('high'), 0)}"
            f"  L:{_fmt_float(wk.get('low'), 0)}  C:{_fmt_float(wk.get('close'), 0)}\n"
            f"  Refreshed: {refreshed}"
        )

        self.query_one("#runtime-panel", Static).update(
            f"RUNTIME\n"
            f"  Strategy: {strategy.get('active_mode', runtime.get('strategy_mode', '—'))}\n"
            f"  Shadow: {strategy.get('shadow_mode', runtime.get('shadow_strategy_mode', '—')) or 'NONE'}\n"
            f"  Macro: {macro_regime.get('regime', 'NONE')} ({macro_regime.get('confidence', '?')})\n"
            f"  Policy: {policy_label}\n"
            f"  Ready: {ready_label} / {runtime.get('strategy_data_reason', '—')}\n"
            f"  Health: {health.get('status', '?')}\n"
            f"  Tick age: {health.get('last_tick_age_seconds', '?')} s\n"
            f"  News feed: {'OK' if runtime.get('news_feed_available') else 'BLOCKED'}\n"
            f"  Reconnects: {runtime.get('reconnect_count', health.get('reconnect_count', 0))}\n"
            f"  Pending IB: {runtime.get('pending_inside_bar_pairs', 0)}\n"
            f"  Candle idx: {runtime.get('candle_index', 0)}"
        )

        if positions:
            lines = [f"OPEN POSITIONS ({len(positions)})"]
            for p in positions:
                lines.append(
                    f"  #{p.get('position_id')}  {p.get('direction')}  "
                    f"Entry:{_fmt_float(p.get('entry_price'))}  "
                    f"SL:{_fmt_float(p.get('stop_loss'))}  TP:{_fmt_float(p.get('take_profit'))}"
                )
                lines.append(
                    f"    Lot:{_fmt_float(p.get('lot_size'))}  "
                    f"Pattern:{p.get('pattern')}  "
                    f"P&L:{_fmt_float(p.get('unrealised_pnl'))}"
                )
        else:
            lines = ["OPEN POSITIONS", "  (none)"]
        self.query_one("#positions-panel", Static).update("\n".join(lines))

        if signal:
            self.query_one("#signal-panel", Static).update(
                f"LAST SIGNAL\n"
                f"  Time: {signal.get('time_utc', '')}\n"
                f"  Mode: {signal.get('strategy_mode', strategy.get('active_mode', '—'))}  "
                f"Stage: {signal.get('setup_stage', '—')}\n"
                f"  Pattern: {signal.get('pattern') or '—'}  "
                f"Level: {signal.get('level_checked') or '?'}\n"
                f"  Gate: {signal.get('gate_result', '—')}  "
                f"Action: {signal.get('action', '?')}\n"
                f"  Source: {signal.get('entry_source', '—')}"
            )
        else:
            self.query_one("#signal-panel", Static).update("LAST SIGNAL\n  (none yet)")

        self.query_one("#diagnostics-panel", Static).update(
            format_diagnostics_panel(diagnostics, transport_errors=snapshot.errors)
        )

        htf_vals = [
            mn.get("open"),
            mn.get("high"),
            mn.get("low"),
            mn.get("close"),
            wk.get("open"),
            wk.get("high"),
            wk.get("low"),
            wk.get("close"),
        ]
        chart_text = _render_chart(closes, entries, htf_vals)
        self.query_one("#chart-panel", Static).update(
            f"PRICE (last 20 {exec_tf} closes)\n"
            f"{_sparkline(closes)}\n"
            f"{chart_text}"
        )

        history_lines = ["RECENT ACTIVITY"]
        if signal_history:
            history_lines.append("  Signals:")
            for item in signal_history[:5]:
                history_lines.append(
                    f"    {item.get('time_utc', '')[11:16]}  "
                    f"{(item.get('pattern') or 'NONE')[:10]:<10}  "
                    f"{item.get('gate_result', '—')[:18]:<18}  "
                    f"{item.get('action', '?')}  "
                    f"{item.get('entry_source', '—')}"
                )
        else:
            history_lines.append("  Signals: (none)")

        if shadow_signal_history:
            history_lines.append("")
            history_lines.append("  Shadow:")
            for item in shadow_signal_history[:4]:
                history_lines.append(
                    f"    {item.get('time_utc', '')[11:16]}  "
                    f"{(item.get('pattern') or 'NONE')[:10]:<10}  "
                    f"{item.get('gate_result', '—')[:18]:<18}  "
                    f"{item.get('action', '?')}  "
                    f"{item.get('entry_source', '—')}"
                )

        if trades:
            history_lines.append("")
            history_lines.append("  Closed trades:")
            for trade in trades[:3]:
                history_lines.append(
                    f"    {trade.get('close_time_utc', '')[:16]}  {trade.get('direction')}  "
                    f"{_fmt_float(trade.get('entry_price'))} -> {_fmt_float(trade.get('close_price'))}  "
                    f"{_fmt_float(trade.get('pnl'))}  {trade.get('pattern')}"
                )

        if snapshot.errors:
            history_lines.append("")
            history_lines.append("  Remote errors:")
            for error in snapshot.errors[:3]:
                history_lines.append(f"    {error[:92]}")
        self.query_one("#history-panel", Static).update("\n".join(history_lines))

        self.query_one("#risk-panel", Static).update(
            f"RISK  Losses today: {risk.get('consecutive_losses_today', 0)}   "
            f"Daily P&L: {_fmt_float(risk.get('daily_pnl'))}   "
            f"Weekly P&L: {_fmt_float(risk.get('weekly_pnl'))}\n"
            f"Read-only remote dashboard   [Q] Quit   Live policy: {policy_label}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remote dashboard for XAUEX")
    parser.add_argument("--health-url", default=DEFAULT_HEALTH_URL, help="Remote /health URL")
    parser.add_argument("--state-url", default=DEFAULT_STATE_URL, help="Remote /state URL")
    parser.add_argument("--refresh", type=float, default=2.0, help="Refresh interval in seconds")
    parser.add_argument("--timeout", type=float, default=5.0, help="Per-request timeout in seconds")
    return parser.parse_args()


def run() -> None:
    args = parse_args()
    RemoteXAUEXDashboard(
        health_url=args.health_url,
        state_url=args.state_url,
        refresh_seconds=args.refresh,
        timeout=args.timeout,
    ).run()


if __name__ == "__main__":
    run()
