"""WebSocket handshake, framing and close tests over loopback."""

from __future__ import annotations

import asyncio
import base64
import fcntl
import os
import tempfile
import time
import unittest
from pathlib import Path

from wmr_bridge import ws as wsmod
from wmr_bridge.bridge import (
    UPGRADE_RATE_BURST,
    UPGRADE_TRIGGER_TTL_S,
    Bridge,
)
from wmr_bridge.protocol import (
    CALIBRATION_WINDOW_MAX,
    calibration_crc32,
    parse_calibration_window,
)
from wmr_bridge.server import BridgeServer


class WebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.handlers: set[asyncio.Task[None]] = set()
        self.server = await wsmod.serve(self._handle, host="127.0.0.1", port=0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients: list[wsmod.WebSocket] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        self.server.close()
        await self.server.wait_closed()
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)

    async def _handle(self, websocket: wsmod.WebSocket) -> None:
        self.handlers.add(asyncio.current_task())
        try:
            while True:
                opcode, payload = await websocket.recv()
                if opcode == wsmod.OP_TEXT:
                    await websocket.send_text("echo:" + payload.decode())
                else:
                    await websocket.send_binary(b"echo:" + payload)
        except wsmod.WebSocketClosed:
            return

    async def connect(self) -> wsmod.WebSocket:
        client = await wsmod.connect("127.0.0.1", self.port, "/wmr", timeout=5)
        self.clients.append(client)
        return client

    async def test_handshake_and_subprotocol(self) -> None:
        client = await self.connect()
        self.assertFalse(client.is_client is False)
        self.assertEqual(client.subprotocol, wsmod.SUBPROTOCOL)
        await client.send_text("hi")
        self.assertEqual(await client.recv_text(), "echo:hi")

    async def test_binary(self) -> None:
        client = await self.connect()
        await client.send_binary(b"\x00\x01\xff")
        self.assertEqual(await client.recv_binary(), b"echo:\x00\x01\xff")

    async def test_ping_is_answered(self) -> None:
        client = await self.connect()
        await client.ping(b"probe")
        await client.send_text("after")
        self.assertEqual(await client.recv_text(), "echo:after")

    async def test_fragmented_client_message(self) -> None:
        client = await self.connect()
        await client._send_frame(wsmod.OP_TEXT, b"he", fin=False)
        await client._send_frame(wsmod.OP_CONT, b"llo")
        self.assertEqual(await client.recv_text(), "echo:hello")

    async def test_json(self) -> None:
        client = await self.connect()
        await client.send_json({"type": "hello", "n": 1})
        self.assertEqual(await client.recv_text(), 'echo:{"type":"hello","n":1}')

    async def test_peer_close_raises(self) -> None:
        client = await self.connect()
        await client.close(1000, "done")
        with self.assertRaises(wsmod.WebSocketClosed):
            await client.recv()

    async def test_rejects_plain_http(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), 5)
        self.assertIn(b"426", response.split(b"\r\n", 1)[0])
        writer.close()
        await writer.wait_closed()

    async def test_rejects_bad_key(self) -> None:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
            b"Connection: Upgrade\r\nSec-WebSocket-Key: nope\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), 5)
        self.assertIn(b"400", response.split(b"\r\n", 1)[0])
        writer.close()
        await writer.wait_closed()

    async def test_close_reason_truncation_keeps_utf8_whole(self) -> None:
        payload = wsmod._close_payload(1000, "é" * 100)
        self.assertLessEqual(len(payload), 125)
        self.assertEqual(int.from_bytes(payload[:2], "big"), 1000)
        self.assertEqual(payload[2:].decode("utf-8"), "é" * 61)

    async def test_one_byte_close_payload_is_a_protocol_error(self) -> None:
        with self.assertRaises(wsmod.WebSocketError):
            wsmod._close_details(b"\x03")

    async def test_connect_closes_the_writer_when_the_handshake_write_fails(self) -> None:
        from unittest import mock

        class FakeReader:
            pass

        class FakeWriter:
            def __init__(self) -> None:
                self.closed = False

            def write(self, _data: bytes) -> None:
                pass

            async def drain(self) -> None:
                raise ConnectionResetError("peer went away")

            def close(self) -> None:
                self.closed = True

            async def wait_closed(self) -> None:
                pass

        writer = FakeWriter()

        async def fake_open_connection(*_args, **_kwargs):
            return FakeReader(), writer

        with mock.patch("wmr_bridge.ws.asyncio.open_connection", fake_open_connection):
            with self.assertRaises(ConnectionResetError):
                await wsmod.connect("127.0.0.1", 1)
        self.assertTrue(writer.closed)


class OriginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = await wsmod.serve(
            self._handle,
            host="127.0.0.1",
            port=0,
            allowed_origins=("https://allowed.example",),
        )
        self.port = self.server.sockets[0].getsockname()[1]
        self.clients: list[wsmod.WebSocket] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, websocket: wsmod.WebSocket) -> None:
        try:
            while True:
                await websocket.recv()
        except wsmod.WebSocketClosed:
            return

    async def _raw_handshake(self, origin: str) -> bytes:
        key = base64.b64encode(b"0123456789abcdef").decode("ascii")
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(
            (
                "GET /wmr HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Origin: {origin}\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        response = await asyncio.wait_for(reader.read(4096), 5)
        writer.close()
        await writer.wait_closed()
        return response

    async def test_rejects_unlisted_origin(self) -> None:
        response = await self._raw_handshake("https://evil.example")
        self.assertIn(b"403", response.split(b"\r\n", 1)[0])

    async def test_allows_listed_origin(self) -> None:
        client = await wsmod.connect(
            "127.0.0.1", self.port, "/wmr", timeout=5, origin="https://allowed.example"
        )
        self.clients.append(client)
        self.assertEqual(client.subprotocol, wsmod.SUBPROTOCOL)

    async def test_allows_client_without_origin(self) -> None:
        client = await wsmod.connect("127.0.0.1", self.port, "/wmr", timeout=5)
        self.clients.append(client)
        self.assertEqual(client.subprotocol, wsmod.SUBPROTOCOL)


class HandshakeTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_handshake_is_closed(self) -> None:
        async def _handle(websocket: wsmod.WebSocket) -> None:  # pragma: no cover - never reached
            await websocket.close()

        server = await wsmod.serve(
            _handle, host="127.0.0.1", port=0, handshake_timeout=0.1
        )
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            response = await asyncio.wait_for(reader.read(4096), 2)
            self.assertIn(b"408", response.split(b"\r\n", 1)[0])
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()


class CalibrationForwardingTests(unittest.IsolatedAsyncioTestCase):
    BLOB = bytes(range(256)) + bytes(344)

    async def asyncSetUp(self) -> None:
        self.bridge = Bridge()
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()
        self.clients: list[wsmod.WebSocket] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        await self.server.close()

    async def connect(self) -> tuple[wsmod.WebSocket, dict]:
        client = await wsmod.connect("127.0.0.1", self.server.port, "/wmr", timeout=5)
        self.clients.append(client)
        hello = await asyncio.wait_for(client.recv_json(), 2)
        return client, hello

    async def read_window(
        self, client: wsmod.WebSocket, hand: str | int, offset: int
    ) -> bytes:
        await client.send_json({"type": "calibration", "hand": hand, "offset": offset})
        message = await asyncio.wait_for(client.recv_binary(), 2)
        self.assertEqual(message[0], wsmod.CALIBRATION_MESSAGE_TAG)
        return wsmod.unpack_calibration_message(message) or b""

    async def test_message_tag_round_trip(self) -> None:
        value = bytes(CALIBRATION_WINDOW_MAX + 16)
        self.assertEqual(
            wsmod.unpack_calibration_message(wsmod.pack_calibration_message(value)),
            value,
        )
        self.assertIsNone(wsmod.unpack_calibration_message(value))
        self.assertIsNone(
            wsmod.unpack_calibration_message(bytes((wsmod.CALIBRATION_MESSAGE_TAG,)))
        )
        with self.assertRaises(ValueError):
            wsmod.pack_calibration_message(bytes(15))
        with self.assertRaises(ValueError):
            wsmod.pack_calibration_message(bytes(CALIBRATION_WINDOW_MAX + 17))

    async def test_hello_advertises_calibration(self) -> None:
        _client, hello = await self.connect()
        info = hello["calibration"]
        self.assertEqual(info["version"], 1)
        self.assertEqual(info["headerSize"], 16)
        self.assertEqual(info["windowMax"], CALIBRATION_WINDOW_MAX)
        self.assertEqual(info["messageTag"], wsmod.CALIBRATION_MESSAGE_TAG)
        self.assertEqual(
            [(item["hand"], item["state"]) for item in info["hands"]],
            [("LEFT", "not_read"), ("RIGHT", "not_read")],
        )

    async def test_reads_cached_blob_in_windows(self) -> None:
        client, _hello = await self.connect()
        self.bridge.set_calibration_blob(0, self.BLOB)

        first = parse_calibration_window(await self.read_window(client, "LEFT", 0))
        self.assertEqual(first.hand, 0)
        self.assertTrue(first.ready)
        self.assertEqual(first.total_len, 600)
        self.assertEqual(first.crc32, calibration_crc32(self.BLOB))
        self.assertEqual(first.data, self.BLOB[:CALIBRATION_WINDOW_MAX])

        tail = parse_calibration_window(await self.read_window(client, 0, 496))
        self.assertEqual((tail.offset, len(tail.data)), (496, 104))
        self.assertEqual(tail.data, self.BLOB[496:])

        empty = parse_calibration_window(await self.read_window(client, "RIGHT", 0))
        self.assertFalse(empty.ready)
        self.assertEqual((empty.total_len, empty.data), (0, b""))

    async def test_calibration_coexists_with_a_stream(self) -> None:
        client, _hello = await self.connect()
        await client.send_json({"type": "subscribe", "stream": "raw"})
        self.assertEqual((await asyncio.wait_for(client.recv_json(), 2))["type"], "subscribed")
        self.bridge.set_calibration_blob(1, self.BLOB[:64])
        window = parse_calibration_window(await self.read_window(client, "RIGHT", 0))
        self.assertEqual(window.data, self.BLOB[:64])

    async def test_status_reports_calibration_state(self) -> None:
        client, _hello = await self.connect()
        self.bridge.set_calibration_state(1, 1)
        await client.send_json({"type": "status"})
        status = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(status["calibration"]["hands"][1]["state"], "reading")
        self.bridge.set_calibration_blob(1, self.BLOB)
        await client.send_json({"type": "status"})
        status = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(status["calibration"]["hands"][1]["totalLen"], 600)

    async def test_bad_calibration_requests(self) -> None:
        client, _hello = await self.connect()
        for message, code in (
            ({"type": "calibration", "hand": "MIDDLE"}, "bad_hand"),
            ({"type": "calibration"}, "bad_hand"),
            ({"type": "calibration", "hand": "LEFT", "offset": -1}, "bad_offset"),
            ({"type": "calibration", "hand": "LEFT", "offset": "0"}, "bad_offset"),
        ):
            await client.send_json(message)
            reply = await asyncio.wait_for(client.recv_json(), 2)
            self.assertEqual(reply, {"type": "error", "code": code})

    async def test_disabled_calibration_is_not_advertised(self) -> None:
        await self.server.close()
        self.bridge = Bridge(calibration=False)
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()
        client, hello = await self.connect()
        self.assertNotIn("calibration", hello)
        await client.send_json({"type": "calibration", "hand": "LEFT", "offset": 0})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "calibration_unavailable"})


class PairingControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bridge = Bridge()
        self.calls: list[tuple[str, float | None]] = []

        async def handler(action: str, seconds: float | None) -> dict:
            self.calls.append((action, seconds))
            return {
                "open": action == "open",
                "manual": action == "open",
                "seconds": seconds,
            }

        self.bridge.pairing_handler = handler
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()
        self.clients: list[wsmod.WebSocket] = []

    async def asyncTearDown(self) -> None:
        for client in self.clients:
            await client.close()
        await self.server.close()

    async def connect(self) -> tuple[wsmod.WebSocket, dict]:
        client = await wsmod.connect("127.0.0.1", self.server.port, "/wmr", timeout=5)
        self.clients.append(client)
        hello = await asyncio.wait_for(client.recv_json(), 2)
        return client, hello

    async def test_open_and_close_reach_the_pairing_handler(self) -> None:
        client, _hello = await self.connect()
        await client.send_json({"type": "pairing", "action": "open", "seconds": 60})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(
            reply,
            {"type": "pairing", "open": True, "manual": True, "seconds": 60.0},
        )
        await client.send_json({"type": "pairing", "action": "close"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(
            reply,
            {"type": "pairing", "open": False, "manual": False, "seconds": None},
        )
        self.assertEqual(self.calls, [("open", 60.0), ("close", None)])

    async def test_bad_pairing_requests_are_rejected(self) -> None:
        client, _hello = await self.connect()
        for message, code in (
            ({"type": "pairing", "action": "toggle"}, "bad_action"),
            ({"type": "pairing", "action": "open", "seconds": 0}, "bad_seconds"),
            ({"type": "pairing", "action": "open", "seconds": "60"}, "bad_seconds"),
        ):
            await client.send_json(message)
            reply = await asyncio.wait_for(client.recv_json(), 2)
            self.assertEqual(reply, {"type": "error", "code": code})
        self.assertEqual(self.calls, [])

    async def test_no_peripheral_reports_pairing_unavailable(self) -> None:
        self.bridge.pairing_handler = None
        client, _hello = await self.connect()
        await client.send_json({"type": "pairing", "action": "open"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "pairing_unavailable"})


class UpgradeControlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_dir = Path(self.tmp.name)
        self.bridge = Bridge(
            token="secret",
            upgrade_dir=self.state_dir,
            upgrade_lock=self.state_dir / "upgrade.lock",
        )
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
        if authenticate:
            await client.send_json({"type": "auth", "token": "secret"})
            auth = await asyncio.wait_for(client.recv_json(), 2)
            self.assertEqual(auth, {"type": "auth_ok"})
        return client, hello

    async def test_hello_advertises_upgrade_capability(self) -> None:
        _client, hello = await self.connect()
        self.assertTrue(hello["upgradeCapable"])
        self.assertFalse(self.bridge.upgrade_trigger.exists())

    async def test_upgrade_writes_an_argument_free_trigger(self) -> None:
        client, _hello = await self.connect()
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "upgrade_status", "state": "started"})
        self.assertEqual(self.bridge.upgrade_trigger.read_bytes(), b"")
        self.assertFalse((self.state_dir / ".upgrade.trigger.tmp").exists())

    async def test_pending_trigger_reports_busy(self) -> None:
        self.bridge.upgrade_trigger.write_bytes(b"")
        client, _hello = await self.connect()
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "upgrade_status", "state": "busy"})

    async def test_stale_trigger_is_not_busy_and_is_replaced(self) -> None:
        self.bridge.upgrade_trigger.write_bytes(b"")
        stale = time.time() - UPGRADE_TRIGGER_TTL_S - 1
        os.utime(self.bridge.upgrade_trigger, (stale, stale))
        self.assertFalse(self.bridge.upgrade_busy())
        client, _hello = await self.connect()
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "upgrade_status", "state": "started"})
        self.assertEqual(self.bridge.upgrade_trigger.read_bytes(), b"")
        self.assertGreater(self.bridge.upgrade_trigger.stat().st_mtime, stale)

    async def test_repeated_upgrades_stop_below_the_start_limit(self) -> None:
        client, _hello = await self.connect()
        for _ in range(UPGRADE_RATE_BURST):
            await client.send_json({"type": "upgrade"})
            reply = await asyncio.wait_for(client.recv_json(), 2)
            self.assertEqual(reply, {"type": "upgrade_status", "state": "started"})
            # systemd's ExecStartPre removes the trigger before the next request.
            self.bridge.upgrade_trigger.unlink()
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "upgrade_status", "state": "busy"})
        self.assertFalse(self.bridge.upgrade_trigger.exists())

    def test_start_limit_budget_survives_a_daemon_restart(self) -> None:
        for _ in range(UPGRADE_RATE_BURST):
            self.assertEqual(self.bridge.request_upgrade(), "started")
            self.bridge.upgrade_trigger.unlink()
        restarted = Bridge(
            token="secret",
            upgrade_dir=self.state_dir,
            upgrade_lock=self.state_dir / "upgrade.lock",
        )
        self.assertTrue(restarted.upgrade_rate_limited())
        self.assertFalse(restarted.upgrade_busy())

    async def test_running_root_unit_reports_busy(self) -> None:
        lock = self.state_dir / "upgrade.lock"
        with lock.open("w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            client, _hello = await self.connect()
            await client.send_json({"type": "upgrade"})
            reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "upgrade_status", "state": "busy"})
        self.assertFalse(self.bridge.upgrade_trigger.exists())

    async def test_unauthenticated_upgrade_fails(self) -> None:
        client, _hello = await self.connect(authenticate=False)
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(reply, {"type": "error", "code": "auth_failed"})
        self.assertFalse(self.bridge.upgrade_trigger.exists())

    async def test_tokenless_bridge_refuses_upgrade(self) -> None:
        self.bridge.token = None
        client, hello = await self.connect(authenticate=False)
        self.assertFalse(hello["upgradeCapable"])
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(
            reply,
            {"type": "upgrade_status", "state": "error", "code": "auth_failed"},
        )
        self.assertFalse(self.bridge.upgrade_trigger.exists())

    async def test_missing_state_directory_reports_unavailable(self) -> None:
        self.bridge.upgrade_dir = self.state_dir / "missing"
        client, hello = await self.connect()
        self.assertFalse(hello["upgradeCapable"])
        await client.send_json({"type": "upgrade"})
        reply = await asyncio.wait_for(client.recv_json(), 2)
        self.assertEqual(
            reply,
            {"type": "upgrade_status", "state": "error", "code": "unavailable"},
        )


if __name__ == "__main__":
    unittest.main()
