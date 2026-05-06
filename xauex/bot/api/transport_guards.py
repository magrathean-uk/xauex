"""Small guard helpers for cTrader transport reliability."""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any

FRAME_HEADER = struct.Struct("!I")
DEFAULT_MAX_FRAME_BYTES = 4 * 1024 * 1024


class FrameLimitError(ConnectionError):
    """Raised when broker frame lengths exceed allowed bounds."""


def parse_frame_length(header: bytes, *, max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> int:
    if len(header) != FRAME_HEADER.size:
        raise FrameLimitError("invalid frame header length")
    (length,) = FRAME_HEADER.unpack(header)
    if length <= 0:
        raise FrameLimitError("empty cTrader frame")
    if length > max_frame_bytes:
        raise FrameLimitError(f"cTrader frame {length} exceeds limit {max_frame_bytes}")
    return length


@dataclass(frozen=True)
class QueuePutResult:
    accepted: bool
    dropped_oldest: bool = False


class DropOldestAsyncQueue:
    """Bounded queue that drops the oldest item instead of growing unbounded."""

    def __init__(self, maxsize: int = 1024):
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max(1, int(maxsize)))
        self.dropped = 0

    def put_nowait(self, item: Any) -> QueuePutResult:
        if self.queue.full():
            try:
                self.queue.get_nowait()
                self.dropped += 1
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(item)
            return QueuePutResult(True, dropped_oldest=True)
        self.queue.put_nowait(item)
        return QueuePutResult(True, dropped_oldest=False)

    async def get(self) -> Any:
        return await self.queue.get()

    def qsize(self) -> int:
        return self.queue.qsize()


class PendingFutureRegistry:
    """Tracks request futures and guarantees cleanup on timeout/close."""

    def __init__(self):
        self._futures: dict[str, asyncio.Future[Any]] = {}

    def create(self, request_id: str) -> asyncio.Future[Any]:
        fut = asyncio.get_running_loop().create_future()
        self._futures[str(request_id)] = fut
        return fut

    def pop(self, request_id: str) -> asyncio.Future[Any] | None:
        return self._futures.pop(str(request_id), None)

    async def wait(self, request_id: str, *, timeout: float) -> Any:
        fut = self._futures[str(request_id)]
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        finally:
            self._futures.pop(str(request_id), None)

    def cancel_all(self, reason: str = "transport closed") -> None:
        for fut in list(self._futures.values()):
            if not fut.done():
                fut.cancel(reason)
        self._futures.clear()

    def __len__(self) -> int:
        return len(self._futures)
