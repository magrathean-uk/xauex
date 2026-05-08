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
    ctrader_tls_server_name: str
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
    xauex_mode: bool
    xauex_signal_path: str
    xauex_signal_max_age_seconds: int
    xauex_entry_timezone: str
    xauex_entry_start_london: str
    xauex_entry_end_london: str
    xauex_entry_second_start_london: str
    xauex_entry_second_end_london: str
    xauex_force_flat_london: str
    xauex_max_trades_per_day: int
    xauex_cash_take_profit_gbp: float
    xauex_cash_stop_loss_gbp: float
    xauex_risk_cap_percent: float
    xauex_confirm_spread_max_dollars: float
    xauex_confirm_max_age_seconds: int
    xauex_confidence_full_threshold: float
    xauex_confidence_medium_threshold: float
    xauex_medium_confidence_lot_multiplier: float
    xauex_low_confidence_lot_multiplier: float
    xauex_session_protect_r: float
    xauex_session_trail_r: float
    xauex_session_atr_multiplier: float
    xauex_session_structure_buffer_usd: float
    xauex_session_protect_buffer_usd: float
    xauex_session_protect_lock_r: float
    xauex_session_low_confidence_protect_lock_r: float
    xauex_session_high_confidence_protect_lock_r: float
    xauex_session_low_confidence_protect_r: float
    xauex_session_high_confidence_protect_r: float
    xauex_counter_signal_enabled: bool
    xauex_counter_signal_confidence: float
    xauex_counter_signal_risk_multiplier: float
    xauex_manual_command_path: str
    xauex_manual_command_secret: str
    xauex_manual_command_ledger_path: str
    xauex_event_journal_path: str
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
    health_check_host: str
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


