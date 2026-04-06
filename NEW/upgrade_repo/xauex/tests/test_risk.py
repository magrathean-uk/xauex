"""Tests for risk management module — all spec cases from 05-RISK-MODULE.md."""

import pytest
from datetime import datetime, timezone, timedelta
from bot.risk.sizing import calculate_lot_size
from bot.risk.gates import RiskGates, RiskState
from bot.api.models import SymbolSpec


@pytest.fixture
def config():
    class Cfg:
        risk_percent = 1.0
        max_open_trades = 2
        sl_offset_dollars = 12.0
        sl_min_dollars = 10.0
        sl_max_dollars = 15.0
        level_proximity_dollars = 3.0
        execution_timeframe = "H1"
        news_block_minutes = 30
        weekly_stop_pct = 5.0
        daily_stop_pct = 2.0
        max_consecutive_losses = 3
        max_lot_size = 100.0
        inside_bar_expiry_candles = 3
        same_level_cooldown_candles = 4
        observe_only = True
    return Cfg()


@pytest.fixture
def symbol_spec():
    return SymbolSpec(
        symbol="XAUUSD",
        lot_size=100.0,       # 1 lot = 100 oz
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        digits=2,
        pip_value=0.01,
    )


@pytest.fixture
def state():
    return RiskState()


@pytest.fixture
def gates(config, state):
    return RiskGates(config, state)


# ─────────────────────────────────────────────────────────
# LOT SIZING
# ─────────────────────────────────────────────────────────

class TestLotSizing:
    """Lot size calculation tests (05-RISK-MODULE.md)."""

    def test_correct_calculation(self, config, symbol_spec):
        """£3,000 balance, 1% risk, $12 SL → correct lot value."""
        # risk = 3000 * 0.01 = £30
        # lot = 30 / (12 * 100) = 30 / 1200 = 0.025
        # floored to step 0.01 → 0.02
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2708.0,     # SL distance = 12
            symbol_spec=symbol_spec,
            current_spread_usd=0.5,     # spread 0.5, 3x = 1.5 < 12 ✓
            config=config,
        )
        assert result is not None
        # risk=30, sl=12, lot_size=30/(12*100)=0.025, floor(0.025/0.01)*0.01=0.02
        assert abs(result - 0.02) < 1e-9

    def test_below_minimum_returns_none(self, config, symbol_spec):
        """Computed lot below volume_min → returns None (never rounded up)."""
        # Use large SL to make lot tiny
        small_spec = SymbolSpec(
            symbol="XAUUSD", lot_size=100.0,
            volume_min=0.10,   # min is 0.10
            volume_max=100.0, volume_step=0.01,
            digits=2, pip_value=0.01,
        )
        # balance=100, risk=1.0%, sl=10 → lot = 1.0/(10*100) = 0.001 < 0.10 min
        result = calculate_lot_size(
            account_balance=100.0,
            entry_price=2720.0,
            stop_loss_price=2710.0,
            symbol_spec=small_spec,
            current_spread_usd=0.5,
            config=config,
        )
        assert result is None

    def test_sl_distance_too_tight_returns_none(self, config, symbol_spec):
        """SL distance < SL_MIN ($10) → returns None."""
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2711.0,     # distance = 9 < 10
            symbol_spec=symbol_spec,
            current_spread_usd=0.5,
            config=config,
        )
        assert result is None

    def test_sl_distance_too_wide_returns_none(self, config, symbol_spec):
        """SL distance > SL_MAX ($15) → returns None."""
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2704.0,     # distance = 16 > 15
            symbol_spec=symbol_spec,
            current_spread_usd=0.5,
            config=config,
        )
        assert result is None

    def test_sl_within_spread_returns_none(self, config, symbol_spec):
        """SL distance < 3× spread → returns None."""
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2708.0,     # sl_distance=12
            symbol_spec=symbol_spec,
            current_spread_usd=5.0,     # 3 * 5 = 15 > 12 → skip
            config=config,
        )
        assert result is None

    def test_lot_floored_to_step_not_rounded_up(self, config, symbol_spec):
        """Lot correctly floored to volume step (never rounded up)."""
        # balance=3000, risk=30, sl=13 → lot=30/(13*100)=0.02307...
        # floor(0.02307/0.01)*0.01 = floor(2.307)*0.01 = 2*0.01 = 0.02
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2707.0,     # sl=13
            symbol_spec=symbol_spec,
            current_spread_usd=0.5,
            config=config,
        )
        assert result is not None
        # Must be a multiple of volume_step and < raw calculated value
        raw = 3000 * 0.01 / (13 * 100)  # ≈ 0.02307
        assert result < raw
        assert round(result / symbol_spec.volume_step) == result / symbol_spec.volume_step

    def test_lot_capped_by_max_lot_size(self, config, symbol_spec):
        """Explicit max_lot_size cap should bound the live lot size."""
        config.max_lot_size = 0.01
        result = calculate_lot_size(
            account_balance=3000.0,
            entry_price=2720.0,
            stop_loss_price=2708.0,
            symbol_spec=symbol_spec,
            current_spread_usd=0.5,
            config=config,
        )
        assert result == 0.01


# ─────────────────────────────────────────────────────────
# RISK GATES
# ─────────────────────────────────────────────────────────

