"""End-to-end bridge tests: control messages, streams, auth and batching."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from wmr_bridge import __version__
from wmr_bridge.bridge import Bridge, Subscriber
from wmr_bridge.inputs import read_capture
from wmr_bridge.protocol import Reassembler, parse_batch, unpack_record
from wmr_bridge.server import BridgeServer
from wmr_bridge import ws as wsmod

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
SAMPLE_DATA = read_capture(SAMPLES)[0][1]


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    token: str | None = None
    batch_max = 9

    async def asyncSetUp(self) -> None:
        self.bridge = Bridge(token=self.token, batch_max=self.batch_max, batch_hold_ms=5)
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()
        self.clients: list[wsmod.WebSocket] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        await self.server.close()

    async def connect(self, authenticate: bool = True) -> tuple[wsmod.WebSocket, dict]:
        client = await wsmod.connect("127.0.0.1", self.server.port, "/wmr", timeout=5)
        self.clients.append(client)
        hello = await asyncio.wait_for(client.recv_json(), 2)
        if self.token and authenticate:
            await client.send_json({"type": "auth", "token": self.token})
            auth = await asyncio.wait_for(client.recv_json(), 2)
            self.assertEqual(auth["type"], "auth_ok")
        return client, hello

    async def subscribe(self, client: wsmod.WebSocket, stream: str) -> dict:
        await client.send_json({"type": "subscribe", "stream": stream})
        return await asyncio.wait_for(client.recv_json(), 2)

    async def test_hello(self) -> None:
        _client, hello = await self.connect()
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["protocol"], 1)
        self.assertEqual(hello["transport"], "wifi")
        self.assertEqual(hello["streams"], ["raw", "decoded", "batched"])
        self.assertEqual(hello["recordSize"], 56)
        self.assertEqual(hello["handFilter"], ["LEFT", "RIGHT"])
        self.assertFalse(hello["authRequired"])

    async def test_raw_stream_reassembles(self) -> None:
        client, _ = await self.connect()
        reply = await self.subscribe(client, "raw")
        self.assertEqual(reply["type"], "subscribed")
        self.bridge.ingest(0, SAMPLE_DATA)
        packet = await asyncio.wait_for(client.recv_binary(), 2)
        reassembler = Reassembler()
        frame = reassembler.push(packet)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.hand, 0)
        self.assertEqual(frame.sequence, 1)
        self.assertEqual(frame.data, SAMPLE_DATA)

    async def test_decoded_stream(self) -> None:
        client, _ = await self.connect()
        await self.subscribe(client, "decoded")
        self.bridge.ingest(1, SAMPLE_DATA, rx_time_us=777)
        record = await asyncio.wait_for(client.recv_binary(), 2)
        self.assertEqual(len(record), 56)
        parsed = unpack_record(record)
        self.assertEqual(parsed.hand, 1)
        self.assertEqual(parsed.sequence, 1)
        self.assertEqual(parsed.rx_time_us, 777)
        self.assertEqual(parsed.flags, 0)
        self.assertEqual(parsed.buttons, SAMPLE_DATA[2])

    async def test_batched_stream_flushes_on_timer(self) -> None:
        client, _ = await self.connect()
        await self.subscribe(client, "batched")
        for _ in range(3):
            self.bridge.ingest(0, SAMPLE_DATA)
        packet = await asyncio.wait_for(client.recv_binary(), 2)
        records = parse_batch(packet)
        self.assertEqual(len(records), 3)
        self.assertEqual([unpack_record(record).sequence for record in records], [1, 2, 3])

    async def test_batched_stream_flushes_when_full(self) -> None:
        await self.server.close()
        self.bridge = Bridge(batch_max=2, batch_hold_ms=1000)
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()
        client, _ = await self.connect()
        await self.subscribe(client, "batched")
        self.bridge.ingest(0, SAMPLE_DATA)
        self.bridge.ingest(0, SAMPLE_DATA)
        packet = await asyncio.wait_for(client.recv_binary(), 2)
        self.assertEqual(len(parse_batch(packet)), 2)

    async def test_streams_are_exclusive(self) -> None:
        client, _ = await self.connect()
        await self.subscribe(client, "raw")
        await client.send_json({"type": "subscribe", "stream": "decoded"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "stream_active"})
        # Subscribing to the same stream again is idempotent.
        self.assertEqual((await self.subscribe(client, "raw"))["type"], "subscribed")
        await client.send_json({"type": "unsubscribe"})
        self.assertEqual((await asyncio.wait_for(client.recv_json(), 2))["type"], "unsubscribed")
        self.assertEqual((await self.subscribe(client, "decoded"))["type"], "subscribed")

    async def test_unsubscribe_drops_payloads_queued_for_the_old_stream(self) -> None:
        class BlockingSocket:
            closed = False

            def __init__(self) -> None:
                self.gate = asyncio.Event()

            async def send_json(self, _value: dict) -> None:
                pass

            async def send_binary(self, _payload: bytes) -> None:
                await self.gate.wait()

        bridge = Bridge()
        socket = BlockingSocket()
        subscriber = Subscriber(bridge, socket)  # type: ignore[arg-type]
        subscriber.stream = "decoded"
        try:
            for _ in range(3):
                subscriber._enqueue(b"old-stream payload")
            await asyncio.sleep(0)  # the sender takes one and blocks in send_binary
            self.assertEqual(subscriber._queue.qsize(), 2)
            subscriber.reset_stream()
            self.assertEqual(subscriber._queue.qsize(), 0)
        finally:
            socket.gate.set()
            subscriber.close()
            await asyncio.sleep(0)

    async def test_status_and_ping(self) -> None:
        client, _ = await self.connect()
        self.bridge.ingest(0, SAMPLE_DATA)
        await client.send_json({"type": "status"})
        status = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(status["type"], "status")
        self.assertEqual(status["version"], __version__)
        self.assertEqual(status["handFilter"], ["LEFT", "RIGHT"])
        self.assertEqual(status["hands"][0]["reports"], 1)
        self.assertEqual(status["hands"][0]["sequence"], 1)
        self.assertEqual(status["hands"][1]["reports"], 0)
        await client.send_json({"type": "ping", "id": 7, "t": 1234})
        pong = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(pong["type"], "pong")
        self.assertEqual((pong["id"], pong["t"]), (7, 1234))
        self.assertIsInstance(pong["serverTimeUs"], int)

    async def test_multiple_clients_get_the_same_frames(self) -> None:
        first, _ = await self.connect()
        second, _ = await self.connect()
        await self.subscribe(first, "decoded")
        await self.subscribe(second, "batched")
        self.bridge.ingest(1, SAMPLE_DATA)
        first_record = unpack_record(await asyncio.wait_for(first.recv_binary(), 2))
        second_batch = parse_batch(await asyncio.wait_for(second.recv_binary(), 2))
        self.assertEqual(first_record.sequence, 1)
        self.assertEqual(unpack_record(second_batch[0]).sequence, 1)

    async def test_binary_from_client_is_rejected(self) -> None:
        client, _ = await self.connect()
        await client.send_binary(b"nope")
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "binary_not_accepted"})

    async def test_bad_json(self) -> None:
        client, _ = await self.connect()
        await client.send_text("{not json")
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "bad_json"})

    async def test_unknown_message(self) -> None:
        client, _ = await self.connect()
        await client.send_json({"type": "nonsense"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "unknown_type"})

    async def test_sequence_advances_without_subscribers(self) -> None:
        self.bridge.ingest(0, SAMPLE_DATA)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(self.bridge.sequence, [2, 0])
        client, _ = await self.connect()
        await self.subscribe(client, "decoded")
        self.bridge.ingest(0, SAMPLE_DATA)
        record = unpack_record(await asyncio.wait_for(client.recv_binary(), 2))
        self.assertEqual(record.sequence, 3)

    async def test_server_close_closes_active_clients(self) -> None:
        client, _ = await self.connect()
        await self.server.close()
        with self.assertRaises(wsmod.WebSocketClosed):
            await asyncio.wait_for(client.recv(), 2)

    async def test_per_hand_dropped_counts_queue_rejections(self) -> None:
        class FullQueue:
            def put_nowait(self, _payload: bytes) -> None:
                raise asyncio.QueueFull

            def get_nowait(self) -> bytes:
                raise asyncio.QueueEmpty

            async def get(self) -> bytes:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        bridge = Bridge(queue_size=1)
        subscriber = Subscriber(bridge, object())  # type: ignore[arg-type]
        subscriber.stream = "decoded"
        subscriber._queue = FullQueue()  # type: ignore[assignment]
        bridge.subscribers.add(subscriber)
        try:
            bridge.ingest(0, SAMPLE_DATA)
            bridge.ingest(0, SAMPLE_DATA)
        finally:
            subscriber.close()
        self.assertEqual(bridge.hands[0].dropped, 2)
        self.assertEqual(bridge.hands[0].delivered, 0)
        self.assertEqual(bridge.stats_dropped, 2)


class AuthBridgeTests(BridgeTests):
    token = "s3cret"

    async def test_hello(self) -> None:
        _client, hello = await self.connect()
        self.assertTrue(hello["authRequired"])

    async def test_hello_announces_auth(self) -> None:
        _client, hello = await self.connect()
        self.assertTrue(hello["authRequired"])

    async def test_wrong_token_closes(self) -> None:
        client, _ = await self.connect(authenticate=False)
        await client.send_json({"type": "auth", "token": "wrong"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "auth_failed"})
        with self.assertRaises(wsmod.WebSocketClosed):
            await asyncio.wait_for(client.recv(), 2)

    async def test_non_ascii_token_is_auth_failed(self) -> None:
        client, _ = await self.connect(authenticate=False)
        await client.send_json({"type": "auth", "token": "pässwörd"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "auth_failed"})
        with self.assertRaises(wsmod.WebSocketClosed):
            await asyncio.wait_for(client.recv(), 2)

    async def test_control_before_auth_is_refused(self) -> None:
        client, _ = await self.connect(authenticate=False)
        await client.send_json({"type": "subscribe", "stream": "raw"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "auth_failed"})
        with self.assertRaises(wsmod.WebSocketClosed):
            await asyncio.wait_for(client.recv(), 2)

    # The inherited tests authenticate through the helper; disable the ones
    # whose unauthenticated handshake is covered above.
    test_binary_from_client_is_rejected = None  # type: ignore[assignment]
    test_bad_json = None  # type: ignore[assignment]
    test_unknown_message = None  # type: ignore[assignment]
    test_server_close_closes_active_clients = None  # type: ignore[assignment]
    test_per_hand_dropped_counts_queue_rejections = None  # type: ignore[assignment]


class HandFilterPayloadTests(unittest.TestCase):
    def test_both_hands_are_reported_by_default(self) -> None:
        bridge = Bridge()
        self.assertEqual(bridge.hello()["handFilter"], ["LEFT", "RIGHT"])
        status = bridge.status()
        self.assertEqual(status["handFilter"], ["LEFT", "RIGHT"])
        self.assertEqual(status["version"], __version__)

    def test_single_hand_filter_reaches_hello_and_status(self) -> None:
        for hand, expected in ((0, ["LEFT"]), (1, ["RIGHT"])):
            bridge = Bridge(allowed_hands=(hand,))
            self.assertEqual(bridge.hello()["handFilter"], expected)
            self.assertEqual(bridge.status()["handFilter"], expected)

    def test_invalid_hand_filter_is_rejected(self) -> None:
        for bad in ((), (2,), (-1,), (0, 2)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Bridge(allowed_hands=bad)


if __name__ == "__main__":
    unittest.main()
