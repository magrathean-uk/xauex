"""
XAUEX Dashboard — Textual Terminal UI

Displays live bot state from state.json every 2 seconds.
Provides kill switch control via cmd.json.

Run separately from the bot:
    cd /opt/xauex && python dashboard.py
"""

# ruff: noqa: E402

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")  # read repo .env without walking up to /.env

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from xauex.shared.diagnostics import build_diagnostics_snapshot
from xauex.shared.tui_diagnostics import format_diagnostics_panel

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, Static, TabbedContent, TabPane

_STATE_PATH = os.getenv("STATE_FILE_PATH", "/var/lib/xauex/state.json")
_CMD_PATH = os.getenv("CMD_FILE_PATH", "/var/lib/xauex/cmd.json")
_BRIEF_PATH = os.getenv("MORNING_BRIEF_PATH", "/var/lib/xauex/morning_brief.json")
_JOURNAL_PATH = os.getenv("TRADE_JOURNAL_PATH", "/var/lib/xauex/trade_journal.json")
_SCORES_PATH = os.getenv("SETUP_SCORES_PATH", "/var/lib/xauex/setup_scores.json")
_REVIEW_PATH = os.getenv("WEEKLY_REVIEW_PATH", "/var/lib/xauex/weekly_review.json")

# Status → colour mapping
_STATUS_COLOURS = {
    "RUNNING": "green",
    "RECONNECTING": "yellow",
    "OBSERVE_ONLY": "cyan",
    "HALTED_WEEKLY_DRAWDOWN": "red",
    "HALTED_DAILY_LOSSES": "red",
    "HALTED_AUTH_FAILURE": "red",
    "HALTED_KILL_SWITCH": "red",
    "HALTED_NEWS_FEED": "red",
    "SHUTDOWN": "grey",
}

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


# ─────────────────────────────────────────────────────────
# ASCII chart renderer
# ─────────────────────────────────────────────────────────

def _render_chart(closes: list, entries: list, htf_levels: list) -> str:
    """Render a fixed 60×8 ASCII price chart."""
    if len(closes) < 2:
        return "  (no price data yet)"

    width = 60
    height = 8
    y_min = min(closes)
    y_max = max(closes)
    y_range = y_max - y_min or 1.0

    def price_to_row(price: float) -> int:
        normalised = (price - y_min) / y_range
        return height - 1 - int(normalised * (height - 1))

    # Build grid
    grid = [[" "] * width for _ in range(height)]

    # Draw HTF levels as dotted horizontal lines
    for level in htf_levels:
        if y_min <= level <= y_max:
            row = price_to_row(level)
            for c in range(width):
                if grid[row][c] == " ":
                    grid[row][c] = "·"

    # Draw price line
    for i, close in enumerate(closes):
        col = int((i / max(len(closes) - 1, 1)) * (width - 1))
        row = price_to_row(close)
        grid[row][col] = "─"

    # Draw entry markers
    for entry in entries:
        idx = entry.get("bar_index", 0)
        col = int((idx / max(len(closes) - 1, 1)) * (width - 1))
        price = entry.get("price", 0.0)
        row = price_to_row(price)
        marker = "*" if entry.get("direction") == "LONG" else "v"
        grid[row][col] = marker

    # Build y-axis labels (5 evenly spaced price labels)
    lines = []
    for row_idx in range(height):
        label_price = y_max - (row_idx / (height - 1)) * y_range
        label = f"{label_price:>7.0f} ┤"
        lines.append(label + "".join(grid[row_idx]))
    lines.append("        └" + "─" * width)
    return "\n".join(lines)


def _sparkline(values: list, width: int = 24) -> str:
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


def _fmt_signed(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):+,.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def _fmt_delta(current: Any, baseline: Any) -> str:
    try:
        return f"{float(current) - float(baseline):+,.2f}"
    except (TypeError, ValueError):
        return "?"


def _policy_summary(policy: Optional[dict]) -> str:
    if not policy:
        return "NONE"
    mode = policy.get("mode", "—")
    direction = policy.get("direction", "—")
    aggressiveness = policy.get("aggressiveness", "?")
    return f"{mode} {direction} x{aggressiveness}"


