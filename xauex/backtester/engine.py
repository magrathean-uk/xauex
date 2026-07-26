"""Backtest engine simulating bot logic on historical data."""

import argparse
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

from xauex.bot.patterns.detector import PatternDetector, Candle, PatternType
from xauex.bot.filters.session import SessionFilter
from xauex.bot.risk.sizing import calculate_lot_size
from xauex.bot.risk.gates import RiskGates, RiskState
from xauex.bot.levels.htf_levels import HTFLevels
from xauex.bot.api.models import SymbolSpec
from xauex.backtester.loader import OHLCBar, TickLoader
from xauex.backtester.report import BacktestReport

logger = logging.getLogger(__name__)


@dataclass
class _OpenTrade:
    """Simulated open position."""
    direction: str               # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    pattern: PatternType
    entry_bar_index: int


class _MockConfig:
    """Minimal config for backtest (read from CLI args)."""
    def __init__(
        self,
        risk_percent=1.0,
        max_open_trades=2,
        sl_min_dollars=10.0,
        sl_max_dollars=15.0,
        level_proximity_dollars=3.0,
        news_block_minutes=30,
        weekly_stop_pct=5.0,
        max_consecutive_losses=3,
        observe_only=True,
    ):
        self.risk_percent = risk_percent
        self.max_open_trades = max_open_trades
        self.sl_min_dollars = sl_min_dollars
        self.sl_max_dollars = sl_max_dollars
        self.level_proximity_dollars = level_proximity_dollars
        self.news_block_minutes = news_block_minutes
        self.weekly_stop_pct = weekly_stop_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.observe_only = observe_only


class _HTFLevelManagerStub:
    """
    Simplified level manager for backtesting.
    Computes levels from bars already loaded; no API calls.
    """

    def __init__(self, config):
        self.config = config
        self._raw: Optional[HTFLevels] = None
        self._levels: List[float] = []
        self._current_week_start: Optional[datetime] = None

    def update_from_bars(self, bars: List[OHLCBar], current_bar_time: datetime) -> None:
        """Refresh levels when a new weekly bar opens."""
        # Identify bars that completed before this bar (closed)
        closed = [b for b in bars if b.open_time < current_bar_time]
        if len(closed) < 4:
            return  # Not enough history

        # Monthly: group by month
        monthly: Dict[str, List[OHLCBar]] = defaultdict(list)
        for b in closed:
            monthly[b.open_time.strftime("%Y-%m")].append(b)

        months = sorted(monthly.keys())
        if len(months) < 2:
            return
        prev_month_bars = monthly[months[-2]]
        mn_o = prev_month_bars[0].open
        mn_h = max(b.high for b in prev_month_bars)
        mn_l = min(b.low for b in prev_month_bars)
        mn_c = prev_month_bars[-1].close

        # Weekly: group by ISO week
        weekly: Dict[str, List[OHLCBar]] = defaultdict(list)
        for b in closed:
            weekly[b.open_time.strftime("%G-%V")].append(b)
        weeks = sorted(weekly.keys())
        if len(weeks) < 2:
            return
        prev_week_bars = weekly[weeks[-2]]
        wk_o = prev_week_bars[0].open
        wk_h = max(b.high for b in prev_week_bars)
        wk_l = min(b.low for b in prev_week_bars)
        wk_c = prev_week_bars[-1].close

        self._raw = HTFLevels(
            mn_open=mn_o, mn_high=mn_h, mn_low=mn_l, mn_close=mn_c,
            wk_open=wk_o, wk_high=wk_h, wk_low=wk_l, wk_close=wk_c,
            last_refresh_utc=current_bar_time,
            weekly_bar_open_time_utc=prev_week_bars[0].open_time,
        )
        raw_vals = sorted({mn_o, mn_h, mn_l, mn_c, wk_o, wk_h, wk_l, wk_c})
        self._levels = self._deduplicate(raw_vals)

    def _deduplicate(self, levels: List[float]) -> List[float]:
        if not levels:
            return []
        result = [levels[0]]
        for lvl in levels[1:]:
            if abs(lvl - result[-1]) <= 1.0:
                result[-1] = (result[-1] + lvl) / 2
            else:
                result.append(lvl)
        return result

    def price_at_level(self, price: float) -> Optional[float]:
        prox = self.config.level_proximity_dollars
        for lvl in self._levels:
            if abs(price - lvl) <= prox:
                return lvl
        return None

    def next_level_from(self, price: float, direction: int) -> Optional[float]:
        if direction > 0:
            above = [level for level in self._levels if level > price]
            return min(above) if above else None
        else:
            below = [level for level in self._levels if level < price]
            return max(below) if below else None


