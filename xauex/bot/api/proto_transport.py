"""
Pure-asyncio cTrader TCP transport.

Implements the Int32String framing used by ctrader-open-api's TcpProtocol:
  - 4-byte big-endian uint32 length prefix
  - ProtoMessage protobuf payload

Replaces the Twisted-based ctrader_open_api.Client entirely, allowing
the bot to run under a plain asyncio.run() event loop.
"""

import asyncio
import logging
import ssl
import struct
from typing import Callable, Optional

from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import (
    ProtoMessage,
    ProtoHeartbeatEvent,
)

logger = logging.getLogger(__name__)

_HEARTBEAT_INTERVAL = 25  # seconds between heartbeats
_FRAME_HEADER = struct.Struct("!I")  # big-endian uint32


def _wrap(inner_msg) -> bytes:
    """Wrap an inner protobuf message in a ProtoMessage envelope."""
    envelope = ProtoMessage()
    envelope.payloadType = inner_msg.payloadType
    if hasattr(inner_msg, "clientMsgId") and inner_msg.clientMsgId:
        envelope.clientMsgId = inner_msg.clientMsgId
    envelope.payload = inner_msg.SerializeToString()
    return envelope.SerializeToString()


def _wrap_with_id(inner_msg, client_msg_id: Optional[str]) -> bytes:
    """Wrap an inner message and embed the clientMsgId in the envelope."""
    envelope = ProtoMessage()
    envelope.payloadType = inner_msg.payloadType
    if client_msg_id:
        envelope.clientMsgId = client_msg_id
    envelope.payload = inner_msg.SerializeToString()
    return envelope.SerializeToString()


class CTraderTransport:
    """
    Async TCP/SSL transport for the cTrader Open API.

    Usage:
        transport = CTraderTransport(host, port)
        transport.set_message_callback(on_msg)
        transport.set_disconnect_callback(on_disc)
        await transport.connect()
        await transport.send(my_proto_msg, client_msg_id="abc123")
        await transport.close()
    """

    def __init__(self, host: str, port: int, tls_server_name: Optional[str] = None):
        self._host = host
        self._port = port
        self._tls_server_name = tls_server_name or None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._msg_callback: Optional[Callable] = None
        self._disc_callback: Optional[Callable] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

    def set_message_callback(self, cb: Callable) -> None:
        """Called with (ProtoMessage,) for every inbound message."""
        self._msg_callback = cb

    def set_disconnect_callback(self, cb: Callable) -> None:
        """Called with (reason: str,) on disconnect."""
        self._disc_callback = cb

    async def connect(self, timeout: float = 30.0) -> None:
        """Establish SSL TCP connection."""
        ssl_ctx = ssl.create_default_context()
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=ssl_ctx,
                    server_hostname=self._tls_server_name,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(
                f"Timeout connecting to {self._host}:{self._port}"
            )
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to {self._host}:{self._port}: {exc}"
            ) from exc

        self._connected = True
        logger.info("[Transport] Connected to %s:%s", self._host, self._port)

        self._recv_task = asyncio.create_task(self._recv_loop(), name="ctrader-recv")
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="ctrader-hb"
        )

    async def send(self, inner_msg, client_msg_id: Optional[str] = None) -> None:
        """Serialize and send a protobuf message with length-prefix framing."""
        if not self._connected or self._writer is None:
            raise ConnectionError("Not connected")

        data = _wrap_with_id(inner_msg, client_msg_id)
        header = _FRAME_HEADER.pack(len(data))
        self._writer.write(header + data)
        await self._writer.drain()

    async def close(self) -> None:
        """Gracefully close the connection and cancel background tasks."""
        self._connected = False
        for task in (self._recv_task, self._heartbeat_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        logger.info("[Transport] Connection closed.")

    async def _read_frame(self) -> bytes:
        """Read exactly one length-prefixed frame from the stream."""
        header = await self._reader.readexactly(4)
        (length,) = _FRAME_HEADER.unpack(header)
        return await self._reader.readexactly(length)

    async def _recv_loop(self) -> None:
        """Background task: read frames and dispatch to message callback."""
        try:
            while self._connected:
                raw = await self._read_frame()
                envelope = ProtoMessage()
                envelope.ParseFromString(raw)

                # Heartbeat — reply immediately
                if envelope.payloadType == ProtoHeartbeatEvent().payloadType:
                    await self._send_heartbeat()
                    continue

                if self._msg_callback:
                    try:
                        self._msg_callback(envelope)
                    except Exception as exc:
                        logger.error("[Transport] msg_callback error: %s", exc)

        except asyncio.IncompleteReadError:
            reason = "server closed connection"
        except asyncio.CancelledError:
            reason = "transport closed"
        except Exception as exc:
            reason = str(exc)

        self._connected = False
        logger.warning("[Transport] Disconnected: %s", reason)
        if self._disc_callback:
            try:
                self._disc_callback(reason)
            except Exception as exc:
                logger.error("[Transport] disc_callback error: %s", exc)

    async def _heartbeat_loop(self) -> None:
        """Send a heartbeat every _HEARTBEAT_INTERVAL seconds."""
        try:
            while self._connected:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                if self._connected:
                    await self._send_heartbeat()
        except asyncio.CancelledError:
            pass

    async def _send_heartbeat(self) -> None:
        hb = ProtoHeartbeatEvent()
        data = hb.SerializeToString()
        envelope = ProtoMessage()
        envelope.payloadType = hb.payloadType
        envelope.payload = data
        serialized = envelope.SerializeToString()
        header = _FRAME_HEADER.pack(len(serialized))
        if self._writer:
            self._writer.write(header + serialized)
            try:
                await self._writer.drain()
            except Exception:
                pass
