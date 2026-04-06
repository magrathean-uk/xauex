"""
Configuration loader from .env file.

Loads and validates all required environment variables for the XAUEX bot.
Never hardcodes credentials or configuration values.
All validation happens at startup — the process exits loudly rather than
silently using unsafe defaults.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when a required configuration value is missing or invalid."""


@dataclass
class Config:
    """Configuration container for XAUEX bot."""

    # cTrader credentials
    ctrader_client_id: str
    ctrader_client_secret: str
    ctrader_host: str
    ctrader_port: int
    ctrader_account_id: str

    # OAuth tokens (empty before first auth run — that is valid)
    ctrader_access_token: str
    ctrader_refresh_token: str
    ctrader_token_expiry: int

    # Bot parameters
    risk_percent: float
    max_open_trades: int
    sl_offset_dollars: float
    sl_min_dollars: float
    sl_max_dollars: float
    level_proximity_dollars: float
    execution_timeframe: str
    news_block_minutes: int
    weekly_stop_pct: float
    daily_stop_pct: float
    max_consecutive_losses: int
    max_lot_size: float
    inside_bar_expiry_candles: int
    same_level_cooldown_candles: int
    enable_ema_pullback_entries: bool
    ema_pullback_proximity_dollars: float
    strategy_mode: str
    shadow_strategy_mode: str
    atr_period: int
    ema_pullback_atr_multiplier: float
    ema_pullback_lookback_bars: int
    ema_min_separation_atr: float
    ema_min_slope_dollars: float
    ema_slope_lookback: int
    atr_min_dollars: float
    sl_buffer_atr_multiplier: float
    sl_buffer_min_dollars: float
    sl_total_min_dollars: float
    ema_pullback_take_profit_rr: float
    strategy_max_trades_per_day: int
    strategy_reentry_cooldown_candles: int
    consolidation_min_candles: int
    consolidation_max_candles: int
    consolidation_range_atr_max: float
    macro_regime_path: str
    macro_regime_max_age_minutes: int
    macro_regime_confidence_threshold: float
    trade_policy_path: str
    trade_policy_max_age_minutes: int
    mirofish_mode: bool
    mirofish_signal_path: str
    mirofish_signal_max_age_seconds: int
    scalp_fast_ema_period: int
    scalp_slow_ema_period: int
    scalp_atr_period: int
    scalp_pullback_lookback_bars: int
    scalp_pullback_atr_multiplier: float
    scalp_touch_proximity_dollars: float
    scalp_body_min_ratio: float
    scalp_close_position_threshold: float
    scalp_ema_distance_atr_min: float
    scalp_atr_min_dollars: float
    scalp_spread_max_dollars: float
    scalp_stop_buffer_atr_multiplier: float
    scalp_stop_buffer_min_dollars: float
    scalp_sl_min_dollars: float
    scalp_sl_max_dollars: float
    scalp_take_profit_rr: float
    scalp_max_trades_per_day: int
    scalp_reentry_cooldown_bars: int

    # Observe-only mode (default True for safety)
    observe_only: bool

    # Health check endpoint
    health_check_port: int

    # File paths
    state_file_path: str
    cmd_file_path: str
    log_file_path: str


def _require(key: str, default: str | None = None) -> str:
    val = os.getenv(key, default)
    if not val:
        raise ConfigError(f"Missing required environment variable: {key}")
    return val


