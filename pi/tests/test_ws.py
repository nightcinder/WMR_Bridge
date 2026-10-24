"""WebSocket handshake, framing and close tests over loopback."""

from __future__ import annotations

import asyncio
import base64
import unittest

from wmr_bridge import ws as wsmod


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


if __name__ == "__main__":
    unittest.main()
