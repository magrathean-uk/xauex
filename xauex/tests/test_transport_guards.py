import asyncio
import struct

import pytest

from bot.api.transport_guards import (
    DropOldestAsyncQueue,
    FrameLimitError,
    PendingFutureRegistry,
    parse_frame_length,
)


def test_frame_length_guard_rejects_oversized():
    header = struct.pack("!I", 99)
    with pytest.raises(FrameLimitError):
        parse_frame_length(header, max_frame_bytes=10)


def test_drop_oldest_queue_bounds_size():
    queue = DropOldestAsyncQueue(maxsize=2)
    queue.put_nowait("a")
    queue.put_nowait("b")
    result = queue.put_nowait("c")
    assert result.dropped_oldest is True
    assert queue.qsize() == 2
    assert queue.dropped == 1


def test_pending_future_cleanup_on_timeout():
    async def run():
        registry = PendingFutureRegistry()
        registry.create("req-1")
        with pytest.raises(asyncio.TimeoutError):
            await registry.wait("req-1", timeout=0.001)
        assert len(registry) == 0

    asyncio.run(run())
