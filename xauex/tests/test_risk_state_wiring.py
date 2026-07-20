from datetime import datetime, timezone
from types import SimpleNamespace

from bot.risk.gates import RiskGates, RiskState
from xauex.main import BotOrchestrator


def _gates(state: RiskState) -> RiskGates:
    return RiskGates(SimpleNamespace(), state)


def test_weekly_baseline_rolls_over_when_bot_starts_on_tuesday():
    state = RiskState(
        week_start_date_utc="2026-07-13",
        week_start_balance=2931.77,
        weekly_pnl=-20.29,
        weekly_halted=True,
    )

    _gates(state).ensure_period_baselines(
        2947.75,
        now_utc=datetime(2026, 7, 21, tzinfo=timezone.utc),
    )

    assert state.week_start_date_utc == "2026-07-20"
    assert state.week_start_balance == 2947.75
    assert state.weekly_pnl == 0.0
    assert state.weekly_halted is False


def test_same_week_restart_preserves_weekly_baseline():
    state = RiskState(
        week_start_date_utc="2026-07-20",
        week_start_balance=2947.75,
        weekly_pnl=-16.64,
    )

    _gates(state).ensure_period_baselines(
        2930.93,
        now_utc=datetime(2026, 7, 22, tzinfo=timezone.utc),
    )

    assert state.week_start_date_utc == "2026-07-20"
    assert state.week_start_balance == 2947.75
    assert state.weekly_pnl == -16.64


def test_risk_wiring_invariant_rejects_replaced_gate_object():
    orchestrator = BotOrchestrator.__new__(BotOrchestrator)
    orchestrator.risk_state = RiskState()
    orchestrator.risk_gates = _gates(orchestrator.risk_state)
    orchestrator.executor = SimpleNamespace(risk_gates=orchestrator.risk_gates)

    assert orchestrator._risk_state_wiring_error() is None

    orchestrator.risk_gates = _gates(RiskState())

    assert orchestrator._risk_state_wiring_error() == "RISK_STATE_WIRING_INVALID"
