"""The Wi-Fi bridge: one stream per client, with BLE-compatible payloads.

A client connects over WebSocket, receives a ``hello``, optionally authenticates,
then subscribes to exactly one of ``raw``, ``decoded`` or ``batched``. The
binary messages that follow are byte-for-byte the BLE notification values the
ESP32 firmware would have sent on FF11, FF12 or FF14. See ``docs/PI_BRIDGE.md``.

Sequence numbers are assigned here, once per received report and independently
of whether anybody is subscribed, matching the firmware's counter semantics.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any

from . import __version__
from .decode import record_from_frame
from .protocol import (
    BATCH_MAX_COUNT,
    DECODED_SIZE,
    DEFAULT_FRAGMENT_CAPACITY,
    DecodedRecord,
    Frame,
    ProtocolError,
    encode_batch,
    encode_raw_messages,
    pack_record,
)
from . import ws as wsmod

STREAMS = ("raw", "decoded", "batched")
HAND_NAMES = ("LEFT", "RIGHT")


@dataclass
class HandStats:
    reports: int = 0
    decoded: int = 0
    errors: int = 0
    delivered: int = 0
    dropped: int = 0
    connected: bool = False
    address: str = ""
    last_rx_us: int = 0

    def to_json(self, sequence: int, now_us: int) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "address": self.address,
            "reports": self.reports,
            "decoded": self.decoded,
            "errors": self.errors,
            "delivered": self.delivered,
            "dropped": self.dropped,
            "sequence": sequence,
            "lastRxAgeMs": None if not self.last_rx_us else round((now_us - self.last_rx_us) / 1000.0, 1),
        }


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _tokens_equal(candidate: str, expected: str) -> bool:
    """Compare tokens as UTF-8 bytes.

    ``hmac.compare_digest`` rejects non-ASCII strings with ``TypeError``; bytes
    comparison keeps the constant-time property and turns a malformed client
    token into an ordinary authentication failure.
    """

    try:
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False


class Subscriber:
    """One connected client and its outbound queue."""

    def __init__(self, bridge: "Bridge", websocket: wsmod.WebSocket) -> None:
        self.bridge = bridge
        self.websocket = websocket
        self.stream: str | None = None
        self.authed = bridge.token is None
        self.dropped = 0
        self.sent = 0
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=bridge.queue_size)
        self._batch: list[tuple[int, bytes]] = []
        self._batch_timer: asyncio.TimerHandle | None = None
        self._sender = asyncio.create_task(self._send_loop())
        self.closed = False

    # ---- outbound --------------------------------------------------------

    async def send_json(self, value: dict[str, Any]) -> None:
        await self.websocket.send_json(value)

    def _enqueue(self, payload: bytes) -> bool:
        if self.closed:
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            self.bridge.stats_dropped += 1
            return False

    async def _send_loop(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                await self.websocket.send_binary(payload)
                self.sent += 1
        except (wsmod.WebSocketClosed, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self.closed = True

    # ---- stream delivery -------------------------------------------------

    def deliver(self, frame: Frame, record: DecodedRecord | None = None) -> None:
        if self.stream == "raw":
            for message in encode_raw_messages(frame, self.bridge.fragment_capacity):
                if self._enqueue(message):
                    self.bridge.hands[frame.hand].delivered += 1
                else:
                    self.bridge.hands[frame.hand].dropped += 1
        elif self.stream in ("decoded", "batched"):
            if record is None:
                return
            packed = pack_record(record)
            if self.stream == "decoded":
                if self._enqueue(packed):
                    self.bridge.hands[frame.hand].delivered += 1
                else:
                    self.bridge.hands[frame.hand].dropped += 1
            else:
                self._batch.append((frame.hand, packed))
                if len(self._batch) >= self.bridge.batch_max:
                    self._flush_batch()
                elif self._batch_timer is None:
                    self._batch_timer = asyncio.get_running_loop().call_later(
                        self.bridge.batch_hold_s, self._flush_batch
                    )

    def _flush_batch(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        if not self._batch:
            return
        records = self._batch
        self._batch = []
        try:
            payload = encode_batch([packed for _hand, packed in records])
        except ProtocolError:
            return
        if self._enqueue(payload):
            self.bridge.stats_batches += 1
            for hand, _packed in records:
                self.bridge.hands[hand].delivered += 1
        else:
            for hand, _packed in records:
                self.bridge.hands[hand].dropped += 1

    def reset_stream(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        self._batch.clear()
        self.stream = None

    def close(self) -> None:
        self.closed = True
        self.reset_stream()
        self._sender.cancel()


class Bridge:
    """Transport-independent bridge state (no sockets, easy to unit test)."""

    def __init__(
        self,
        *,
        token: str | None = None,
        queue_size: int = 512,
        batch_max: int = BATCH_MAX_COUNT,
        batch_hold_ms: float = 5.0,
        fragment_capacity: int = DEFAULT_FRAGMENT_CAPACITY,
        auth_timeout_s: float = 5.0,
    ) -> None:
        if not 1 <= batch_max <= BATCH_MAX_COUNT:
            raise ValueError(f"batch_max must be 1..{BATCH_MAX_COUNT}")
        if batch_hold_ms < 0:
            raise ValueError("batch_hold_ms cannot be negative")
        self.token = token
        self.queue_size = max(1, queue_size)
        self.batch_max = batch_max
        self.batch_hold_s = batch_hold_ms / 1000.0
        self.fragment_capacity = max(13, fragment_capacity)
        self.auth_timeout_s = auth_timeout_s
        self.hands = [HandStats(), HandStats()]
        self.sequence = [0, 0]
        self.subscribers: set[Subscriber] = set()
        self.started_us = time.monotonic_ns() // 1000
        self.stats_dropped = 0
        self.stats_batches = 0

    # ---- ingest ----------------------------------------------------------

    def ingest(self, hand: int, data: bytes, rx_time_us: int | None = None) -> Frame | None:
        """Assign a sequence number and fan one report out to subscribers."""

        if not 0 <= hand < 2:
            raise ValueError(f"bad hand {hand}")
        if not data:
            return None
        now_us = time.monotonic_ns() // 1000 if rx_time_us is None else rx_time_us
        self.sequence[hand] = (self.sequence[hand] + 1) & 0xFFFFFFFF
        stats = self.hands[hand]
        stats.reports += 1
        stats.last_rx_us = now_us
        try:
            frame = Frame(hand=hand, sequence=self.sequence[hand], data=bytes(data), rx_time_us=now_us)
        except ProtocolError:
            stats.errors += 1
            return None
        subscribers = tuple(subscriber for subscriber in self.subscribers if subscriber.stream)
        if not subscribers:
            return frame
        record = None
        if any(subscriber.stream in ("decoded", "batched") for subscriber in subscribers):
            record = record_from_frame(frame)
            if record is None:
                stats.errors += 1
            else:
                stats.decoded += 1
        for subscriber in subscribers:
            subscriber.deliver(frame, record)
        return frame

    def set_hand_connected(self, hand: int, connected: bool, address: str = "") -> None:
        if not 0 <= hand < 2:
            return
        self.hands[hand].connected = connected
        if address:
            self.hands[hand].address = address

    # ---- status ----------------------------------------------------------

    def now_us(self) -> int:
        return time.monotonic_ns() // 1000

    def status(self) -> dict[str, Any]:
        now = self.now_us()
        hands = []
        for index, stats in enumerate(self.hands):
            item = stats.to_json(self.sequence[index], now)
            item["hand"] = HAND_NAMES[index]
            hands.append(item)
        return {
            "type": "status",
            "uptimeS": round((now - self.started_us) / 1_000_000.0, 1),
            "hostTimeUs": now,
            "hands": hands,
            "clients": len(self.subscribers),
            "subscribed": sum(1 for s in self.subscribers if s.stream),
            "dropped": self.stats_dropped,
            "batches": self.stats_batches,
        }

    def hello(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "protocol": 1,
            "transport": "wifi",
            "device": "wmr-pi",
            "version": __version__,
            "hostname": socket.gethostname(),
            "authRequired": self.token is not None,
            "streams": list(STREAMS),
            "recordSize": DECODED_SIZE,
            "batchMax": BATCH_MAX_COUNT,
            "fragmentCapacity": self.fragment_capacity,
        }

    # ---- client session --------------------------------------------------

    async def run_client(self, websocket: wsmod.WebSocket) -> None:
        subscriber = Subscriber(self, websocket)
        self.subscribers.add(subscriber)
        try:
            await subscriber.send_json(self.hello())
            if self.token is not None:
                if not await self._authenticate(subscriber):
                    return
            while not subscriber.closed:
                try:
                    opcode, payload = await websocket.recv()
                except wsmod.WebSocketClosed:
                    return
                if opcode == wsmod.OP_BINARY:
                    await subscriber.send_json({"type": "error", "code": "binary_not_accepted"})
                    continue
                message = _parse_json(payload.decode("utf-8", errors="replace"))
                if message is None:
                    await subscriber.send_json({"type": "error", "code": "bad_json"})
                    continue
                if not await self._handle_control(subscriber, message):
                    return
        except (ConnectionError, OSError, wsmod.WebSocketError, wsmod.WebSocketClosed):
            pass
        finally:
            subscriber.close()
            self.subscribers.discard(subscriber)
            if not websocket.closed:
                await websocket.close()

    async def _authenticate(self, subscriber: Subscriber) -> bool:
        try:
            opcode, payload = await asyncio.wait_for(
                subscriber.websocket.recv(), timeout=self.auth_timeout_s
            )
        except (asyncio.TimeoutError, wsmod.WebSocketClosed):
            await subscriber.websocket.close(4401, "authentication required")
            return False
        message = _parse_json(payload.decode("utf-8", errors="replace")) if opcode == wsmod.OP_TEXT else None
        if (
            message is None
            or message.get("type") != "auth"
            or not isinstance(message.get("token"), str)
            or self.token is None
            or not _tokens_equal(message["token"], self.token)
        ):
            await subscriber.send_json({"type": "error", "code": "auth_failed"})
            await subscriber.websocket.close(4401, "authentication failed")
            return False
        subscriber.authed = True
        await subscriber.send_json({"type": "auth_ok"})
        return True

    async def _handle_control(self, subscriber: Subscriber, message: dict[str, Any]) -> bool:
        kind = message.get("type")

        if kind == "subscribe":
            stream = message.get("stream")
            if stream not in STREAMS:
                await subscriber.send_json({"type": "error", "code": "bad_stream"})
                return True
            if subscriber.stream and subscriber.stream != stream:
                await subscriber.send_json({"type": "error", "code": "stream_active"})
                return True
            if subscriber.stream == stream:
                await subscriber.send_json({"type": "subscribed", "stream": stream})
                return True
            subscriber.stream = stream
            await subscriber.send_json(
                {
                    "type": "subscribed",
                    "stream": stream,
                    "recordSize": DECODED_SIZE,
                    "batchMax": self.batch_max,
                    "fragmentCapacity": self.fragment_capacity,
                }
            )
            return True

        if kind == "unsubscribe":
            subscriber.reset_stream()
            await subscriber.send_json({"type": "unsubscribed"})
            return True

        if kind == "status":
            await subscriber.send_json(self.status())
            return True

        if kind == "ping":
            reply: dict[str, Any] = {
                "type": "pong",
                "id": message.get("id"),
                "serverTimeUs": self.now_us(),
            }
            if "t" in message:
                reply["t"] = message["t"]
            await subscriber.send_json(reply)
            return True

        if kind == "hello":
            await subscriber.send_json(self.hello())
            return True

        if kind == "auth":
            if subscriber.authed:
                await subscriber.send_json({"type": "auth_ok"})
            else:
                await subscriber.send_json({"type": "error", "code": "auth_failed"})
            return True

        if kind == "close":
            await subscriber.websocket.close(wsmod.CLOSE_NORMAL, "client requested close")
            return False

        await subscriber.send_json({"type": "error", "code": "unknown_type"})
        return True