class TestRiskGates:
    """Risk gate tests (05-RISK-MODULE.md)."""

    def test_third_consecutive_loss_closes_gate(self, gates, state):
        """3rd consecutive loss → gate closed."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        gates.record_trade_closed(-30.0)
        gates.record_trade_closed(-30.0)
        gates.record_trade_closed(-30.0)  # 3rd loss

        ok, reason = gates.can_trade()
        assert ok is False
        assert reason == "DAILY_CONSECUTIVE_LOSS_LIMIT"

    def test_win_resets_consecutive_counter(self, gates, state):
        """Win after 2 losses → consecutive counter resets to 0."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        gates.record_trade_closed(-30.0)
        gates.record_trade_closed(-30.0)
        gates.record_trade_closed(60.0)   # win resets counter

        assert state.consecutive_losses_today == 0
        ok, reason = gates.can_trade()
        assert ok is True

    def test_counter_resets_at_new_utc_day(self, gates, state):
        """Counter resets to 0 at UTC midnight."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Simulate yesterday's date
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        state.losses_date_utc = yesterday
        state.consecutive_losses_today = 3  # should reset

        ok, reason = gates.can_trade()
        assert ok is True
        assert state.consecutive_losses_today == 0

    def test_weekly_reset_only_on_monday(self, gates, state):
        state.week_start_date_utc = "2026-03-11"  # Wednesday
        state.weekly_pnl = -40.0
        state.weekly_halted = True

        from unittest.mock import patch
        with patch("bot.risk.gates._today_utc", return_value="2026-03-17"):  # Tuesday
            gates._maybe_reset_weekly()

        assert state.weekly_halted is True
        assert state.weekly_pnl == -40.0

    def test_weekly_reset_on_new_monday(self, gates, state):
        state.week_start_date_utc = "2026-03-11"  # Wednesday
        state.weekly_pnl = -40.0
        state.weekly_halted = True

        from unittest.mock import patch
        with patch("bot.risk.gates._today_utc", return_value="2026-03-23"):  # Monday
            gates._maybe_reset_weekly()

        assert state.weekly_halted is False
        assert state.weekly_pnl == 0.0
        assert state.week_start_date_utc == "2026-03-23"

    def test_weekly_pnl_minus_4_9_percent_gate_open(self, gates, state):
        """Weekly P&L at -4.9% → gate open."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.weekly_pnl = -(3000.0 * 0.049)  # -4.9%

        ok, reason = gates.can_trade()
        assert ok is True

    def test_weekly_pnl_minus_5_percent_gate_closed(self, gates, state):
        """Weekly P&L at -5.0% → gate closed."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.weekly_pnl = -(3000.0 * 0.05)   # exactly -5.0%

        ok, reason = gates.can_trade()
        assert ok is False
        assert reason == "WEEKLY_DRAWDOWN_LIMIT"

    def test_daily_drawdown_gate_closed(self, gates, state):
        """Daily P&L at configured stop percentage closes the gate."""
        state.day_start_balance = 3000.0
        state.day_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.daily_pnl = -(3000.0 * 0.02)

        ok, reason = gates.can_trade()
        assert ok is False
        assert reason == "DAILY_DRAWDOWN_LIMIT"

    def test_weekly_gate_persists_through_win(self, gates, state):
        """Weekly gate remains closed after subsequent win."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.weekly_pnl = -(3000.0 * 0.05)   # -5.0% → triggered

        gates.can_trade()  # triggers halt flag
        assert state.weekly_halted is True

        # Win does NOT clear the weekly halt
        gates.record_trade_closed(500.0)
        assert state.weekly_halted is True

        ok, reason = gates.can_trade()
        assert ok is False
        assert reason == "WEEKLY_DRAWDOWN_LIMIT"

    def test_position_cap_gate_closed_at_max(self, gates, state):
        """2 open positions → position cap gate closed."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gates.set_open_position_count(2)

        ok, reason = gates.can_trade()
        assert ok is False
        assert reason == "MAX_POSITIONS_REACHED"

    def test_position_cap_gate_open_at_one(self, gates, state):
        """1 open position → gate open."""
        state.week_start_balance = 3000.0
        state.week_start_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state.losses_date_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        gates.set_open_position_count(1)

        ok, reason = gates.can_trade()
        assert ok is True

    def test_can_trade_initializes_missing_period_baselines(self, gates, state):
        """Passing current balance should initialize day/week baselines automatically."""
        ok, reason = gates.can_trade(3000.0)
        assert ok is True
        assert reason == "OK"
        assert state.week_start_balance == 3000.0
        assert state.day_start_balance == 3000.0

    def test_state_serialization_roundtrip(self, state):
        """Serialize risk state → deserialize → identical values."""
        state.consecutive_losses_today = 2
        state.losses_date_utc = "2026-03-10"
        state.weekly_pnl = -125.50
        state.week_start_balance = 3000.0
        state.week_start_date_utc = "2026-03-09"
        state.weekly_halted = True
        state.daily_halted = False

        serialized = state.to_dict()
        restored = RiskState.from_dict(serialized)

        assert restored.consecutive_losses_today == state.consecutive_losses_today
        assert restored.losses_date_utc == state.losses_date_utc
        assert abs(restored.weekly_pnl - state.weekly_pnl) < 1e-9
        assert abs(restored.week_start_balance - state.week_start_balance) < 1e-9
        assert restored.week_start_date_utc == state.week_start_date_utc
        assert restored.weekly_halted == state.weekly_halted
        assert restored.daily_halted == state.daily_halted
