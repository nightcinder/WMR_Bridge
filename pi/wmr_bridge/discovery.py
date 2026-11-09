"""UDP discovery responder for LAN clients.

A client broadcasts ``WMR_DISCOVER_V1 <nonce>`` to UDP port 8765. Every bridge
that hears the probe answers the sender directly with one JSON object carrying
its identity and WebSocket endpoint. The reply echoes the nonce so a client can
match it to the probe it sent; it never contains the shared token. The reply is
only a hint: a client still has to open the WebSocket and check the ``hello``
``bridge_id`` before it sends a token (see ``docs/PI_BRIDGE.md``).

Unicast replies make discovery usable on networks that drop broadcast traffic,
while the per-source rate limit keeps a probe flood from turning into a reply
flood.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

from . import __version__

LOG = logging.getLogger("wmr_bridge.discovery")

PROBE_PREFIX = "WMR_DISCOVER_V1"
DISCOVERY_PORT = 8765
DEFAULT_PATH = "/wmr"
MAX_NONCE_LENGTH = 64
MAX_PROBE_BYTES = 256
DEFAULT_REPLY_INTERVAL_S = 1.0
MAX_TRACKED_SOURCES = 256


def parse_probe(data: bytes) -> str | None:
    """Return the probe nonce, or ``None`` when the datagram is not a probe."""

    if not data or len(data) > MAX_PROBE_BYTES:
        return None
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return None
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != PROBE_PREFIX:
        return None
    nonce = parts[1]
    if not 1 <= len(nonce) <= MAX_NONCE_LENGTH:
        return None
    return nonce


class DiscoveryResponder:
    """Answer ``WMR_DISCOVER_V1`` probes with unicast JSON metadata."""

    def __init__(
        self,
        *,
        bridge_id: str,
        name: str,
        port: int,
        hands: list[str] | tuple[str, ...],
        path: str = DEFAULT_PATH,
        version: str = __version__,
        auth_required: bool = False,
        listen_host: str = "0.0.0.0",
        listen_port: int = DISCOVERY_PORT,
        reply_interval_s: float = DEFAULT_REPLY_INTERVAL_S,
        max_tracked_sources: int = MAX_TRACKED_SOURCES,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger = LOG,
    ) -> None:
        self.bridge_id = bridge_id
        self.name = name
        self.port = port
        self.hands = list(hands)
        self.path = path
        self.version = version
        self.auth_mode = "token" if auth_required else "none"
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.reply_interval_s = max(0.0, reply_interval_s)
        self.max_tracked_sources = max(1, max_tracked_sources)
        self._clock = clock
        self._log = log
        self._transport: asyncio.DatagramTransport | None = None
        self._bound_port: int | None = None
        self._last_reply: dict[str, float] = {}

    def reply_json(self, nonce: str) -> dict[str, Any]:
        """Build the advertised metadata; the shared token is never included."""

        return {
            "type": "wmr_bridge",
            "protocol": 1,
            "bridge_id": self.bridge_id,
            "name": self.name,
            "port": self.port,
            "path": self.path,
            "hands": list(self.hands),
            "version": self.version,
            "authMode": self.auth_mode,
            "nonce": nonce,
        }

    def reply_for(self, data: bytes, address: str) -> bytes | None:
        """Return the reply datagram for one probe, or ``None`` to stay silent."""

        nonce = parse_probe(data)
        if nonce is None:
            return None
        if not self._may_reply(address):
            return None
        return json.dumps(self.reply_json(nonce), separators=(",", ":")).encode("utf-8")

    def _may_reply(self, address: str) -> bool:
        now = self._clock()
        previous = self._last_reply.get(address)
        if previous is not None and now - previous < self.reply_interval_s:
            return False
        self._last_reply[address] = now
        if len(self._last_reply) > self.max_tracked_sources:
            del self._last_reply[next(iter(self._last_reply))]
        return True

    async def start(self) -> None:
        """Bind the discovery socket; the caller decides whether failure is fatal."""

        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: _DiscoveryProtocol(self),
            local_addr=(self.listen_host, self.listen_port),
        )
        self._transport = transport
        sockname = transport.get_extra_info("sockname")
        if sockname:
            self._bound_port = int(sockname[1])
        self._log.info(
            "UDP discovery listening on %s:%d (bridge_id %s)",
            self.listen_host,
            self._bound_port if self._bound_port is not None else self.listen_port,
            self.bridge_id,
        )

    @property
    def bound_port(self) -> int | None:
        """Port the discovery socket is bound to, or ``None`` before ``start``."""

        return self._bound_port

    async def close(self) -> None:
        transport, self._transport = self._transport, None
        self._bound_port = None
        if transport is not None:
            transport.close()

    async def __aenter__(self) -> "DiscoveryResponder":
        await self.start()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, responder: DiscoveryResponder) -> None:
        self.responder = responder
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        reply = self.responder.reply_for(data, addr[0])
        if reply is None or self._transport is None:
            return
        try:
            self._transport.sendto(reply, addr)
        except OSError as exc:
            self.responder._log.debug("UDP discovery reply to %s failed: %s", addr[0], exc)

    def error_received(self, exc: Exception) -> None:
        self.responder._log.debug("UDP discovery socket error: %s", exc)