def _hhmm_to_minutes(value: str, key: str) -> int:
    text = str(value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError):
        raise ConfigError(f"{key} must be in HH:MM format, got: {value!r}")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"{key} must be in HH:MM format, got: {value!r}")
    return hour * 60 + minute


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
    # Match the live deployment examples unless the operator overrides them.
    host          = os.getenv("CTRADER_HOST", "demo-uk-eqx-01.p.c-trader.com")
    port          = collect(_int_range, "CTRADER_PORT", 1, 65535, 5035)
    tls_server_name = os.getenv("CTRADER_TLS_SERVER_NAME", "connect.spotware.com").strip()
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
    xauex_mode = os.getenv("XAUEX_MODE", os.getenv("MIROFISH_MODE", "false")).lower() in ("true", "1", "yes")
    xauex_signal_path = os.getenv(
        "XAUEX_SIGNAL_PATH",
        os.getenv(
            "MIROFISH_SIGNAL_PATH",
            os.getenv("CMD_FILE_PATH", "/var/lib/xauex/cmd.json"),
        ),
    )
    xauex_signal_max_age_seconds = collect(
        _int_range,
        "XAUEX_SIGNAL_MAX_AGE_SECONDS",
        30,
        3600,
        int(os.getenv("MIROFISH_SIGNAL_MAX_AGE_SECONDS", "300")),
    )
    xauex_entry_timezone = os.getenv("XAUEX_ENTRY_TIMEZONE", os.getenv("MIROFISH_ENTRY_TIMEZONE", "Europe/London"))
    xauex_entry_start_london = os.getenv("XAUEX_ENTRY_START_LONDON", os.getenv("MIROFISH_ENTRY_START_LONDON", "08:00"))
    xauex_entry_end_london = os.getenv("XAUEX_ENTRY_END_LONDON", os.getenv("MIROFISH_ENTRY_END_LONDON", "08:05"))
    xauex_entry_second_start_london = os.getenv(
        "XAUEX_ENTRY_SECOND_START_LONDON",
        os.getenv("MIROFISH_ENTRY_SECOND_START_LONDON", "11:30"),
    )
    xauex_entry_second_end_london = os.getenv(
        "XAUEX_ENTRY_SECOND_END_LONDON",
        os.getenv("MIROFISH_ENTRY_SECOND_END_LONDON", "11:35"),
    )
    xauex_force_flat_london = os.getenv("XAUEX_FORCE_FLAT_LONDON", os.getenv("MIROFISH_FORCE_FLAT_LONDON", "15:00"))
    xauex_max_trades_per_day = collect(
        _int_range,
        "XAUEX_MAX_TRADES_PER_DAY",
        1,
        5,
        int(os.getenv("MIROFISH_MAX_TRADES_PER_DAY", "3")),
    )
    xauex_cash_take_profit_gbp = collect(
        _float_range,
        "XAUEX_CASH_TAKE_PROFIT_GBP",
        1.0,
        5000.0,
        float(os.getenv("MIROFISH_CASH_TAKE_PROFIT_GBP", "50.0")),
    )
    xauex_cash_stop_loss_gbp = collect(
        _float_range,
        "XAUEX_CASH_STOP_LOSS_GBP",
        1.0,
        5000.0,
        float(os.getenv("MIROFISH_CASH_STOP_LOSS_GBP", "50.0")),
    )
    xauex_risk_cap_percent = collect(
        _float_range,
        "XAUEX_RISK_CAP_PERCENT",
        0.1,
        10.0,
        float(os.getenv("MIROFISH_RISK_CAP_PERCENT", "1.0")),
    )
    xauex_confirm_spread_max_dollars = collect(
        _float_range,
        "XAUEX_CONFIRM_SPREAD_MAX_DOLLARS",
        0.05,
        10.0,
        float(os.getenv("MIROFISH_CONFIRM_SPREAD_MAX_DOLLARS", os.getenv("SCALP_SPREAD_MAX_DOLLARS", "1.0"))),
    )
    xauex_confirm_max_age_seconds = collect(
        _int_range,
        "XAUEX_CONFIRM_MAX_AGE_SECONDS",
        60,
        3600,
        int(os.getenv("XAUEX_CONFIRM_MAX_AGE_SECONDS", "600")),
    )
    xauex_confidence_full_threshold = collect(
        _float_range,
        "XAUEX_CONFIDENCE_FULL_THRESHOLD",
        0.0,
        1.0,
        float(os.getenv("MIROFISH_CONFIDENCE_FULL_THRESHOLD", "0.65")),
    )
    xauex_confidence_medium_threshold = collect(
        _float_range,
        "XAUEX_CONFIDENCE_MEDIUM_THRESHOLD",
        0.0,
        1.0,
        float(os.getenv("MIROFISH_CONFIDENCE_MEDIUM_THRESHOLD", "0.55")),
    )
    xauex_medium_confidence_lot_multiplier = collect(
        _float_range,
        "XAUEX_MEDIUM_CONFIDENCE_LOT_MULTIPLIER",
        0.1,
        1.0,
        float(os.getenv("MIROFISH_MEDIUM_CONFIDENCE_LOT_MULTIPLIER", "0.5")),
    )
    xauex_low_confidence_lot_multiplier = collect(
        _float_range,
        "XAUEX_LOW_CONFIDENCE_LOT_MULTIPLIER",
        0.1,
        1.0,
        float(os.getenv("MIROFISH_LOW_CONFIDENCE_LOT_MULTIPLIER", "0.25")),
    )
    xauex_session_protect_r = collect(
        _float_range,
        "XAUEX_SESSION_PROTECT_R",
        0.1,
        5.0,
        float(os.getenv("MIROFISH_SESSION_PROTECT_R", "0.85")),
    )
    xauex_session_trail_r = collect(
        _float_range,
        "XAUEX_SESSION_TRAIL_R",
        0.2,
        8.0,
        float(os.getenv("MIROFISH_SESSION_TRAIL_R", "1.35")),
    )
    xauex_session_atr_multiplier = collect(
        _float_range,
        "XAUEX_SESSION_ATR_MULTIPLIER",
        0.1,
        10.0,
        float(os.getenv("MIROFISH_SESSION_ATR_MULTIPLIER", "1.4")),
    )
    xauex_session_structure_buffer_usd = collect(
        _float_range,
        "XAUEX_SESSION_STRUCTURE_BUFFER_USD",
        0.1,
        50.0,
        float(os.getenv("MIROFISH_SESSION_STRUCTURE_BUFFER_USD", "2.5")),
    )
    xauex_session_protect_buffer_usd = collect(
        _float_range,
        "XAUEX_SESSION_PROTECT_BUFFER_USD",
        0.1,
        20.0,
        float(os.getenv("MIROFISH_SESSION_PROTECT_BUFFER_USD", "1.0")),
    )
    xauex_session_protect_lock_r = collect(
        _float_range,
        "XAUEX_SESSION_PROTECT_LOCK_R",
        0.0,
        2.0,
        float(os.getenv("MIROFISH_SESSION_PROTECT_LOCK_R", "0.30")),
    )
    xauex_session_low_confidence_protect_lock_r = collect(
        _float_range,
        "XAUEX_SESSION_LOW_CONFIDENCE_PROTECT_LOCK_R",
        0.0,
        2.0,
        float(os.getenv("MIROFISH_SESSION_LOW_CONFIDENCE_PROTECT_LOCK_R", "0.35")),
    )
    xauex_session_high_confidence_protect_lock_r = collect(
        _float_range,
        "XAUEX_SESSION_HIGH_CONFIDENCE_PROTECT_LOCK_R",
        0.0,
        2.0,
        float(os.getenv("MIROFISH_SESSION_HIGH_CONFIDENCE_PROTECT_LOCK_R", "0.25")),
    )
    xauex_session_low_confidence_protect_r = collect(
        _float_range,
        "XAUEX_SESSION_LOW_CONFIDENCE_PROTECT_R",
        0.1,
        5.0,
        float(os.getenv("MIROFISH_SESSION_LOW_CONFIDENCE_PROTECT_R", "0.7")),
    )
    xauex_session_high_confidence_protect_r = collect(
        _float_range,
        "XAUEX_SESSION_HIGH_CONFIDENCE_PROTECT_R",
        0.1,
        5.0,
        float(os.getenv("MIROFISH_SESSION_HIGH_CONFIDENCE_PROTECT_R", "1.0")),
    )
    xauex_counter_signal_enabled = os.getenv("XAUEX_COUNTER_SIGNAL_ENABLED", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    xauex_counter_signal_confidence = collect(
        _float_range,
        "XAUEX_COUNTER_SIGNAL_CONFIDENCE",
        0.45,
        0.75,
        0.58,
    )
    xauex_counter_signal_risk_multiplier = collect(
        _float_range,
        "XAUEX_COUNTER_SIGNAL_RISK_MULTIPLIER",
        0.05,
        1.0,
        0.5,
    )
    xauex_manual_command_path = os.getenv(
        "XAUEX_MANUAL_COMMAND_PATH",
        os.getenv("MIROFISH_MANUAL_COMMAND_PATH", "/var/lib/xauex/manual_trade_cmd.json"),
    )
    xauex_manual_command_secret = os.getenv("XAUEX_MANUAL_COMMAND_SECRET", "").strip()
    xauex_manual_command_ledger_path = os.getenv(
        "XAUEX_MANUAL_COMMAND_LEDGER_PATH",
        "/var/lib/xauex/manual_command_ids.json",
    ).strip()
    xauex_event_journal_path = os.getenv("XAUEX_EVENT_JOURNAL_PATH", "/var/lib/xauex/events.jsonl").strip()
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

    morning_start_minutes = collect(
        _hhmm_to_minutes, xauex_entry_start_london, "XAUEX_ENTRY_START_LONDON"
    )
    morning_end_minutes = collect(
        _hhmm_to_minutes, xauex_entry_end_london, "XAUEX_ENTRY_END_LONDON"
    )
    second_start_minutes = collect(
        _hhmm_to_minutes, xauex_entry_second_start_london, "XAUEX_ENTRY_SECOND_START_LONDON"
    )
    second_end_minutes = collect(
        _hhmm_to_minutes, xauex_entry_second_end_london, "XAUEX_ENTRY_SECOND_END_LONDON"
    )
    force_flat_minutes = collect(
        _hhmm_to_minutes, xauex_force_flat_london, "XAUEX_FORCE_FLAT_LONDON"
    )

    if (
        morning_start_minutes is not None
        and morning_end_minutes is not None
        and morning_end_minutes <= morning_start_minutes
    ):
        errors.append(
            "XAUEX_ENTRY_END_LONDON must be later than XAUEX_ENTRY_START_LONDON"
        )

    if (
        second_start_minutes is not None
        and second_end_minutes is not None
        and second_end_minutes <= second_start_minutes
    ):
        errors.append(
            "XAUEX_ENTRY_SECOND_END_LONDON must be later than XAUEX_ENTRY_SECOND_START_LONDON"
        )

    if (
        morning_end_minutes is not None
        and second_start_minutes is not None
        and second_start_minutes < morning_end_minutes
    ):
        errors.append(
            "XAUEX_ENTRY_SECOND_START_LONDON must be at or after XAUEX_ENTRY_END_LONDON"
        )

    if (
        second_end_minutes is not None
        and force_flat_minutes is not None
        and force_flat_minutes <= second_end_minutes
    ):
        errors.append(
            "XAUEX_FORCE_FLAT_LONDON must be later than XAUEX_ENTRY_SECOND_END_LONDON"
        )

    if (
        xauex_confidence_medium_threshold is not None
        and xauex_confidence_full_threshold is not None
        and xauex_confidence_medium_threshold > xauex_confidence_full_threshold
    ):
        errors.append(
            "XAUEX_CONFIDENCE_MEDIUM_THRESHOLD must be <= XAUEX_CONFIDENCE_FULL_THRESHOLD"
        )

    if (
        xauex_low_confidence_lot_multiplier is not None
        and xauex_medium_confidence_lot_multiplier is not None
        and xauex_low_confidence_lot_multiplier > xauex_medium_confidence_lot_multiplier
    ):
        errors.append(
            "XAUEX_LOW_CONFIDENCE_LOT_MULTIPLIER must be <= "
            "XAUEX_MEDIUM_CONFIDENCE_LOT_MULTIPLIER"
        )

    observe_only = os.getenv("OBSERVE_ONLY", "true").lower() == "true"

    health_check_host = os.getenv("HEALTH_CHECK_HOST", "127.0.0.1").strip() or "127.0.0.1"
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
        ctrader_tls_server_name=tls_server_name,
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
        xauex_mode=xauex_mode,
        xauex_signal_path=xauex_signal_path,
        xauex_signal_max_age_seconds=xauex_signal_max_age_seconds,
        xauex_entry_timezone=xauex_entry_timezone,
        xauex_entry_start_london=xauex_entry_start_london,
        xauex_entry_end_london=xauex_entry_end_london,
        xauex_entry_second_start_london=xauex_entry_second_start_london,
        xauex_entry_second_end_london=xauex_entry_second_end_london,
        xauex_force_flat_london=xauex_force_flat_london,
        xauex_max_trades_per_day=xauex_max_trades_per_day,
        xauex_cash_take_profit_gbp=xauex_cash_take_profit_gbp,
        xauex_cash_stop_loss_gbp=xauex_cash_stop_loss_gbp,
        xauex_risk_cap_percent=xauex_risk_cap_percent,
        xauex_confirm_spread_max_dollars=xauex_confirm_spread_max_dollars,
        xauex_confirm_max_age_seconds=xauex_confirm_max_age_seconds,
        xauex_confidence_full_threshold=xauex_confidence_full_threshold,
        xauex_confidence_medium_threshold=xauex_confidence_medium_threshold,
        xauex_medium_confidence_lot_multiplier=xauex_medium_confidence_lot_multiplier,
        xauex_low_confidence_lot_multiplier=xauex_low_confidence_lot_multiplier,
        xauex_session_protect_r=xauex_session_protect_r,
        xauex_session_trail_r=xauex_session_trail_r,
        xauex_session_atr_multiplier=xauex_session_atr_multiplier,
        xauex_session_structure_buffer_usd=xauex_session_structure_buffer_usd,
        xauex_session_protect_buffer_usd=xauex_session_protect_buffer_usd,
        xauex_session_protect_lock_r=xauex_session_protect_lock_r,
        xauex_session_low_confidence_protect_lock_r=xauex_session_low_confidence_protect_lock_r,
        xauex_session_high_confidence_protect_lock_r=xauex_session_high_confidence_protect_lock_r,
        xauex_session_low_confidence_protect_r=xauex_session_low_confidence_protect_r,
        xauex_session_high_confidence_protect_r=xauex_session_high_confidence_protect_r,
        xauex_counter_signal_enabled=xauex_counter_signal_enabled,
        xauex_counter_signal_confidence=xauex_counter_signal_confidence,
        xauex_counter_signal_risk_multiplier=xauex_counter_signal_risk_multiplier,
        xauex_manual_command_path=xauex_manual_command_path,
        xauex_manual_command_secret=xauex_manual_command_secret,
        xauex_manual_command_ledger_path=xauex_manual_command_ledger_path,
        xauex_event_journal_path=xauex_event_journal_path,
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
        health_check_host=health_check_host,
        health_check_port=health_check_port,
        state_file_path=state_file_path,
        cmd_file_path=cmd_file_path,
        log_file_path=log_file_path,
    )
