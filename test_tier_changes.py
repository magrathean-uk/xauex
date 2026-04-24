#!/usr/bin/env python3
"""Test script to validate Tier 1 and 2 changes."""

from statistics import mean
from xauex.signal.direct_predictor import _calculate_atr, _price_features

def test_atr_calculation():
    """Test ATR calculation."""
    closes = [2450.0, 2451.0, 2450.5, 2452.0, 2451.5, 2453.0, 2452.5]
    atr = _calculate_atr(closes, periods=3)
    print(f"✓ ATR calculated: {atr:.4f}")
    assert atr >= 0, "ATR should be non-negative"
    assert atr > 0, "ATR should be positive with price movement"

def test_range_exhaustion_filter():
    """Test range exhaustion filter in price features."""
    state = {
        'recent_h1_closes': [2450.0, 2450.5, 2451.0, 2451.5, 2452.0, 2453.0, 2458.0, 2459.0],
        'levels': {
            'daily': {
                'low': 2448.0,
                'high': 2460.0,
            }
        },
        'runtime': {
            'latest_quote': {
                'mid': 2459.0,
                'bid': 2458.9,
                'ask': 2459.1,
                'updated_at_utc': '2026-04-24T14:00:00Z',
            }
        }
    }

    price_features = _price_features(state)
    print(f"✓ Price features: range_position={price_features['range_position']}, price_bias={price_features['price_bias']}")

    # Price 2459 with low=2448, high=2460: pos = (2459-2448)/(2460-2448) = 11/12 = 0.917 = UPPER_THIRD
    assert price_features['range_position'] == 'UPPER_THIRD', f"Expected UPPER_THIRD but got {price_features['range_position']}"

    # Check that BUY was downgraded to NEUTRAL when in UPPER_THIRD
    assert price_features['price_bias'] == 'NEUTRAL', f"Expected NEUTRAL (BUY downgraded) but got {price_features['price_bias']}"
    print(f"✓ Range exhaustion filter: BUY signal downgraded to NEUTRAL in UPPER_THIRD")

def test_atr_momentum_threshold():
    """Test ATR-based momentum threshold."""
    state = {
        'recent_h1_closes': [2450.0, 2450.5, 2451.0, 2451.5, 2452.0, 2453.0, 2453.5, 2453.0],
        'levels': {
            'daily': {
                'low': 2450.0,
                'high': 2454.0,
            }
        },
        'runtime': {
            'latest_quote': {
                'mid': 2453.0,
            }
        }
    }

    price_features = _price_features(state)
    atr = price_features.get('atr_14')
    threshold = price_features.get('momentum_threshold')

    print(f"✓ ATR: {atr:.4f}, Momentum threshold: {threshold:.4f}")
    assert threshold > 0, "Momentum threshold should be calculated"
    assert threshold <= atr, f"Threshold should be <= ATR (threshold={threshold}, atr={atr})"
    print(f"✓ ATR-aware momentum threshold: threshold = {threshold:.4f}")

def test_assurance_floor():
    """Test that assurance floor was raised to 0.55."""
    from xauex.main import build_xauex_assurance_profile
    from types import SimpleNamespace

    config = SimpleNamespace(
        xauex_session_low_confidence_protect_r=0.70,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.00,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
        xauex_memory_loss_threshold_r=-2.0,
    )

    # Test score just below new floor (0.54)
    signal_low = {
        'action': 'BUY',
        'confidence': 0.54,
        'consensus_state': 'aligned',
        'validator_status': 'approved',
        'validator_summary': 'approved',
        'decision_packet': {
            'input_freshness': {}
        },
        'memory_summary': {
            'net_pnl': 0.0,
            'trade_count': 0,
        }
    }

    assurance = build_xauex_assurance_profile(signal_low, config)
    assert not assurance.allow_trade, f"Score 0.54 should be blocked, got {assurance.reason}"
    print(f"✓ Assurance floor at 0.55: score=0.54 blocked ({assurance.reason})")

    # Test score at new floor (0.55)
    signal_ok = {
        'action': 'BUY',
        'confidence': 0.55,
        'consensus_state': 'aligned',
        'validator_status': 'approved',
        'validator_summary': 'approved',
        'decision_packet': {
            'input_freshness': {}
        },
        'memory_summary': {
            'net_pnl': 0.0,
            'trade_count': 0,
        }
    }

    assurance = build_xauex_assurance_profile(signal_ok, config)
    assert assurance.allow_trade, f"Score 0.55 should be allowed, got {assurance.reason}"
    assert assurance.bucket == 'medium', f"Expected medium bucket, got {assurance.bucket}"
    print(f"✓ Assurance floor at 0.55: score=0.55 allowed (bucket={assurance.bucket})")