def _sparkline(values: list, width: int = 24) -> str:
    """Render a compact sparkline for recent prices."""
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
    out = []
    for value in usable:
        idx = int(((value - lo) / span) * (len(_SPARK_BLOCKS) - 1))
        out.append(_SPARK_BLOCKS[max(0, min(len(_SPARK_BLOCKS) - 1, idx))])
    return "".join(out)


def _fmt_signed(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):+,.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def _fmt_delta(current: Any, baseline: Any) -> str:
    try:
        return f"{float(current) - float(baseline):+,.2f}"
    except (TypeError, ValueError):
        return "?"


def _policy_summary(policy: Optional[dict]) -> str:
    if not policy:
        return "NONE"
    mode = policy.get("mode", "—")
    direction = policy.get("direction", "—")
    aggressiveness = policy.get("aggressiveness", "?")
    return f"{mode} {direction} x{aggressiveness}"


# ─────────────────────────────────────────────────────────
# Analyst data helpers
# ─────────────────────────────────────────────────────────

def _load_json_safe(path: str) -> Optional[Any]:
    """Load JSON from path; return None on any error."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _format_morning_brief(data: Optional[dict]) -> str:
    if not data:
        return "MORNING BRIEF\n  (no data yet)"
    ts = data.get("generated_at_utc", "")
    model = data.get("model", "")
    stale = data.get("stale_state", False)
    brief = data.get("brief", "")
    header = f"MORNING BRIEF  {ts}  [{model}]"
    warning = "\n  [STALE DATA — bot may be down]\n" if stale else "\n"
    return header + warning + brief


def _format_setup_score(data: Optional[list]) -> str:
    if not data:
        return "LATEST SETUP SCORE\n  (no data yet)"
    entry = data[-1]
    ts = entry.get("signal_ts", "")
    breakdown = entry.get("breakdown", "")
    sig = entry.get("signal", {})
    direction = sig.get("direction", "?")
    pattern = sig.get("pattern", "?")
    level = sig.get("level_checked", "?")
    return (
        f"LATEST SETUP SCORE  {ts}\n"
        f"  {direction} {pattern} @ {level}\n"
        f"{breakdown}"
    )


def _format_weekly_review(data: Optional[dict]) -> str:
    if not data:
        return "WEEKLY REVIEW\n  (no data yet)"
    week_start = data.get("week_starting", "")
    week_end = data.get("week_ending", "")
    trades = data.get("trades_reviewed", 0)
    setups = data.get("setups_reviewed", 0)
    review = data.get("review", "")
    return (
        f"WEEKLY REVIEW  {week_start} → {week_end}"
        f"  ({trades} trades, {setups} setups)\n"
        f"{review}"
    )


def _format_journal_entries(data: Optional[list]) -> str:
    if not data:
        return "TRADE JOURNAL\n  (no entries yet)"
    lines = ["TRADE JOURNAL"]
    for e in reversed(data[-20:]):
        trade_id = e.get("trade_id", "?")
        ts = e.get("journalled_at_utc", "")[:16]
        entry = e.get("entry", {})
        direction = entry.get("direction", "?")
        entry_price = entry.get("entry_price", 0.0)
        pnl = entry.get("pnl", 0.0)
        journal = e.get("journal", "")
        try:
            price_str = f"{float(entry_price):.2f}"
            pnl_str = f"{float(pnl):+.2f}"
        except (TypeError, ValueError):
            price_str = str(entry_price)
            pnl_str = str(pnl)
        lines.append(f"\n  [{ts}] #{trade_id}  {direction} @ {price_str}  P&L: {pnl_str}")
        lines.append(f"  {journal}")
        lines.append("  " + "─" * 60)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────
# Kill switch confirmation modal
# ─────────────────────────────────────────────────────────

class KillSwitchModal(ModalScreen):
    """Confirmation prompt: Halt trading? [Y/N]"""

    BINDINGS = [
        Binding("y", "confirm", "Halt"),
        Binding("n", "dismiss", "Cancel"),
        Binding("escape", "dismiss", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Halt trading? [Y/N]", id="dialog-label")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_dismiss(self) -> None:
        self.dismiss(False)


# ─────────────────────────────────────────────────────────
# Main dashboard app
# ─────────────────────────────────────────────────────────

class XAUEXDashboard(App):
    """XAUEX terminal dashboard — read-only state consumer."""

    CSS = """
    /* Trading tab — existing panel sizing */
    #status-bar { height: 1; }
    #overview-panel { height: 3; }
    #account-panel { width: 25; }
    #trend-panel { width: 25; }
    #levels-panel { width: 1fr; }
    #runtime-panel { width: 26; }
    #top-row { height: 9; }
    #positions-panel { height: 6; }
    #signal-panel { height: 6; }
    #diagnostics-panel { height: 9; }
    #chart-panel { height: 10; }
    #history-panel { height: 10; }
    #risk-panel { height: 3; }

    /* Analyst and Journal tab panels */
    #analyst-brief  { padding: 0 1; margin-bottom: 1; }
    #analyst-score  { padding: 0 1; margin-bottom: 1; }
    #analyst-review { padding: 0 1; margin-bottom: 1; }
    #journal-entries { padding: 0 1; }
    """

    BINDINGS = [
        Binding("1", "tab_trading", "Trading", show=False),
        Binding("2", "tab_analyst", "Analyst", show=False),
        Binding("3", "tab_journal", "Journal", show=False),
        Binding("k", "kill_switch", "Kill switch", priority=True),
        Binding("q", "quit", "Quit"),
    ]

    _state: reactive = reactive({})

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="trading"):
            with TabPane("Trading [1]", id="trading"):
                with Vertical():
                    yield Static("● WAITING FOR BOT...", id="status-bar")
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
            with TabPane("Analyst [2]", id="analyst"):
                with ScrollableContainer():
                    yield Static("", id="analyst-brief")
                    yield Static("", id="analyst-score")
                    yield Static("", id="analyst-review")
            with TabPane("Journal [3]", id="journal"):
                with ScrollableContainer():
                    yield Static("", id="journal-entries")
        yield Footer()

    def on_mount(self) -> None:
        self.set_interval(2.0, self._refresh_state)

    def action_tab_trading(self) -> None:
        self.query_one(TabbedContent).active = "trading"

    def action_tab_analyst(self) -> None:
        self.query_one(TabbedContent).active = "analyst"

    def action_tab_journal(self) -> None:
        self.query_one(TabbedContent).active = "journal"

    def _refresh_state(self) -> None:
        # Bot state
        try:
            with open(_STATE_PATH, "r") as f:
                state = json.load(f)
            self._update_ui(state)
        except Exception:
            self.query_one("#status-bar", Static).update("● WAITING FOR BOT...")

        # Analyst outputs — each loaded independently; missing files → "no data yet"
        brief = _load_json_safe(_BRIEF_PATH)
        scores = _load_json_safe(_SCORES_PATH)
        review = _load_json_safe(_REVIEW_PATH)
        journal = _load_json_safe(_JOURNAL_PATH)

        self.query_one("#analyst-brief", Static).update(_format_morning_brief(brief))
        self.query_one("#analyst-score", Static).update(_format_setup_score(scores))
        self.query_one("#analyst-review", Static).update(_format_weekly_review(review))
        self.query_one("#journal-entries", Static).update(_format_journal_entries(journal))

    def _update_ui(self, state: dict) -> None:
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
        signal = state.get("last_signal", {})
        signal_history = state.get("signal_history", [])
        shadow_signal_history = state.get("shadow_signal_history", [])
        diagnostics = state.get("diagnostics", {}) or {}
        observe = state.get("observe_only", True)
        exec_tf = runtime.get("execution_timeframe", trend.get("execution_timeframe", "H1"))

        if not diagnostics:
            diagnostics = build_diagnostics_snapshot(state)

        status = meta.get("bot_status", "UNKNOWN")
        updated = meta.get("last_updated_utc", "")
        colour = _STATUS_COLOURS.get(status, "white")
        strategy_ready = runtime.get("strategy_data_ready", True)
        ready_colour = "green" if strategy_ready else "red"
        ready_label = "READY" if strategy_ready else "WAITING"
        policy_label = _policy_summary(trade_policy)
        balance = account.get("balance", 0.0)
        equity = account.get("equity", 0.0)
        day_start = risk.get("day_start_balance", balance)
        week_start = risk.get("week_start_balance", balance)

        self.query_one("#status-bar", Static).update(
            f"[{colour}]● {status}[/{colour}]  {updated}"
        )
        self.query_one("#overview-panel", Static).update(
            f"[{ready_colour}]{ready_label}[/{ready_colour}] "
            f"Strategy:{strategy.get('active_mode', runtime.get('strategy_mode', '—'))}  "
            f"Shadow:{strategy.get('shadow_mode', runtime.get('shadow_strategy_mode', '—')) or 'NONE'}  "
            f"Macro:{macro_regime.get('regime', 'NONE')}  "
            f"Policy:{policy_label}\n"
            f"BalanceΔ day {_fmt_delta(balance, day_start)}  "
            f"week {_fmt_delta(balance, week_start)}  "
            f"Equity-balance {_fmt_signed(equity - balance)}  "
            f"Spread {_fmt_signed(runtime.get('spread_dollars'))}  "
            f"Signals live/shadow {len(signal_history)}/{len(shadow_signal_history)}  "
            f"Closed {len(trades)}"
        )

        # Account panel
        pnl = account.get("open_pnl", 0.0)
        ccy = account.get("currency", "GBP")
        self.query_one("#account-panel", Static).update(
            f"ACCOUNT\n"
            f"  Balance:  {ccy} {_fmt_signed(balance)}\n"
            f"  Equity:   {ccy} {_fmt_signed(equity)}\n"
            f"  Open P&L: {ccy} {_fmt_signed(pnl)}"
        )

        alignment = trend.get("alignment", "UNKNOWN")
        self.query_one("#trend-panel", Static).update(
            f"TREND FILTER\n"
            f"  Align: {alignment}\n"
            f"  D1  8/21: {trend.get('daily_ema_8', '?')} / {trend.get('daily_ema_21', '?')}\n"
            f"  {exec_tf} 50/200: {trend.get('exec_ema_50', '?')} / {trend.get('exec_ema_200', '?')}\n"
            f"  Reason: {trend.get('reason', '—')}\n"
            f"  EMA spread: {trend.get('ema_spread', '?')}  "
            f"Slope: {trend.get('ema_slope', '?')}"
        )

        # Levels panel
        mn = levels.get("monthly", {})
        wk = levels.get("weekly", {})
        refreshed = levels.get("last_refresh_utc", "—")
        self.query_one("#levels-panel", Static).update(
            f"HTF LEVELS\n"
            f"  MN  O:{mn.get('open', '?'):.0f}  H:{mn.get('high', '?'):.0f}"
            f"  L:{mn.get('low', '?'):.0f}  C:{mn.get('close', '?'):.0f}\n"
            f"  WK  O:{wk.get('open', '?'):.0f}  H:{wk.get('high', '?'):.0f}"
            f"  L:{wk.get('low', '?'):.0f}  C:{wk.get('close', '?'):.0f}\n"
            f"  Refreshed: {refreshed}"
        )

        self.query_one("#runtime-panel", Static).update(
            f"RUNTIME\n"
            f"  Strategy: {strategy.get('active_mode', runtime.get('strategy_mode', '—'))}\n"
            f"  Shadow: {strategy.get('shadow_mode', runtime.get('shadow_strategy_mode', '—')) or 'NONE'}\n"
            f"  Macro: {macro_regime.get('regime', 'NONE')} ({macro_regime.get('confidence', '?')})\n"
            f"  Policy: {policy_label}\n"
            f"  Ready: {ready_label} / {runtime.get('strategy_data_reason', '—')}\n"
            f"  Reconnects: {runtime.get('reconnect_count', 0)}\n"
            f"  News feed: {'OK' if runtime.get('news_feed_available') else 'BLOCKED'}\n"
            f"  Kill switch: {'ON' if runtime.get('kill_switch_active') else 'OFF'}\n"
            f"  Pending IB: {runtime.get('pending_inside_bar_pairs', 0)}\n"
            f"  Candle idx: {runtime.get('candle_index', 0)}"
        )

        # Open positions
        if positions:
            lines = [f"OPEN POSITIONS ({len(positions)})"]
            for p in positions:
                lines.append(
                    f"  #{p.get('position_id')}  {p.get('direction')}  "
                    f"Entry:{p.get('entry_price'):.2f}  "
                    f"SL:{p.get('stop_loss'):.2f}  TP:{p.get('take_profit'):.2f}"
                )
                lines.append(
                    f"    Lot:{p.get('lot_size'):.2f}  "
                    f"Pattern:{p.get('pattern')}  "
                    f"P&L:{_fmt_signed(p.get('unrealised_pnl', 0))}"
                )
        else:
            lines = ["OPEN POSITIONS", "  (none)"]
        self.query_one("#positions-panel", Static).update("\n".join(lines))

        # Last signal
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
                f"  Source: {signal.get('entry_source', '—')}\n"
                f"  Reason: {signal.get('reason', signal.get('gate_result', '—'))}"
            )
        else:
            self.query_one("#signal-panel", Static).update("LAST SIGNAL\n  (none yet)")

        self.query_one("#diagnostics-panel", Static).update(
            format_diagnostics_panel(diagnostics)
        )

        # Price chart
        htf_vals = [
            mn.get("open"), mn.get("high"), mn.get("low"), mn.get("close"),
            wk.get("open"), wk.get("high"), wk.get("low"), wk.get("close"),
        ]
        htf_vals = [v for v in htf_vals if v is not None]
        chart_text = _render_chart(closes, entries, htf_vals)
        self.query_one("#chart-panel", Static).update(
            f"PRICE (last 20 {exec_tf} closes)\n"
            f"{_sparkline(closes)}\n"
            f"{chart_text}"
        )

        # Recent signal/trade activity
        lines = ["RECENT ACTIVITY"]
        if signal_history:
            lines.append("  Signals:")
            for s in signal_history[:5]:
                lines.append(
                    f"    {s.get('time_utc', '')[11:16]}  "
                    f"{(s.get('pattern') or 'NONE')[:10]:<10}  "
                    f"{s.get('gate_result', '—')[:18]:<18}  "
                    f"{s.get('action', '?')}  "
                    f"{s.get('entry_source', '—')}"
                )
        else:
            lines.append("  Signals: (none)")

        if shadow_signal_history:
            lines.append("")
            lines.append("  Shadow:")
            for s in shadow_signal_history[:4]:
                lines.append(
                    f"    {s.get('time_utc', '')[11:16]}  "
                    f"{(s.get('pattern') or 'NONE')[:10]:<10}  "
                    f"{s.get('gate_result', '—')[:18]:<18}  "
                    f"{s.get('action', '?')}  "
                    f"{s.get('entry_source', '—')}"
                )

        if trades:
            lines.append("")
            lines.append("  Closed trades:")
            for t in trades[:3]:
                pnl_str = f"{t.get('pnl', 0):+.2f}"
                lines.append(
                    f"    {t.get('close_time_utc', '')[:16]}  {t.get('direction')}  "
                    f"{t.get('entry_price'):.2f} → {t.get('close_price'):.2f}  "
                    f"{pnl_str}  {t.get('pattern')}"
                )
        self.query_one("#history-panel", Static).update("\n".join(lines))

        # Risk row
        losses = risk.get("consecutive_losses_today", 0)
        max_l = 2
        daily_pnl = risk.get("daily_pnl", 0.0)
        weekly_pnl = risk.get("weekly_pnl", 0.0)
        obs_label = "YES" if observe else "NO"
        self.query_one("#risk-panel", Static).update(
            f"RISK  Losses today: {losses}/{max_l}   "
            f"Daily P&L: {daily_pnl:+.2f}   "
            f"Weekly P&L: {weekly_pnl:+.2f}   "
            f"Observe: {obs_label}\n"
            f"[K] Kill switch   [Q] Quit   Live policy: {policy_label}"
        )

    async def action_kill_switch(self) -> None:
        result = await self.push_screen_wait(KillSwitchModal())
        if result:
            # Toggle kill switch
            current = False
            try:
                with open(_CMD_PATH, "r") as f:
                    current = json.load(f).get("kill_switch", False)
            except Exception:
                pass
            new_value = not current
            self._write_cmd({"kill_switch": new_value})
            action = "activated" if new_value else "deactivated"
            self.notify(f"Kill switch {action}.")

    def _write_cmd(self, payload: dict) -> None:
        cmd_dir = os.path.dirname(_CMD_PATH)
        if cmd_dir:
            os.makedirs(cmd_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cmd_dir or ".", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, _CMD_PATH)


def run() -> None:
    XAUEXDashboard().run()


if __name__ == "__main__":
    run()
