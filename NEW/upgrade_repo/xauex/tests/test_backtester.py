"""Tests for backtester — synthetic tick data, no Dukascopy download needed."""

import csv
import os
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from backtester.loader import Tick, OHLCBar, TickLoader
from backtester.report import BacktestReport


# ─────────────────────────────────────────────────────────
# Synthetic CSV generation helpers
# ─────────────────────────────────────────────────────────

def _write_tick_csv(path: str, ticks: list) -> None:
    """Write synthetic ticks to a CSV in duka format."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["DateTime", "Bid", "Ask", "BidVolume", "AskVolume"])
        for ts, bid, ask in ticks:
            writer.writerow([ts.isoformat(), f"{bid:.2f}", f"{ask:.2f}", "1", "1"])


def _make_tick_at(hour: int, minute: int = 0, bid: float = 2720.0) -> tuple:
    ts = datetime(2026, 3, 10, hour, minute, 0, tzinfo=timezone.utc)
    return ts, bid, bid + 0.50  # spread = 0.50


# ─────────────────────────────────────────────────────────
# TickLoader tests
# ─────────────────────────────────────────────────────────

class TestTickLoader:

    def test_loads_csv_and_returns_bars(self, tmp_path):
        """TickLoader with a valid CSV returns H1 OHLCBar objects."""
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [
            _make_tick_at(9, 0, 2718.0),
            _make_tick_at(9, 15, 2720.0),
            _make_tick_at(9, 45, 2722.0),
            _make_tick_at(9, 59, 2721.0),
            _make_tick_at(10, 0, 2723.0),  # new bar
        ]
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")

        assert len(bars) == 2   # 09:00 bar and 10:00 bar

    def test_bar_ohlc_correct(self, tmp_path):
        """H1 bar OHLC computed from mid prices correctly."""
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [
            _make_tick_at(9, 0,  2710.0),
            _make_tick_at(9, 20, 2730.0),
            _make_tick_at(9, 40, 2700.0),
            _make_tick_at(9, 55, 2720.0),
        ]
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")

        assert len(bars) == 1
        bar = bars[0]
        # mid = (bid + ask) / 2 = bid + 0.25
        assert abs(bar.open  - 2710.25) < 1e-6
        assert abs(bar.high  - 2730.25) < 1e-6
        assert abs(bar.low   - 2700.25) < 1e-6
        assert abs(bar.close - 2720.25) < 1e-6

    def test_tick_count_correct(self, tmp_path):
        """tick_count matches number of ticks in bar."""
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [_make_tick_at(9, i * 10) for i in range(6)]  # 6 ticks in 09:xx
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")
        assert bars[0].tick_count == 6

    def test_ticks_retained_per_bar(self, tmp_path):
        """Bar.ticks list populated in Python path; empty in Rust path (by design)."""
        from backtester.loader import _RUST_AVAILABLE
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [_make_tick_at(9, i * 5) for i in range(4)]
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")
        assert len(bars) == 1
        if _RUST_AVAILABLE:
            # Rust path: ticks list is empty (bar aggregates only — saves memory)
            assert bars[0].tick_count == 4
        else:
            assert len(bars[0].ticks) == 4

    def test_empty_csv_returns_no_bars(self, tmp_path):
        """CSV with headers only → no bars."""
        csv_path = str(tmp_path / "ticks.csv")
        with open(csv_path, "w") as f:
            f.write("DateTime,Bid,Ask,BidVolume,AskVolume\n")

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")
        assert bars == []

    def test_date_range_filter(self, tmp_path):
        """Ticks outside date range excluded."""
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [
            (datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc), 2710.0, 2710.5),  # excluded
            (datetime(2026, 3, 10, 10, 0, tzinfo=timezone.utc), 2720.0, 2720.5), # included
            (datetime(2026, 3, 11, 10, 0, tzinfo=timezone.utc), 2730.0, 2730.5), # excluded
        ]
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")
        assert len(bars) == 1
        assert abs(bars[0].open - 2720.25) < 1e-6

    def test_max_spread_computed(self, tmp_path):
        """max_spread = worst spread seen during the bar."""
        csv_path = str(tmp_path / "ticks.csv")
        ticks = [
            (datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc), 2720.0, 2720.5),   # spread 0.5
            (datetime(2026, 3, 10, 9, 30, tzinfo=timezone.utc), 2720.0, 2722.0),  # spread 2.0
        ]
        _write_tick_csv(csv_path, ticks)

        loader = TickLoader(tmp_path)
        bars = loader.load_bars("2026-03-10", "2026-03-10")
        assert abs(bars[0].max_spread - 2.0) < 1e-6


# ─────────────────────────────────────────────────────────
# BacktestReport tests
# ─────────────────────────────────────────────────────────

class TestBacktestReport:

    def test_print_report_no_error(self, capsys):
        """Report prints without exception given a complete metrics dict."""
        metrics = {
            "period": "2022-01-01 to 2025-12-31",
            "signals_detected": 312,
            "trades_taken": 187,
            "closed_trades": 185,
            "skipped_session": 68,
            "skipped_news": 31,
            "skipped_risk": 26,
            "win_rate": 54.5,
            "profit_factor": 1.42,
            "total_pnl": 2840.0,
            "max_drawdown": 412.0,
            "max_consecutive_losses": 4,
            "pattern_breakdown": {
                "BULLISH_PIN_BAR": {"wins": 28, "losses": 20},
                "BEARISH_PIN_BAR": {"wins": 24, "losses": 17},
            },
            "weekly_halt_count": 8,
            "daily_halt_count": 14,
        }
        report = BacktestReport(metrics, "2022-01-01", "2025-12-31")
        report.print_report()   # must not raise

        captured = capsys.readouterr()
        assert "XAUEX BACKTEST REPORT" in captured.out
        assert "54.5%" in captured.out
        assert "1.42" in captured.out
        assert "BULLISH_PIN_BAR" in captured.out

    def test_infinite_profit_factor_handled(self, capsys):
        """Profit factor = inf (no losses) printed as ∞."""
        metrics = {
            "period": "2026-01-01 to 2026-03-01",
            "signals_detected": 5,
            "trades_taken": 5,
            "closed_trades": 5,
            "skipped_session": 0,
            "skipped_news": 0,
            "skipped_risk": 0,
            "win_rate": 100.0,
            "profit_factor": float("inf"),
            "total_pnl": 500.0,
            "max_drawdown": 0.0,
            "max_consecutive_losses": 0,
            "pattern_breakdown": {},
            "weekly_halt_count": 0,
            "daily_halt_count": 0,
        }
        report = BacktestReport(metrics)
        report.print_report()
        captured = capsys.readouterr()
        assert "∞" in captured.out


# ─────────────────────────────────────────────────────────
# LevelStore tests
# ─────────────────────────────────────────────────────────

class TestLevelStore:

    def test_save_and_load_roundtrip(self, tmp_path):
        """Save HTFLevels → load → identical values."""
        from bot.levels.htf_levels import HTFLevels
        from bot.levels.level_store import LevelStore

        path = str(tmp_path / "levels.json")
        store = LevelStore(path)

        original = HTFLevels(
            day_open=2710.0, day_high=2742.0, day_low=2698.0, day_close=2731.0,
            mn_open=2680.0, mn_high=2750.0, mn_low=2610.0, mn_close=2740.0,
            wk_open=2720.0, wk_high=2745.0, wk_low=2700.0, wk_close=2735.0,
            last_refresh_utc=datetime(2026, 3, 9, 21, 0, tzinfo=timezone.utc),
            weekly_bar_open_time_utc=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
        )
        store.save = lambda lvl: store._save_sync(lvl)  # call sync in test
        store.save(original)
        restored = store._load_sync()

        assert restored is not None
        assert restored.mn_open == original.mn_open
        assert restored.mn_high == original.mn_high
        assert restored.wk_low == original.wk_low
        assert restored.last_refresh_utc == original.last_refresh_utc

    def test_load_missing_returns_none(self, tmp_path):
        """Load from non-existent path → None."""
        from bot.levels.level_store import LevelStore
        store = LevelStore(str(tmp_path / "nonexistent.json"))
        assert store._load_sync() is None

    def test_load_corrupt_returns_none(self, tmp_path):
        """Load from corrupt JSON → None."""
        from bot.levels.level_store import LevelStore
        path = str(tmp_path / "levels.json")
        with open(path, "w") as f:
            f.write("{not valid json")
        store = LevelStore(path)
        assert store._load_sync() is None

    def test_save_atomic_no_temp_files(self, tmp_path):
        """After save, no .tmp files left in directory."""
        from bot.levels.htf_levels import HTFLevels
        from bot.levels.level_store import LevelStore
        path = str(tmp_path / "levels.json")
        store = LevelStore(path)
        levels = HTFLevels(
            day_open=2710, day_high=2742, day_low=2698, day_close=2731,
            mn_open=2680, mn_high=2750, mn_low=2610, mn_close=2740,
            wk_open=2720, wk_high=2745, wk_low=2700, wk_close=2735,
            last_refresh_utc=datetime(2026, 3, 9, 21, 0, tzinfo=timezone.utc),
            weekly_bar_open_time_utc=datetime(2026, 3, 9, 0, 0, tzinfo=timezone.utc),
        )
        store._save_sync(levels)
        leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert leftovers == []
