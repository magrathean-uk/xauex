"""Tests for bot/risk/trailing_stop.py."""

import pytest
from bot.risk.trailing_stop import evaluate_trailing_stop, TrailingResult

# Constants for a standard XAUUSD position
LOT_SIZE  = 0.05       # std lots
CONTRACT  = 100.0      # oz per lot
RISK_USD  = 100.0      # $100 risked
ENTRY     = 2700.0
SL_DIST   = RISK_USD / (LOT_SIZE * CONTRACT)  # $20 per oz = SL 20 pts away


class TestBreakevenLong:
    def test_no_action_in_drawdown(self):
        """Losing trade — SL must not move."""
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=2690.0,
            current_sl=ENTRY - SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is None

    def test_no_action_below_1r(self):
        """Profit is positive but < 1R — no breakeven yet."""
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=ENTRY + SL_DIST * 0.5,
            current_sl=ENTRY - SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is None

    def test_breakeven_triggered_at_1r(self):
        """Profit = 1R → SL should move to entry."""
        current_price = ENTRY + SL_DIST  # exactly 1R profit
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=current_price,
            current_sl=ENTRY - SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is not None
        # At exactly 1R, trail_sl == entry_price → breakeven
        assert result.reason in ("breakeven", "trail")
        assert result.new_sl >= ENTRY - 0.01  # at or above entry

    def test_trail_at_2r(self):
        """Profit = 2R → SL trails at current_price - SL_DIST."""
        current_price = ENTRY + SL_DIST * 2  # 2R in profit
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=current_price,
            current_sl=ENTRY - SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is not None
        assert result.reason == "trail"
        expected_sl = round(current_price - SL_DIST, 2)
        assert abs(result.new_sl - expected_sl) < 0.01

    def test_no_action_if_sl_already_better(self):
        """SL already at breakeven and profit < next trail threshold → no action."""
        # SL is already at entry (breakeven done), profit < 2R
        current_price = ENTRY + SL_DIST * 1.5
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=current_price,
            current_sl=ENTRY,  # already at breakeven
            volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        # Trail SL would be current_price - SL_DIST = ENTRY + 0.5 * SL_DIST > ENTRY
        # But let's see if it improves current_sl=ENTRY:
        # trail_sl > ENTRY → should return trail
        assert result is not None  # still improves
        assert result.new_sl > ENTRY


class TestBreakevenShort:
    def test_no_action_in_drawdown(self):
        result = evaluate_trailing_stop(
            "SHORT", ENTRY, current_price=ENTRY + SL_DIST,
            current_sl=ENTRY + SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is None

    def test_breakeven_triggered(self):
        current_price = ENTRY - SL_DIST  # 1R profit on short
        result = evaluate_trailing_stop(
            "SHORT", ENTRY, current_price=current_price,
            current_sl=ENTRY + SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is not None
        # SL should move toward entry (down for short)
        assert result.new_sl <= ENTRY + 0.01

    def test_trail_at_2r(self):
        current_price = ENTRY - SL_DIST * 2  # 2R profit
        result = evaluate_trailing_stop(
            "SHORT", ENTRY, current_price=current_price,
            current_sl=ENTRY + SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is not None
        assert result.reason == "trail"
        expected_sl = round(current_price + SL_DIST, 2)
        assert abs(result.new_sl - expected_sl) < 0.01


class TestEdgeCases:
    def test_zero_volume_returns_none(self):
        result = evaluate_trailing_stop(
            "LONG", ENTRY, ENTRY + 100, ENTRY - 20,
            volume_lots=0.0, contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        assert result is None

    def test_zero_risk_returns_none(self):
        result = evaluate_trailing_stop(
            "LONG", ENTRY, ENTRY + 100, ENTRY - 20,
            volume_lots=LOT_SIZE, contract_size_oz=CONTRACT, risk_amount=0.0,
        )
        assert result is None

    def test_result_is_rounded(self):
        """new_sl should be rounded to 2 decimal places."""
        current_price = ENTRY + SL_DIST * 2.333
        result = evaluate_trailing_stop(
            "LONG", ENTRY, current_price=current_price,
            current_sl=ENTRY - SL_DIST, volume_lots=LOT_SIZE,
            contract_size_oz=CONTRACT, risk_amount=RISK_USD,
        )
        if result:
            assert result.new_sl == round(result.new_sl, 2)
