from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from main import BotOrchestrator, _SCALP_BAR_LOOKBACK


UTC = timezone.utc


def test_current_strategy_health_reports_missing_scalp_h1_confirmation(minimal_config):
    minimal_config.strategy_mode = "SCALP_V1"
    orchestrator = BotOrchestrator(minimal_config)

    orchestrator._update_strategy_data_status(
        "SCALP_V1",
        execution_timeframe="M5",
        execution_bars=[{}] * 500,
        signal_index=10,
        daily_closes=[5000.0] * 120,
        h1_closes=[4800.0] * 42,
    )

    ready, reason = orchestrator.current_strategy_health()

    assert ready is False
    assert reason == "H1_CONFIRMATION_UNAVAILABLE"


@pytest.mark.asyncio
async def test_scalp_live_path_uses_deep_history_fetch(minimal_config):
    minimal_config.strategy_mode = "SCALP_V1"
    orchestrator = BotOrchestrator(minimal_config)
    orchestrator._fetch_execution_bars = AsyncMock(return_value=(None, None))
    orchestrator._finalize_candle = AsyncMock()
    orchestrator.write_state = AsyncMock()

    await orchestrator._process_scalp_v1_candle_close(
        price=4250.0,
        timestamp=datetime(2026, 3, 23, 10, 0, tzinfo=UTC),
        store="live",
        apply_risk_gates=False,
    )

    orchestrator._fetch_execution_bars.assert_awaited_once_with(
        "M5",
        count=_SCALP_BAR_LOOKBACK,
    )
    assert orchestrator._strategy_data_status["SCALP_V1"]["reason"] == "EXECUTION_BARS_UNAVAILABLE"
    orchestrator._finalize_candle.assert_awaited_once()