def test_loss_memory_gate():
    """Test hard memory gate that tightens targets on loss streaks."""
    from xauex.main import build_xauex_assurance_profile
    from types import SimpleNamespace

    config = SimpleNamespace(
        xauex_session_low_confidence_protect_r=0.70,
        xauex_session_protect_r=0.85,
        xauex_session_high_confidence_protect_r=1.00,
        xauex_session_trail_r=1.35,
        xauex_session_low_confidence_protect_lock_r=0.35,
        xauex_session_protect_lock_r=0.30,
        xauex_session_high_confidence_protect_lock_r=0.25,
        xauex_memory_loss_threshold_r=-2.0,
    )

    # Test with no losses
    signal_no_loss = {
        'action': 'BUY',
        'confidence': 0.70,
        'consensus_state': 'aligned',
        'validator_status': 'approved',
        'validator_summary': 'well-supported',
        'decision_packet': {
            'input_freshness': {}
        },
        'memory_summary': {
            'net_pnl': 50.0,
            'trade_count': 5,
        }
    }

    assurance = build_xauex_assurance_profile(signal_no_loss, config)
    assert assurance.target_rr == 2.5, f"No loss: target_rr should be 2.5, got {assurance.target_rr}"
    print(f"✓ Loss memory gate: no losses, target_rr=2.5")

    # Test with loss streak (> -100 GBP loss and >= 4 trades)
    signal_loss = {
        'action': 'BUY',
        'confidence': 0.70,
        'consensus_state': 'aligned',
        'validator_status': 'approved',
        'validator_summary': 'well-supported',
        'decision_packet': {
            'input_freshness': {}
        },
        'memory_summary': {
            'net_pnl': -120.0,  # < -100
            'trade_count': 5,   # >= 4
        }
    }

    assurance = build_xauex_assurance_profile(signal_loss, config)
    expected_target_rr = 2.5 * 0.5  # 1.25
    assert assurance.target_rr == expected_target_rr, f"Loss streak: target_rr should be {expected_target_rr}, got {assurance.target_rr}"
    assert "_LOSS_MEMORY_TIGHTENED" in assurance.reason, f"Expected tightened marker in reason, got {assurance.reason}"
    print(f"✓ Loss memory gate: loss streak detected, target_rr={assurance.target_rr} (tightened)")

if __name__ == '__main__':
    print("Testing Tier 1 and Tier 2 changes...")
    print()

    print("=== ATR Calculation ===")
    test_atr_calculation()
    print()

    print("=== Range Exhaustion Filter ===")
    test_range_exhaustion_filter()
    print()

    print("=== ATR-Based Momentum Threshold ===")
    test_atr_momentum_threshold()
    print()

    print("Note: Assurance floor and loss memory gate tests require ctrader_open_api")
    print("which is not available in this test environment. These changes can be")
    print("verified by reviewing the code or running in the deployment environment.")
    print()

    print("✅ Direct predictor tests passed!")
    print("✅ Range exhaustion filter: working correctly")
    print("✅ ATR-aware momentum threshold: working correctly")
