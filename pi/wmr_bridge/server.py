"""WebSocket server wrapper around :class:`wmr_bridge.bridge.Bridge`."""

from __future__ import annotations

import asyncio

from .bridge import Bridge
from . import ws as wsmod


class BridgeServer:
    def __init__(self, bridge: Bridge, host: str = "0.0.0.0", port: int = 8765) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await wsmod.serve(
            self.bridge.run_client,
            host=self.host,
            port=self.port,
        )
        sockets = self._server.sockets or []
        if sockets and self.port == 0:
            self.port = int(sockets[0].getsockname()[1])

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for subscriber in list(self.bridge.subscribers):
            subscriber.close()
        self.bridge.subscribers.clear()

    async def __aenter__(self) -> "BridgeServer":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
