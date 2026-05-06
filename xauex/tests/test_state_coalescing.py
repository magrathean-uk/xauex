from bot.state.coalescing import StateWriteCoalescer


def test_state_coalescer_skips_unchanged_payload():
    coalescer = StateWriteCoalescer(min_interval_seconds=1)
    assert coalescer.decide({"x": 1}, now=10).should_write is True
    decision = coalescer.decide({"x": 1}, now=20)
    assert decision.should_write is False
    assert decision.reason == "unchanged"


def test_state_coalescer_allows_critical_write():
    coalescer = StateWriteCoalescer(min_interval_seconds=10)
    assert coalescer.decide({"x": 1}, now=10).should_write is True
    decision = coalescer.decide({"x": 2}, critical=True, now=11)
    assert decision.should_write is True
    assert decision.reason == "critical"


def test_state_coalescer_coalesces_fast_noncritical_change():
    coalescer = StateWriteCoalescer(min_interval_seconds=10)
    assert coalescer.decide({"x": 1}, now=10).should_write is True
    decision = coalescer.decide({"x": 2}, now=11)
    assert decision.should_write is False
    assert decision.reason == "coalesced"
