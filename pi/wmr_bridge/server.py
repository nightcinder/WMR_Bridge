"""WebSocket server wrapper around :class:`wmr_bridge.bridge.Bridge`."""

from __future__ import annotations

import asyncio

from .bridge import Bridge
from . import ws as wsmod


class BridgeServer:
    def __init__(
        self,
        bridge: Bridge,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        allowed_origins: tuple[str, ...] = (),
        handshake_timeout: float = wsmod.DEFAULT_HANDSHAKE_TIMEOUT,
    ) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self.allowed_origins = allowed_origins
        self.handshake_timeout = handshake_timeout
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await wsmod.serve(
            self.bridge.run_client,
            host=self.host,
            port=self.port,
            allowed_origins=self.allowed_origins,
            handshake_timeout=self.handshake_timeout,
        )
        sockets = self._server.sockets or []
        if sockets and self.port == 0:
            self.port = int(sockets[0].getsockname()[1])

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
        subscribers = list(self.bridge.subscribers)
        for subscriber in subscribers:
            subscriber.close()
        if subscribers:
            await asyncio.gather(
                *(
                    subscriber.websocket.close(
                        wsmod.CLOSE_GOING_AWAY, "server shutting down"
                    )
                    for subscriber in subscribers
                ),
                return_exceptions=True,
            )
        self.bridge.subscribers.clear()
        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), 5.0)
            except asyncio.TimeoutError:
                pass

    async def __aenter__(self) -> "BridgeServer":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()