def _float_range(key: str, lo: float, hi: float, default: float) -> float:
    raw = os.getenv(key, str(default))
    try:
        val = float(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a number, got: {raw!r}")
    if not (lo <= val <= hi):
        raise ConfigError(f"{key}={val} out of valid range [{lo}, {hi}]")
    return val


def _int_range(key: str, lo: int, hi: int, default: int) -> int:
    raw = os.getenv(key, str(default))
    try:
        val = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be an integer, got: {raw!r}")
    if not (lo <= val <= hi):
        raise ConfigError(f"{key}={val} out of valid range [{lo}, {hi}]")
    return val


def _one_of(key: str, allowed: tuple[str, ...], default: str) -> str:
    raw = os.getenv(key, default).strip().upper()
    if raw not in allowed:
        allowed_text = ", ".join(allowed)
        raise ConfigError(f"{key} must be one of [{allowed_text}], got: {raw!r}")
    return raw


def load_config(env_file: str = ".env") -> Config:
    """Load and validate configuration from .env file.

    Raises ConfigError on any missing or out-of-range value.
    Token fields (access/refresh/expiry) are allowed to be empty —
    they are populated by auth.py before the bot is started live.
    """
    load_dotenv(env_file)

    errors: list[str] = []

    def collect(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ConfigError as exc:
            errors.append(str(exc))
            return None

    client_id     = collect(_require, "CTRADER_CLIENT_ID")
    client_secret = collect(_require, "CTRADER_CLIENT_SECRET")
    host          = os.getenv("CTRADER_HOST", "demo.ctraderapi.com")
    port          = collect(_int_range, "CTRADER_PORT", 1, 65535, 5035)
    account_id    = collect(_require, "CTRADER_ACCOUNT_ID")

    # Tokens: empty is OK pre-auth
    access_token  = os.getenv("CTRADER_ACCESS_TOKEN", "")
    refresh_token = os.getenv("CTRADER_REFRESH_TOKEN", "")
    try:
        token_expiry = int(os.getenv("CTRADER_TOKEN_EXPIRY", "0"))
    except ValueError:
        errors.append("CTRADER_TOKEN_EXPIRY must be an integer Unix timestamp")
        token_expiry = 0

    risk_percent           = collect(_float_range, "RISK_PERCENT", 0.01, 5.0, 1.0)
    max_open_trades        = collect(_int_range,   "MAX_OPEN_TRADES", 1, 10, 2)
    sl_offset_dollars      = collect(_float_range, "SL_OFFSET_DOLLARS", 1.0, 100.0, 12.0)
    sl_min_dollars         = collect(_float_range, "SL_MIN_DOLLARS", 1.0, 100.0, 10.0)
    sl_max_dollars         = collect(_float_range, "SL_MAX_DOLLARS", 1.0, 500.0, 15.0)
    level_proximity_dollars = collect(_float_range, "LEVEL_PROXIMITY_DOLLARS", 0.5, 50.0, 3.0)
    execution_timeframe    = collect(_one_of, "EXECUTION_TIMEFRAME", ("M5", "M15", "M30", "H1"), "H1")
    news_block_minutes     = collect(_int_range,   "NEWS_BLOCK_MINUTES", 5, 120, 30)
    weekly_stop_pct        = collect(_float_range, "WEEKLY_STOP_PCT", 0.5, 20.0, 5.0)
    daily_stop_pct         = collect(_float_range, "DAILY_STOP_PCT", 0.25, 10.0, 2.0)
    max_consecutive_losses = collect(_int_range,   "MAX_CONSECUTIVE_LOSSES", 1, 10, 3)
    max_lot_size           = collect(_float_range, "MAX_LOT_SIZE", 0.01, 100.0, 100.0)
    inside_bar_expiry_candles = collect(_int_range, "INSIDE_BAR_EXPIRY_CANDLES", 1, 20, 3)
    same_level_cooldown_candles = collect(_int_range, "SAME_LEVEL_COOLDOWN_CANDLES", 0, 20, 4)
    enable_ema_pullback_entries = os.getenv("ENABLE_EMA_PULLBACK_ENTRIES", "true").lower() == "true"
    ema_pullback_proximity_dollars = collect(
        _float_range,
        "EMA_PULLBACK_PROXIMITY_DOLLARS",
        0.25,
        20.0,
        2.5,
    )
    strategy_mode = collect(
        _one_of,
        "STRATEGY_MODE",
        ("LEGACY_LEVELS", "EMA_PULLBACK_H1", "SCALP_V1"),
        "LEGACY_LEVELS",
    )
    shadow_strategy_mode = collect(
        _one_of,
        "SHADOW_STRATEGY_MODE",
        ("NONE", "LEGACY_LEVELS", "EMA_PULLBACK_H1", "SCALP_V1"),
        "NONE",
    )
    atr_period = collect(_int_range, "ATR_PERIOD", 5, 100, 14)
    ema_pullback_atr_multiplier = collect(
        _float_range, "EMA_PULLBACK_ATR_MULTIPLIER", 0.1, 3.0, 1.0
    )
    ema_pullback_lookback_bars = collect(
        _int_range, "EMA_PULLBACK_LOOKBACK_BARS", 2, 20, 6
    )
    ema_min_separation_atr = collect(
        _float_range, "EMA_MIN_SEPARATION_ATR", 0.01, 5.0, 0.2
    )
    ema_min_slope_dollars = collect(
        _float_range, "EMA_MIN_SLOPE_DOLLARS", 0.0, 50.0, 1.5
    )
    ema_slope_lookback = collect(_int_range, "EMA_SLOPE_LOOKBACK", 1, 20, 5)
    atr_min_dollars = collect(_float_range, "ATR_MIN_DOLLARS", 0.5, 100.0, 6.0)
    sl_buffer_atr_multiplier = collect(
        _float_range, "SL_BUFFER_ATR_MULTIPLIER", 0.1, 3.0, 0.5
    )
    sl_buffer_min_dollars = collect(
        _float_range, "SL_BUFFER_MIN_DOLLARS", 0.5, 50.0, 3.0
    )
    sl_total_min_dollars = collect(
        _float_range, "SL_TOTAL_MIN_DOLLARS", 1.0, 200.0, 10.0
    )
    ema_pullback_take_profit_rr = collect(
        _float_range, "EMA_PULLBACK_TAKE_PROFIT_RR", 0.5, 10.0, 2.0
    )
    strategy_max_trades_per_day = collect(
        _int_range, "STRATEGY_MAX_TRADES_PER_DAY", 1, 10, 2
    )
    strategy_reentry_cooldown_candles = collect(
        _int_range, "STRATEGY_REENTRY_COOLDOWN_CANDLES", 0, 50, 6
    )
    consolidation_min_candles = collect(
        _int_range, "CONSOLIDATION_MIN_CANDLES", 1, 10, 2
    )
    consolidation_max_candles = collect(
        _int_range, "CONSOLIDATION_MAX_CANDLES", 2, 10, 4
    )
    consolidation_range_atr_max = collect(
        _float_range, "CONSOLIDATION_RANGE_ATR_MAX", 0.1, 2.0, 0.6
    )
    macro_regime_path = os.getenv("MACRO_REGIME_PATH", "/var/lib/xauex/macro_regime.json")
    macro_regime_max_age_minutes = collect(
        _int_range, "MACRO_REGIME_MAX_AGE_MINUTES", 5, 1440, 180
    )
    macro_regime_confidence_threshold = collect(
        _float_range, "MACRO_REGIME_CONFIDENCE_THRESHOLD", 0.0, 1.0, 0.65
    )
    trade_policy_path = os.getenv("TRADE_POLICY_PATH", "/var/lib/xauex/trade_policy.json")
    trade_policy_max_age_minutes = collect(
        _int_range, "TRADE_POLICY_MAX_AGE_MINUTES", 5, 1440, 60
    )
    mirofish_mode = os.getenv("MIROFISH_MODE", "false").lower() in ("true", "1", "yes")
    mirofish_signal_path = os.getenv(
        "MIROFISH_SIGNAL_PATH",
        os.getenv("CMD_FILE_PATH", "/var/lib/xauex/cmd.json"),
    )
    mirofish_signal_max_age_seconds = collect(
        _int_range, "MIROFISH_SIGNAL_MAX_AGE_SECONDS", 30, 3600, 300
    )
    scalp_fast_ema_period = collect(_int_range, "SCALP_FAST_EMA_PERIOD", 3, 50, 9)
    scalp_slow_ema_period = collect(_int_range, "SCALP_SLOW_EMA_PERIOD", 5, 100, 20)
    scalp_atr_period = collect(_int_range, "SCALP_ATR_PERIOD", 5, 100, 14)
    scalp_pullback_lookback_bars = collect(
        _int_range, "SCALP_PULLBACK_LOOKBACK_BARS", 2, 20, 4
    )
    scalp_pullback_atr_multiplier = collect(
        _float_range, "SCALP_PULLBACK_ATR_MULTIPLIER", 0.05, 2.0, 0.25
    )
    scalp_touch_proximity_dollars = collect(
        _float_range, "SCALP_TOUCH_PROXIMITY_DOLLARS", 0.1, 20.0, 1.5
    )
    scalp_body_min_ratio = collect(
        _float_range, "SCALP_BODY_MIN_RATIO", 0.1, 1.0, 0.45
    )
    scalp_close_position_threshold = collect(
        _float_range, "SCALP_CLOSE_POSITION_THRESHOLD", 0.05, 0.95, 0.35
    )
    scalp_ema_distance_atr_min = collect(
        _float_range, "SCALP_EMA_DISTANCE_ATR_MIN", 0.01, 2.0, 0.05
    )
    scalp_atr_min_dollars = collect(
        _float_range, "SCALP_ATR_MIN_DOLLARS", 0.25, 50.0, 3.0
    )
    scalp_spread_max_dollars = collect(
        _float_range, "SCALP_SPREAD_MAX_DOLLARS", 0.05, 10.0, 1.0
    )
    scalp_stop_buffer_atr_multiplier = collect(
        _float_range, "SCALP_STOP_BUFFER_ATR_MULTIPLIER", 0.05, 2.0, 0.35
    )
    scalp_stop_buffer_min_dollars = collect(
        _float_range, "SCALP_STOP_BUFFER_MIN_DOLLARS", 0.1, 20.0, 1.5
    )
    scalp_sl_min_dollars = collect(
        _float_range, "SCALP_SL_MIN_DOLLARS", 0.5, 50.0, 3.0
    )
    scalp_sl_max_dollars = collect(
        _float_range, "SCALP_SL_MAX_DOLLARS", 1.0, 100.0, 12.0
    )
    scalp_take_profit_rr = collect(
        _float_range, "SCALP_TAKE_PROFIT_RR", 0.5, 5.0, 1.2
    )
    scalp_max_trades_per_day = collect(
        _int_range, "SCALP_MAX_TRADES_PER_DAY", 1, 50, 12
    )
    scalp_reentry_cooldown_bars = collect(
        _int_range, "SCALP_REENTRY_COOLDOWN_BARS", 0, 50, 2
    )

    # SL consistency check
    if sl_min_dollars and sl_max_dollars and sl_min_dollars >= sl_max_dollars:
        errors.append(
            f"SL_MIN_DOLLARS ({sl_min_dollars}) must be less than "
            f"SL_MAX_DOLLARS ({sl_max_dollars})"
        )

    observe_only = os.getenv("OBSERVE_ONLY", "true").lower() == "true"

    health_check_port = collect(_int_range, "HEALTH_CHECK_PORT", 1024, 65535, 8051) or 8051

    state_file_path = os.getenv("STATE_FILE_PATH", "/var/lib/xauex/state.json")
    cmd_file_path   = os.getenv("CMD_FILE_PATH",   "/var/lib/xauex/cmd.json")
    log_file_path   = os.getenv("LOG_FILE_PATH",   "/var/log/xauex/xauex.log")

    if errors:
        bullet = "\n  • ".join(errors)
        raise ConfigError(f"Configuration errors:\n  • {bullet}")

    return Config(
        ctrader_client_id=client_id,
        ctrader_client_secret=client_secret,
        ctrader_host=host,
        ctrader_port=port,
        ctrader_account_id=account_id,
        ctrader_access_token=access_token,
        ctrader_refresh_token=refresh_token,
        ctrader_token_expiry=token_expiry,
        risk_percent=risk_percent,
        max_open_trades=max_open_trades,
        sl_offset_dollars=sl_offset_dollars,
        sl_min_dollars=sl_min_dollars,
        sl_max_dollars=sl_max_dollars,
        level_proximity_dollars=level_proximity_dollars,
        execution_timeframe=execution_timeframe,
        news_block_minutes=news_block_minutes,
        weekly_stop_pct=weekly_stop_pct,
        daily_stop_pct=daily_stop_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_lot_size=max_lot_size,
        inside_bar_expiry_candles=inside_bar_expiry_candles,
        same_level_cooldown_candles=same_level_cooldown_candles,
        enable_ema_pullback_entries=enable_ema_pullback_entries,
        ema_pullback_proximity_dollars=ema_pullback_proximity_dollars,
        strategy_mode=strategy_mode,
        shadow_strategy_mode=shadow_strategy_mode,
        atr_period=atr_period,
        ema_pullback_atr_multiplier=ema_pullback_atr_multiplier,
        ema_pullback_lookback_bars=ema_pullback_lookback_bars,
        ema_min_separation_atr=ema_min_separation_atr,
        ema_min_slope_dollars=ema_min_slope_dollars,
        ema_slope_lookback=ema_slope_lookback,
        atr_min_dollars=atr_min_dollars,
        sl_buffer_atr_multiplier=sl_buffer_atr_multiplier,
        sl_buffer_min_dollars=sl_buffer_min_dollars,
        sl_total_min_dollars=sl_total_min_dollars,
        ema_pullback_take_profit_rr=ema_pullback_take_profit_rr,
        strategy_max_trades_per_day=strategy_max_trades_per_day,
        strategy_reentry_cooldown_candles=strategy_reentry_cooldown_candles,
        consolidation_min_candles=consolidation_min_candles,
        consolidation_max_candles=consolidation_max_candles,
        consolidation_range_atr_max=consolidation_range_atr_max,
        macro_regime_path=macro_regime_path,
        macro_regime_max_age_minutes=macro_regime_max_age_minutes,
        macro_regime_confidence_threshold=macro_regime_confidence_threshold,
        trade_policy_path=trade_policy_path,
        trade_policy_max_age_minutes=trade_policy_max_age_minutes,
        mirofish_mode=mirofish_mode,
        mirofish_signal_path=mirofish_signal_path,
        mirofish_signal_max_age_seconds=mirofish_signal_max_age_seconds,
        scalp_fast_ema_period=scalp_fast_ema_period,
        scalp_slow_ema_period=scalp_slow_ema_period,
        scalp_atr_period=scalp_atr_period,
        scalp_pullback_lookback_bars=scalp_pullback_lookback_bars,
        scalp_pullback_atr_multiplier=scalp_pullback_atr_multiplier,
        scalp_touch_proximity_dollars=scalp_touch_proximity_dollars,
        scalp_body_min_ratio=scalp_body_min_ratio,
        scalp_close_position_threshold=scalp_close_position_threshold,
        scalp_ema_distance_atr_min=scalp_ema_distance_atr_min,
        scalp_atr_min_dollars=scalp_atr_min_dollars,
        scalp_spread_max_dollars=scalp_spread_max_dollars,
        scalp_stop_buffer_atr_multiplier=scalp_stop_buffer_atr_multiplier,
        scalp_stop_buffer_min_dollars=scalp_stop_buffer_min_dollars,
        scalp_sl_min_dollars=scalp_sl_min_dollars,
        scalp_sl_max_dollars=scalp_sl_max_dollars,
        scalp_take_profit_rr=scalp_take_profit_rr,
        scalp_max_trades_per_day=scalp_max_trades_per_day,
        scalp_reentry_cooldown_bars=scalp_reentry_cooldown_bars,
        observe_only=observe_only,
        health_check_port=health_check_port,
        state_file_path=state_file_path,
        cmd_file_path=cmd_file_path,
        log_file_path=log_file_path,
    )
