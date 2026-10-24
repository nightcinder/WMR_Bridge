"""A small, dependency-free WebSocket server/client (RFC 6455).

The host bridge needs only a handful of WebSocket features: an HTTP upgrade,
binary and text messages, ping/pong and a clean close. Implementing that here
keeps the Raspberry Pi install to ``python3`` plus the standard library, which
matters on a device that should boot unattended.

Not implemented: extensions (permessage-deflate), subprotocol negotiation
beyond a single match, and HTTP serving. Messages are unfragmented when sent;
fragmented messages from a peer are reassembled.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SUBPROTOCOL = "wmr-wifi-v1"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

DEFAULT_MAX_MESSAGE = 1 << 20
MAX_HEADER_BYTES = 16 * 1024
DEFAULT_HANDSHAKE_TIMEOUT = 5.0

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002
CLOSE_UNSUPPORTED = 1003
CLOSE_INVALID_PAYLOAD = 1007
CLOSE_POLICY = 1008
CLOSE_TOO_LARGE = 1009
CLOSE_INTERNAL = 1011


class WebSocketError(Exception):
    """Handshake or protocol failure."""


class WebSocketClosed(Exception):
    """The peer closed the connection (or it went away)."""

    def __init__(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        super().__init__(f"websocket closed ({code}) {reason}".strip())
        self.code = code
        self.reason = reason


def _accept_key(key: str) -> str:
    digest = hashlib.sha1((key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def _header_tokens(value: str) -> set[str]:
    return {token.strip().lower() for token in value.split(",") if token.strip()}


async def _read_http_headers(reader: asyncio.StreamReader, limit: int = MAX_HEADER_BYTES) -> bytes:
    try:
        data = await reader.readuntil(b"\r\n\r\n")
    except asyncio.IncompleteReadError as exc:
        raise WebSocketError("connection closed during the HTTP handshake") from exc
    except asyncio.LimitOverrunError as exc:
        raise WebSocketError("HTTP handshake is too large") from exc
    if len(data) > limit:
        raise WebSocketError("HTTP handshake is too large")
    return data


def _parse_headers(raw: bytes) -> tuple[str, list[tuple[str, str]]]:
    try:
        text = raw.decode("iso-8859-1")
    except UnicodeDecodeError as exc:  # pragma: no cover - iso-8859-1 always decodes
        raise WebSocketError("bad HTTP header encoding") from exc
    lines = text.split("\r\n")
    start = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep:
            raise WebSocketError(f"malformed HTTP header: {line!r}")
        headers.append((name.strip().lower(), value.strip()))
    return start, headers


def _first(headers: list[tuple[str, str]], name: str) -> str | None:
    for key, value in headers:
        if key == name:
            return value
    return None


def _all(headers: list[tuple[str, str]], name: str) -> str:
    return ", ".join(value for key, value in headers if key == name)


def _normalise_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _close_payload(code: int, reason: str) -> bytes:
    """Build a close payload without splitting a UTF-8 sequence.

    The reason field is limited to 123 bytes (125 minus the 2-byte code).
    Truncating the encoded bytes directly can leave an incomplete sequence,
    which peers must reject, so drop whole trailing characters instead.
    """

    encoded = reason.encode("utf-8")
    if len(encoded) > 123:
        encoded = encoded[:123]
        while encoded:
            try:
                encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
    return code.to_bytes(2, "big") + encoded


def _close_details(payload: bytes) -> tuple[int, str]:
    """Decode a received close payload; a 1-byte payload is a protocol error."""

    if not payload:
        return CLOSE_NORMAL, ""
    if len(payload) == 1:
        raise WebSocketError("close frame payload is 1 byte; a close code needs 2")
    code = int.from_bytes(payload[:2], "big")
    return code, payload[2:].decode("utf-8", errors="replace")


@dataclass
class WebSocket:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    is_client: bool
    subprotocol: str | None = None
    max_message: int = DEFAULT_MAX_MESSAGE

    def __post_init__(self) -> None:
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._close_sent = False
        self._peer = self.writer.get_extra_info("peername")

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def peer(self) -> Any:
        return self._peer

    # ---- sending ---------------------------------------------------------

    async def _send_frame(self, opcode: int, payload: bytes = b"", fin: bool = True) -> None:
        if self._closed:
            raise WebSocketClosed()
        first = (0x80 if fin else 0x00) | (opcode & 0x0F)
        length = len(payload)
        if length < 126:
            header = bytes((first, length))
        elif length <= 0xFFFF:
            header = bytes((first, 126)) + length.to_bytes(2, "big")
        else:
            header = bytes((first, 127)) + length.to_bytes(8, "big")
        # Clients mask every frame (RFC 6455 5.3); servers do not.
        if self.is_client:
            mask = os.urandom(4)
            header = bytes((header[0], header[1] | 0x80)) + header[2:] + mask
            payload = bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))
        async with self._send_lock:
            self.writer.write(header + payload)
            await self.writer.drain()

    async def send_binary(self, payload: bytes) -> None:
        await self._send_frame(OP_BINARY, bytes(payload))

    async def send_text(self, text: str) -> None:
        await self._send_frame(OP_TEXT, text.encode("utf-8"))

    async def send_json(self, value: Any) -> None:
        await self.send_text(json.dumps(value, separators=(",", ":"), ensure_ascii=False))

    async def ping(self, payload: bytes = b"") -> None:
        if len(payload) > 125:
            raise ValueError("ping payload is limited to 125 bytes")
        await self._send_frame(OP_PING, payload)

    async def close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        if not self._closed and not self._close_sent:
            try:
                await self._send_frame(OP_CLOSE, _close_payload(code, reason))
                self._close_sent = True
            except (WebSocketClosed, ConnectionError, asyncio.CancelledError):
                pass
        self._closed = True
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    # ---- receiving -------------------------------------------------------

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        header = await self.reader.readexactly(2)
        first, second = header
        fin = bool(first & 0x80)
        rsv = first & 0x70
        opcode = first & 0x0F
        if rsv:
            raise WebSocketError("reserved bits are set but no extension was negotiated")
        masked = bool(second & 0x80)
        if masked == self.is_client:
            # Servers receive masked frames, clients receive unmasked frames.
            raise WebSocketError("incorrect frame masking")
        length = second & 0x7F
        if length == 126:
            length = int.from_bytes(await self.reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self.reader.readexactly(8), "big")
        if length > self.max_message:
            raise WebSocketError(f"frame of {length} bytes exceeds the limit")
        if opcode >= 0x8 and (not fin or length > 125):
            raise WebSocketError("invalid control frame")
        mask = await self.reader.readexactly(4) if masked else b""
        payload = await self.reader.readexactly(length) if length else b""
        if masked and payload:
            payload = bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))
        return fin, opcode, payload

    async def recv(self) -> tuple[int, bytes]:
        """Return one complete ``(OP_TEXT|OP_BINARY, payload)`` message.

        Ping frames are answered transparently. A close frame raises
        :class:`WebSocketClosed` after echoing the close.
        """

        fragments = bytearray()
        message_opcode: int | None = None
        while True:
            try:
                fin, opcode, payload = await self._read_frame()
            except asyncio.IncompleteReadError as exc:
                self._closed = True
                raise WebSocketClosed(CLOSE_GOING_AWAY, "peer disconnected") from exc
            except (ConnectionError, OSError) as exc:
                self._closed = True
                raise WebSocketClosed(CLOSE_GOING_AWAY, "connection error") from exc

            if opcode == OP_CLOSE:
                code, reason = _close_details(payload)
                if not self._close_sent:
                    try:
                        await self._send_frame(OP_CLOSE, payload[:125])
                        self._close_sent = True
                    except (WebSocketClosed, ConnectionError):
                        pass
                self._closed = True
                raise WebSocketClosed(code, reason)
            if opcode == OP_PING:
                try:
                    await self._send_frame(OP_PONG, payload)
                except (WebSocketClosed, ConnectionError):
                    pass
                continue
            if opcode == OP_PONG:
                continue
            if opcode not in (OP_TEXT, OP_BINARY, OP_CONT):
                raise WebSocketError(f"unsupported opcode {opcode:#x}")
            if opcode == OP_CONT:
                if message_opcode is None:
                    raise WebSocketError("continuation frame without a message")
            else:
                if message_opcode is not None:
                    raise WebSocketError("new data frame inside a fragmented message")
                message_opcode = opcode
            fragments.extend(payload)
            if len(fragments) > self.max_message:
                raise WebSocketError("message exceeds the limit")
            if not fin:
                continue
            if message_opcode == OP_TEXT:
                try:
                    fragments.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise WebSocketError("text message is not valid UTF-8") from exc
            assert message_opcode is not None
            return message_opcode, bytes(fragments)

    async def recv_text(self) -> str:
        opcode, payload = await self.recv()
        if opcode != OP_TEXT:
            raise WebSocketError("expected a text message")
        return payload.decode("utf-8")

    async def recv_binary(self) -> bytes:
        opcode, payload = await self.recv()
        if opcode != OP_BINARY:
            raise WebSocketError("expected a binary message")
        return payload

    async def recv_json(self) -> Any:
        try:
            return json.loads(await self.recv_text())
        except json.JSONDecodeError as exc:
            raise WebSocketError("text message is not valid JSON") from exc


async def accept(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    subprotocols: tuple[str, ...] = (SUBPROTOCOL,),
    max_message: int = DEFAULT_MAX_MESSAGE,
    allowed_origins: tuple[str, ...] = (),
    handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
) -> WebSocket:
    """Perform the server side of the WebSocket handshake."""

    try:
        raw = await asyncio.wait_for(_read_http_headers(reader), timeout=handshake_timeout)
    except asyncio.TimeoutError as exc:
        await _reject(writer, 408, "Request Timeout")
        raise WebSocketError("HTTP handshake timed out") from exc
    start, headers = _parse_headers(raw)
    parts = start.split(" ")
    if len(parts) != 3 or parts[0] != "GET" or not parts[2].startswith("HTTP/1."):
        await _reject(writer, 400, "Bad Request")
        raise WebSocketError(f"bad request line: {start!r}")
    origin = _first(headers, "origin")
    if origin is not None:
        allowed = {_normalise_origin(item) for item in allowed_origins if item.strip()}
        if _normalise_origin(origin) not in allowed:
            await _reject(writer, 403, "Forbidden")
            raise WebSocketError(f"origin not allowed: {origin!r}")
    if "websocket" not in _header_tokens(_all(headers, "upgrade")):
        await _reject(writer, 426, "Upgrade Required", [("Upgrade", "websocket")])
        raise WebSocketError("not a WebSocket upgrade")
    if "upgrade" not in _header_tokens(_all(headers, "connection")):
        await _reject(writer, 400, "Bad Request")
        raise WebSocketError("Connection header does not contain 'upgrade'")
    if _first(headers, "sec-websocket-version") != "13":
        await _reject(writer, 426, "Upgrade Required", [("Sec-WebSocket-Version", "13")])
        raise WebSocketError("unsupported WebSocket version")
    key = _first(headers, "sec-websocket-key")
    if not key:
        await _reject(writer, 400, "Bad Request")
        raise WebSocketError("missing Sec-WebSocket-Key")
    try:
        if len(base64.b64decode(key, validate=True)) != 16:
            raise ValueError
    except ValueError as exc:
        await _reject(writer, 400, "Bad Request")
        raise WebSocketError("invalid Sec-WebSocket-Key") from exc

    offered = [token.strip() for token in _all(headers, "sec-websocket-protocol").split(",") if token.strip()]
    chosen = next((name for name in subprotocols if name in offered), None)

    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Accept: {_accept_key(key)}",
    ]
    if chosen:
        lines.append(f"Sec-WebSocket-Protocol: {chosen}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    await writer.drain()
    return WebSocket(reader, writer, is_client=False, subprotocol=chosen, max_message=max_message)


async def _reject(
    writer: asyncio.StreamWriter,
    status: int,
    reason: str,
    extra: list[tuple[str, str]] | None = None,
) -> None:
    body = f"{status} {reason}\n".encode("ascii")
    lines = [f"HTTP/1.1 {status} {reason}", "Connection: close", "Content-Type: text/plain"]
    for key, value in extra or []:
        lines.append(f"{key}: {value}")
    lines.append(f"Content-Length: {len(body)}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body)
    try:
        await writer.drain()
    except ConnectionError:
        pass
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


async def connect(
    host: str,
    port: int,
    path: str = "/wmr",
    *,
    subprotocol: str | None = SUBPROTOCOL,
    max_message: int = DEFAULT_MAX_MESSAGE,
    ssl: Any = None,
    timeout: float = 10.0,
    origin: str | None = None,
) -> WebSocket:
    """Open a WebSocket client connection."""

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ssl, server_hostname=host if ssl else None),
        timeout,
    )
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if subprotocol:
            lines.append(f"Sec-WebSocket-Protocol: {subprotocol}")
        if origin is not None:
            lines.append(f"Origin: {origin}")
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        await writer.drain()

        raw = await asyncio.wait_for(_read_http_headers(reader), timeout)
        start, headers = _parse_headers(raw)
        parts = start.split(" ")
        if len(parts) < 2 or parts[1] != "101":
            raise WebSocketError(f"server refused the upgrade: {start!r}")
        if _first(headers, "sec-websocket-accept") != _accept_key(key):
            raise WebSocketError("server returned a bad Sec-WebSocket-Accept")
        chosen = _first(headers, "sec-websocket-protocol")
        if chosen and chosen not in (subprotocol,):
            raise WebSocketError(f"server selected an unrequested subprotocol: {chosen!r}")
    except Exception:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass
        raise
    return WebSocket(reader, writer, is_client=True, subprotocol=chosen, max_message=max_message)


async def serve(
    handler: Any,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    subprotocols: tuple[str, ...] = (SUBPROTOCOL,),
    max_message: int = DEFAULT_MAX_MESSAGE,
    allowed_origins: tuple[str, ...] = (),
    handshake_timeout: float = DEFAULT_HANDSHAKE_TIMEOUT,
) -> asyncio.AbstractServer:
    """Start a WebSocket server; ``handler(websocket)`` runs per connection."""

    async def _client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            websocket = await accept(
                reader,
                writer,
                subprotocols=subprotocols,
                max_message=max_message,
                allowed_origins=allowed_origins,
                handshake_timeout=handshake_timeout,
            )
        except (WebSocketError, ConnectionError, asyncio.IncompleteReadError):
            if not writer.is_closing():
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            return
        try:
            await handler(websocket)
        except (WebSocketClosed, WebSocketError, ConnectionError, OSError):
            pass
        finally:
            await websocket.close()

    return await asyncio.start_server(_client, host=host, port=port)
