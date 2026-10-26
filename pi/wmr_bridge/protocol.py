"""Wire formats shared with the ESP32 bridge.

The Linux/Pi host bridge deliberately reuses the payloads defined in
``docs/PROTOCOL.md`` instead of inventing a new stream:

* raw stream  - the 12-byte fragment header plus a chunk of one HID input report
  (46 bytes for WMR, 79 bytes for PSVR2)
* decoded     - one 56-byte FF12 version 1 record
* batched     - the 2-byte FF14 version 2 header plus 1..9 decoded records

Only the link underneath changes (WebSocket over Wi-Fi instead of BLE GATT).
Keeping the payloads identical means the companion app can reuse its existing
reassembler, record parser and batch parser unchanged.

Pure Python, no dependencies. All multi-byte integers are little-endian.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Sequence

PROTO_MAGIC = 0x52
PROTO_HEADER_SIZE = 12
MAX_FRAME = 80
NUM_HANDS = 2

DECODED_VERSION = 1
DECODED_SIZE = 56

BATCH_VERSION = 2
BATCH_HEADER_SIZE = 2
BATCH_MAX_COUNT = 9

# The firmware clamps a BLE notification value to 244 bytes. On Wi-Fi there is
# no ATT MTU, so the host bridge defaults to the same 244-byte capacity; both a
# 46-byte WMR frame and a 79-byte PSVR2 frame therefore travel in one fragment.
# Keeping the ceiling means the companion's reference reassembler sees a shape
# it already handles.
DEFAULT_FRAGMENT_CAPACITY = 244


class ProtocolError(ValueError):
    """Raised when a frame or record cannot be encoded or parsed."""


@dataclass(frozen=True)
class Frame:
    """One controller input report, exactly as the ESP32 would queue it."""

    hand: int
    sequence: int
    data: bytes
    rx_time_us: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.hand < NUM_HANDS:
            raise ProtocolError(f"hand out of range: {self.hand}")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ProtocolError(f"sequence out of range: {self.sequence}")
        if not 0 < len(self.data) <= MAX_FRAME:
            raise ProtocolError(f"frame length out of range: {len(self.data)}")
        if not 0 <= self.rx_time_us <= 0xFFFFFFFFFFFFFFFF:
            raise ProtocolError(f"rx_time_us out of range: {self.rx_time_us}")


def encode_fragment(frame: Frame, offset: int, capacity: int) -> bytes:
    """Encode the fragment of ``frame`` that starts at ``offset``.

    ``capacity`` is the maximum notification value size (header + payload).
    Returns ``b""`` for invalid arguments, matching the C encoder's "nothing
    written" contract. One frame may take several fragments when the capacity
    is small; the host bridge normally uses a capacity that fits one frame.
    """

    if capacity <= PROTO_HEADER_SIZE or offset < 0 or offset >= len(frame.data):
        return b""
    chunk = min(capacity - PROTO_HEADER_SIZE, len(frame.data) - offset)
    if chunk <= 0:
        return b""
    header = struct.pack(
        "<BBIHHH",
        PROTO_MAGIC,
        frame.hand,
        frame.sequence,
        len(frame.data),
        offset,
        chunk,
    )
    return header + frame.data[offset : offset + chunk]


def encode_raw_messages(frame: Frame, capacity: int = DEFAULT_FRAGMENT_CAPACITY) -> list[bytes]:
    """All fragments of ``frame`` in order."""

    messages: list[bytes] = []
    offset = 0
    while offset < len(frame.data):
        packet = encode_fragment(frame, offset, capacity)
        if not packet:
            raise ProtocolError(f"cannot encode frame at offset {offset}")
        messages.append(packet)
        offset += len(packet) - PROTO_HEADER_SIZE
    return messages


@dataclass(frozen=True)
class DecodedRecord:
    """One FF12 version 1 record (uncalibrated values)."""

    hand: int
    sequence: int
    rx_time_us: int
    device_ticks: int
    gyro: tuple[float, float, float]
    accel: tuple[float, float, float]
    temperature_raw: int
    stick: tuple[int, int]
    trackpad: tuple[int, int]
    trigger: int
    battery: int
    buttons: int = 0
    flags: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.hand < NUM_HANDS:
            raise ProtocolError(f"hand out of range: {self.hand}")
        if not 0 <= self.sequence <= 0xFFFFFFFF:
            raise ProtocolError(f"sequence out of range: {self.sequence}")
        if not 0 <= self.device_ticks <= 0xFFFFFFFF:
            raise ProtocolError(f"device_ticks out of range: {self.device_ticks}")
        if not 0 <= self.trigger <= 0xFF or not 0 <= self.battery <= 0xFF:
            raise ProtocolError("trigger and battery must be bytes")
        if not 0 <= self.buttons <= 0xFF or not 0 <= self.flags <= 0xFF:
            raise ProtocolError("buttons and flags must be bytes")


_RECORD = struct.Struct("<BBBB I Q I 3f 3f h 4h BB")


def pack_record(record: DecodedRecord) -> bytes:
    """Serialize a record to exactly 56 bytes (FF12 layout)."""

    return _RECORD.pack(
        DECODED_VERSION,
        record.hand,
        record.flags,
        record.buttons,
        record.sequence,
        record.rx_time_us,
        record.device_ticks,
        *record.gyro,
        *record.accel,
        record.temperature_raw,
        *record.stick,
        *record.trackpad,
        record.trigger,
        record.battery,
    )


def unpack_record(packet: bytes) -> DecodedRecord:
    """Parse one FF12 record. Raises :class:`ProtocolError` when malformed."""

    if len(packet) != DECODED_SIZE:
        raise ProtocolError(f"record is {len(packet)} bytes, expected {DECODED_SIZE}")
    if packet[0] != DECODED_VERSION:
        raise ProtocolError(f"unknown record version {packet[0]}")
    if packet[1] >= NUM_HANDS:
        raise ProtocolError(f"bad hand {packet[1]}")
    (
        _version,
        hand,
        flags,
        buttons,
        sequence,
        rx_time_us,
        device_ticks,
        gx,
        gy,
        gz,
        ax,
        ay,
        az,
        temperature_raw,
        stick_x,
        stick_y,
        trackpad_x,
        trackpad_y,
        trigger,
        battery,
    ) = _RECORD.unpack(packet)
    return DecodedRecord(
        hand=hand,
        sequence=sequence,
        rx_time_us=rx_time_us,
        device_ticks=device_ticks,
        gyro=(gx, gy, gz),
        accel=(ax, ay, az),
        temperature_raw=temperature_raw,
        stick=(stick_x, stick_y),
        trackpad=(trackpad_x, trackpad_y),
        trigger=trigger,
        battery=battery,
        buttons=buttons,
        flags=flags,
    )


def encode_batch(records: Sequence[bytes]) -> bytes:
    """Wrap 1..9 packed records in an FF14 version 2 batch."""

    if not 1 <= len(records) <= BATCH_MAX_COUNT:
        raise ProtocolError(f"batch must hold 1..{BATCH_MAX_COUNT} records")
    for record in records:
        if len(record) != DECODED_SIZE:
            raise ProtocolError("batch members must be 56-byte records")
    return bytes((BATCH_VERSION, len(records))) + b"".join(records)


def parse_batch(packet: bytes) -> list[bytes]:
    """Return the records of an FF14 batch. Raises :class:`ProtocolError`."""

    if len(packet) < BATCH_HEADER_SIZE + DECODED_SIZE:
        raise ProtocolError("batch is too short")
    if packet[0] != BATCH_VERSION:
        raise ProtocolError(f"unknown batch version {packet[0]}")
    count = packet[1]
    if count < 1 or count > BATCH_MAX_COUNT:
        raise ProtocolError(f"bad batch count {count}")
    if len(packet) != BATCH_HEADER_SIZE + count * DECODED_SIZE:
        raise ProtocolError("batch length does not match its count")
    return [
        packet[BATCH_HEADER_SIZE + i * DECODED_SIZE : BATCH_HEADER_SIZE + (i + 1) * DECODED_SIZE]
        for i in range(count)
    ]


class Reassembler:
    """Reference raw-stream reassembler, per hand, matching the C rules."""

    def __init__(self) -> None:
        self._slot: list[dict | None] = [None, None]
        self.frames_complete = [0, 0]
        self.frames_incomplete = [0, 0]
        self.errors = 0

    def push(self, packet: bytes) -> Frame | None:
        """Feed one notification value; return a :class:`Frame` when complete."""

        if len(packet) < PROTO_HEADER_SIZE:
            self.errors += 1
            return None
        magic = packet[0]
        hand = packet[1]
        sequence, total, offset, chunk = struct.unpack_from("<IHHH", packet, 2)
        if magic != PROTO_MAGIC or hand >= NUM_HANDS:
            self.errors += 1
            if hand < NUM_HANDS:
                self._abandon(hand)
            return None
        if (
            chunk != len(packet) - PROTO_HEADER_SIZE
            or chunk == 0
            or total == 0
            or total > MAX_FRAME
            or offset >= total
            or offset + chunk > total
        ):
            self.errors += 1
            self._abandon(hand)
            return None

        slot = self._slot[hand]
        if offset == 0:
            if slot is not None:
                self.frames_incomplete[hand] += 1
            slot = {"sequence": sequence, "total": total, "buf": bytearray()}
            self._slot[hand] = slot
        else:
            if (
                slot is None
                or slot["sequence"] != sequence
                or slot["total"] != total
                or len(slot["buf"]) != offset
            ):
                self._abandon(hand)
                return None
        slot["buf"].extend(packet[PROTO_HEADER_SIZE:])
        if len(slot["buf"]) < total:
            return None
        self._slot[hand] = None
        self.frames_complete[hand] += 1
        return Frame(hand=hand, sequence=sequence, data=bytes(slot["buf"]))

    def _abandon(self, hand: int) -> None:
        if self._slot[hand] is not None:
            self.frames_incomplete[hand] += 1
            self._slot[hand] = None


def iter_records(batch: bytes) -> Iterable[bytes]:
    """Convenience wrapper used by the probe and tests."""

    return iter(parse_batch(batch))
