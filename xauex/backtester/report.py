"""Backtest report generation."""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class BacktestReport:
    """Generate and print formatted backtest report."""

    def __init__(self, metrics: Dict[str, Any], start_date: str = "", end_date: str = ""):
        self.metrics = metrics
        self.start_date = start_date
        self.end_date = end_date

    def print_report(self) -> None:
        m = self.metrics
        period = m.get("period", f"{self.start_date} to {self.end_date}")

        print()
        print("=" * 50)
        print("  XAUEX BACKTEST REPORT")
        print("=" * 50)
        print(f"  Period:               {period}")
        print(f"  Instrument:           XAUUSD")
        print()
        print(f"  Signals detected:     {m.get('signals_detected', 0)}")
        print(f"  Trades taken:         {m.get('trades_taken', 0)}")
        print(f"    Skipped (session):  {m.get('skipped_session', 0)}")
        print(f"    Skipped (news):     {m.get('skipped_news', 0)}")
        print(f"    Skipped (risk):     {m.get('skipped_risk', 0)}")
        print()

        win_rate = m.get("win_rate", 0.0)
        pf = m.get("profit_factor", 0.0)
        pf_str = f"{pf:.2f}" if pf < 1e6 else "∞"
        total_pnl = m.get("total_pnl", 0.0)
        max_dd = m.get("max_drawdown", 0.0)
        max_cl = m.get("max_consecutive_losses", 0)

        print(f"  Win rate:             {win_rate:.1f}%")
        print(f"  Profit factor:        {pf_str}")
        pnl_sign = "+" if total_pnl >= 0 else ""
        print(f"  Total P&L:            {pnl_sign}£{total_pnl:.2f}")
        print(f"  Max drawdown:         -£{max_dd:.2f}")
        print(f"  Max consec. losses:   {max_cl}")
        print()

        breakdown = m.get("pattern_breakdown", {})
        if breakdown:
            print("  Pattern breakdown:")
            for pattern_name, stats in sorted(breakdown.items()):
                wins = stats.get("wins", 0)
                losses = stats.get("losses", 0)
                total = wins + losses
                pct = wins / max(total, 1) * 100
                print(f"    {pattern_name:<26} {wins}/{total}  wins ({pct:.1f}%)")
            print()

        print(f"  Weekly halt triggers: {m.get('weekly_halt_count', 0)} weeks")
        print(f"  Daily halt triggers:  {m.get('daily_halt_count', 0)} days")
        print("=" * 50)

        # Pass/fail criteria
        print()
        print("  PASS CRITERIA:")
        self._check("Profit factor ≥ 1.30", pf >= 1.30)
        self._check("Win rate ≥ 45%", win_rate >= 45.0)
        from config import load_config
        try:
            cfg = load_config()
            balance = 3000.0
        except Exception:
            balance = 3000.0
        max_dd_pct = max_dd / max(balance, 1) * 100
        self._check(f"Max drawdown ≤ 20% ({max_dd_pct:.1f}%)", max_dd_pct <= 20.0)
        self._check(f"Max consec. losses ≤ 6 ({max_cl})", max_cl <= 6)
        closed = m.get("closed_trades", 0)
        self._check(f"Completed trades ≥ 100 ({closed})", closed >= 100)
        print()

    @staticmethod
    def _check(label: str, passed: bool) -> None:
        icon = "✓" if passed else "✗"
        print(f"    [{icon}] {label}")
