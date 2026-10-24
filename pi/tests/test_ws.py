"""WebSocket handshake, framing and close tests over loopback."""

from __future__ import annotations

import asyncio
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


if __name__ == "__main__":
    unittest.main()