# ─────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Backtest strategy against historical XAUUSD tick data.

    Imports and reuses the actual bot modules (not simplified versions).
    """

    LOT_SIZE = 100.0        # XAUUSD: 1 lot = 100 oz
    VOLUME_MIN = 0.01
    VOLUME_STEP = 0.01

    def __init__(
        self,
        data_dir: Path,
        start_date: str,
        end_date: str,
        config=None,
        starting_balance: float = 3000.0,
    ):
        self.data_dir = Path(data_dir)
        self.start_date = start_date
        self.end_date = end_date
        self.config = config or _MockConfig()
        self.starting_balance = starting_balance

        self._session = SessionFilter()
        self._pattern = PatternDetector(self.config)
        self._level_mgr = _HTFLevelManagerStub(self.config)
        self._risk_state = RiskState()
        self._risk_gates = RiskGates(self.config, self._risk_state)

        self._symbol_spec = SymbolSpec(
            symbol="XAUUSD",
            lot_size=self.LOT_SIZE,
            volume_min=self.VOLUME_MIN,
            volume_max=100.0,
            volume_step=self.VOLUME_STEP,
            digits=2,
            pip_value=0.01,
        )

    def run(self) -> dict:
        """Run backtest. Returns metrics dict."""
        loader = TickLoader(self.data_dir)
        bars = loader.load_bars(self.start_date, self.end_date)
        if not bars:
            logger.error("[BACKTEST] No bars loaded.")
            return {}

        logger.info("[BACKTEST] Loaded %d H1 bars.", len(bars))
        return self._simulate(bars)

    def _simulate(self, bars: List[OHLCBar]) -> dict:
        balance = self.starting_balance
        equity = balance
        peak_equity = balance

        signals_detected = 0
        trades_taken = 0
        skipped_session = 0
        skipped_news = 0
        skipped_risk = 0

        open_trades: List[_OpenTrade] = []
        closed_trades = []
        weekly_halt_count = 0
        daily_halt_count = 0

        pattern_stats: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "losses": 0})

        for i, bar in enumerate(bars):
            if i < 2:
                self._level_mgr.update_from_bars(bars[:i], bar.open_time)
                continue

            bar_time = bar.open_time

            # Update levels if needed (pass all bars up to this point)
            self._level_mgr.update_from_bars(bars[:i], bar_time)

            # Check open position exits (tick-level)
            still_open = []
            for trade in open_trades:
                hit = self._check_exit(trade, bar)
                if hit is not None:
                    pnl = self._compute_pnl(trade, hit)
                    balance += pnl
                    outcome = "WIN" if pnl > 0 else "LOSS"
                    closed_trades.append({"pnl": pnl, "pattern": trade.pattern, "outcome": outcome})
                    pattern_key = trade.pattern.name
                    if pnl > 0:
                        pattern_stats[pattern_key]["wins"] += 1
                    else:
                        pattern_stats[pattern_key]["losses"] += 1
                    self._risk_gates.record_trade_closed(pnl)
                else:
                    still_open.append(trade)
            open_trades = still_open

            # Update equity and drawdown
            equity = balance  # simplified (no unrealised P&L in backtest)
            if equity > peak_equity:
                peak_equity = equity

            self._risk_gates.set_open_position_count(len(open_trades))

            # Gate: session
            tradeable, reason = self._session.is_tradeable(bar_time)
            if not tradeable:
                skipped_session += 1
                continue

            # Gate: risk
            can_trade, reason = self._risk_gates.can_trade()
            if not can_trade:
                skipped_risk += 1
                if reason == "WEEKLY_DRAWDOWN_LIMIT":
                    weekly_halt_count += 1
                elif reason == "DAILY_CONSECUTIVE_LOSS_LIMIT":
                    daily_halt_count += 1
                continue

            # Level proximity
            level = self._level_mgr.price_at_level(bar.close)
            if level is None:
                continue

            # Pattern detection
            prev_bar = bars[i - 1]
            prev_c = Candle(open=prev_bar.open, high=prev_bar.high, low=prev_bar.low, close=prev_bar.close)
            sig_c = Candle(open=bar.open, high=bar.high, low=bar.low, close=bar.close)

            result = self._pattern.detect(prev_c, sig_c, level)
            if result.pattern_type == PatternType.NONE:
                continue

            signals_detected += 1

            # Lot sizing
            direction = 1 if result.is_bullish else -1
            sl_price = (
                level - self.config.sl_min_dollars if direction > 0
                else level + self.config.sl_min_dollars
            )
            lot = calculate_lot_size(
                account_balance=balance,
                entry_price=level,
                stop_loss_price=sl_price,
                symbol_spec=self._symbol_spec,
                current_spread_usd=bar.max_spread,
                config=self.config,
            )
            if lot is None:
                skipped_risk += 1
                continue

            # Entry: next bar open + spread
            if i + 1 >= len(bars):
                continue
            next_bar = bars[i + 1]
            half_spread = next_bar.max_spread / 2.0
            if direction > 0:
                entry = next_bar.open + half_spread
            else:
                entry = next_bar.open - half_spread

            tp = self._level_mgr.next_level_from(level, direction)
            if tp is None:
                sl_dist = abs(entry - sl_price)
                tp = entry + direction * 2 * sl_dist

            trades_taken += 1
            open_trades.append(_OpenTrade(
                direction="LONG" if direction > 0 else "SHORT",
                entry_price=entry,
                stop_loss=sl_price,
                take_profit=tp,
                lot_size=lot,
                pattern=result.pattern_type,
                entry_bar_index=i + 1,
            ))

        # Force-close any remaining open positions at last bar close
        if bars and open_trades:
            last_price = bars[-1].close
            for trade in open_trades:
                pnl = self._compute_pnl(trade, last_price)
                balance += pnl
                closed_trades.append({"pnl": pnl, "pattern": trade.pattern, "outcome": "OPEN"})

        # Compute metrics
        wins = [t for t in closed_trades if t["pnl"] > 0]
        losses = [t for t in closed_trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in closed_trades)
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
        win_rate = len(wins) / max(len(closed_trades), 1) * 100

        # Max drawdown
        running_balance = self.starting_balance
        peak = running_balance
        max_dd = 0.0
        for t in closed_trades:
            running_balance += t["pnl"]
            if running_balance > peak:
                peak = running_balance
            dd = peak - running_balance
            if dd > max_dd:
                max_dd = dd

        # Max consecutive losses
        max_consec = consec = 0
        for t in closed_trades:
            if t["pnl"] <= 0:
                consec += 1
                max_consec = max(max_consec, consec)
            else:
                consec = 0

        return {
            "period": f"{self.start_date} to {self.end_date}",
            "signals_detected": signals_detected,
            "trades_taken": trades_taken,
            "closed_trades": len(closed_trades),
            "skipped_session": skipped_session,
            "skipped_news": skipped_news,
            "skipped_risk": skipped_risk,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "max_drawdown": max_dd,
            "max_consecutive_losses": max_consec,
            "pattern_breakdown": dict(pattern_stats),
            "weekly_halt_count": weekly_halt_count,
            "daily_halt_count": daily_halt_count,
        }

    def _check_exit(self, trade: _OpenTrade, bar: OHLCBar) -> Optional[float]:
        """
        Return exit price if SL or TP hit during bar (tick-level check).
        Returns None if still open.
        """
        if trade.direction == "LONG":
            for tick in bar.ticks:
                if tick.bid <= trade.stop_loss:
                    return trade.stop_loss   # no slippage model
                if tick.ask >= trade.take_profit:
                    return trade.take_profit
        else:
            for tick in bar.ticks:
                if tick.ask >= trade.stop_loss:
                    return trade.stop_loss
                if tick.bid <= trade.take_profit:
                    return trade.take_profit
        return None

    def _compute_pnl(self, trade: _OpenTrade, close_price: float) -> float:
        """Compute P&L in account currency (GBP)."""
        if trade.direction == "LONG":
            pip_move = close_price - trade.entry_price
        else:
            pip_move = trade.entry_price - close_price
        # 1 lot XAUUSD = 100 oz; P&L in USD ≈ GBP at ~0.80 rate (simplified)
        pnl_usd = pip_move * trade.lot_size * self.LOT_SIZE
        pnl_gbp = pnl_usd * 0.80   # rough USD→GBP conversion
        return pnl_gbp


# ─────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="XAUEX Backtester")
    parser.add_argument("--data", required=True, help="Path to Dukascopy tick data directory")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=3000.0, help="Starting balance (GBP)")
    args = parser.parse_args()

    engine = BacktestEngine(
        data_dir=args.data,
        start_date=args.start,
        end_date=args.end,
        starting_balance=args.balance,
    )
    metrics = engine.run()
    if metrics:
        report = BacktestReport(metrics, start_date=args.start, end_date=args.end)
        report.print_report()
