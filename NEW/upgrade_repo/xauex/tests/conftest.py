"""Shared pytest fixtures."""

import os
import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def tmp_state_path(tmp_path):
    """Temporary state.json path for tests."""
    return str(tmp_path / "state.json")


@pytest.fixture
def tmp_cmd_path(tmp_path):
    """Temporary cmd.json path for tests."""
    return str(tmp_path / "cmd.json")


@pytest.fixture
def minimal_config(tmp_state_path, tmp_cmd_path):
    """Minimal config object for tests — no .env needed."""
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
        enable_ema_pullback_entries = True
        ema_pullback_proximity_dollars = 2.5
        strategy_mode = "LEGACY_LEVELS"
        shadow_strategy_mode = "NONE"
        atr_period = 14
        ema_pullback_atr_multiplier = 1.0
        ema_pullback_lookback_bars = 6
        ema_min_separation_atr = 0.2
        ema_min_slope_dollars = 1.5
        ema_slope_lookback = 5
        atr_min_dollars = 6.0
        sl_buffer_atr_multiplier = 0.5
        sl_buffer_min_dollars = 3.0
        sl_total_min_dollars = 10.0
        ema_pullback_take_profit_rr = 2.0
        strategy_max_trades_per_day = 2
        strategy_reentry_cooldown_candles = 6
        consolidation_min_candles = 2
        consolidation_max_candles = 4
        consolidation_range_atr_max = 0.6
        macro_regime_path = "/tmp/macro_regime.json"
        macro_regime_max_age_minutes = 180
        macro_regime_confidence_threshold = 0.65
        trade_policy_path = "/tmp/trade_policy.json"
        trade_policy_max_age_minutes = 60
        scalp_fast_ema_period = 9
        scalp_slow_ema_period = 20
        scalp_atr_period = 14
        scalp_pullback_lookback_bars = 4
        scalp_pullback_atr_multiplier = 0.25
        scalp_touch_proximity_dollars = 1.5
        scalp_body_min_ratio = 0.45
        scalp_close_position_threshold = 0.35
        scalp_ema_distance_atr_min = 0.05
        scalp_atr_min_dollars = 3.0
        scalp_spread_max_dollars = 1.0
        scalp_stop_buffer_atr_multiplier = 0.35
        scalp_stop_buffer_min_dollars = 1.5
        scalp_sl_min_dollars = 3.0
        scalp_sl_max_dollars = 12.0
        scalp_take_profit_rr = 1.2
        scalp_max_trades_per_day = 12
        scalp_reentry_cooldown_bars = 2
        observe_only = True
        state_file_path = tmp_state_path
        cmd_file_path = tmp_cmd_path
        log_file_path = "/tmp/xauex_test.log"
    return Cfg()
