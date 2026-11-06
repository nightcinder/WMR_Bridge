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
import zlib
from dataclasses import dataclass
from enum import IntEnum
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

# FF13 controller config block. The layout is the firmware's
# ``wmr_cfg_pack_window`` value, so a phone can reuse the BLE parser unchanged.
CALIBRATION_VERSION = 1
CALIBRATION_HEADER_SIZE = 16
CALIBRATION_WINDOW_MAX = 496
CALIBRATION_MAX_BLOB = 16 * 1024
CALIBRATION_CURSOR_SIZE = 5


class CalibrationState(IntEnum):
    """FF13 ``state`` values (docs/PROTOCOL.md, "FF13 characteristic")."""

    NOT_READ = 0
    READING = 1
    READY = 2
    FAILED = 3


CALIBRATION_STATE_NAMES = ("not_read", "reading", "ready", "failed")


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


def _check_hand(hand: int) -> int:
    if not isinstance(hand, int) or isinstance(hand, bool) or not 0 <= hand < NUM_HANDS:
        raise ProtocolError(f"hand out of range: {hand!r}")
    return hand


def _check_offset(offset: int) -> int:
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= 0xFFFFFFFF:
        raise ProtocolError(f"offset out of range: {offset!r}")
    return offset


def calibration_crc32(blob: bytes) -> int:
    """CRC-32 IEEE 802.3 of a whole config block, matching ``wmr_cfg_crc32``."""

    return zlib.crc32(blob) & 0xFFFFFFFF


_CALIBRATION_HEADER = struct.Struct("<BBBBI I I")


@dataclass(frozen=True)
class CalibrationWindow:
    """One FF13 read/notify value: a 16-byte header plus up to 496 data bytes."""

    hand: int
    state: CalibrationState
    total_len: int
    offset: int
    crc32: int
    data: bytes = b""

    @property
    def ready(self) -> bool:
        return self.state is CalibrationState.READY


def encode_calibration_window(
    hand: int,
    state: CalibrationState | int,
    blob: bytes = b"",
    offset: int = 0,
) -> bytes:
    """Encode the FF13 value for ``blob`` at ``offset``.

    A non-READY state carries no data, ``total_len`` or CRC, exactly like the
    firmware. A READY state carries at most ``CALIBRATION_WINDOW_MAX`` bytes.
    """

    hand = _check_hand(hand)
    offset = _check_offset(offset)
    try:
        state = CalibrationState(state)
    except ValueError as exc:
        raise ProtocolError(f"bad calibration state {state!r}") from exc
    blob = bytes(blob)
    if len(blob) > CALIBRATION_MAX_BLOB:
        raise ProtocolError(f"config block is {len(blob)} bytes, limit is {CALIBRATION_MAX_BLOB}")

    ready = state is CalibrationState.READY
    if ready and not blob:
        raise ProtocolError("READY requires a non-empty config block")
    total = len(blob) if ready else 0
    crc = calibration_crc32(blob) if ready else 0
    data = b""
    if ready and offset < total:
        data = blob[offset : offset + CALIBRATION_WINDOW_MAX]
    header = _CALIBRATION_HEADER.pack(
        CALIBRATION_VERSION, hand, int(state), 0, total, offset, crc
    )
    return header + data


def parse_calibration_window(packet: bytes) -> CalibrationWindow:
    """Parse one FF13 value. Raises :class:`ProtocolError` when malformed."""

    if len(packet) < CALIBRATION_HEADER_SIZE:
        raise ProtocolError("calibration window is shorter than its header")
    if len(packet) > CALIBRATION_HEADER_SIZE + CALIBRATION_WINDOW_MAX:
        raise ProtocolError("calibration window is longer than the maximum")
    version, hand, state_value, _reserved, total, offset, crc = _CALIBRATION_HEADER.unpack_from(packet)
    if version != CALIBRATION_VERSION:
        raise ProtocolError(f"unknown calibration version {version}")
    hand = _check_hand(hand)
    try:
        state = CalibrationState(state_value)
    except ValueError as exc:
        raise ProtocolError(f"bad calibration state {state_value}") from exc
    data = bytes(packet[CALIBRATION_HEADER_SIZE:])
    if state is not CalibrationState.READY:
        if total != 0 or crc != 0 or data:
            raise ProtocolError("a non-READY calibration window must be empty")
        return CalibrationWindow(hand, state, 0, offset, 0, b"")
    if total == 0 or total > CALIBRATION_MAX_BLOB:
        raise ProtocolError(f"bad calibration blob length {total}")
    expected = 0 if offset >= total else min(total - offset, CALIBRATION_WINDOW_MAX)
    if len(data) != expected:
        raise ProtocolError(f"calibration window has {len(data)} bytes, expected {expected}")
    return CalibrationWindow(hand, state, total, offset, crc, data)


def parse_calibration_cursor(value: bytes) -> tuple[int, int]:
    """Parse the FF13 cursor write: ``hand`` u8 followed by ``offset`` u32 LE."""

    if len(value) != CALIBRATION_CURSOR_SIZE:
        raise ProtocolError(f"calibration cursor is {len(value)} bytes, expected 5")
    hand, offset = struct.unpack("<BI", value)
    return _check_hand(hand), _check_offset(offset)


class CalibrationCache:
    """Per-hand FF13 config-block cache.

    The cache stores the controller block exactly as received (including the
    leading two bytes and the obfuscation); it never parses or rewrites it. An
    ingest layer marks a read in progress with :meth:`set_state` and publishes a
    completed block with :meth:`set_blob`.
    """

    def __init__(self, max_blob: int = CALIBRATION_MAX_BLOB) -> None:
        if max_blob < 1:
            raise ValueError("max_blob must be positive")
        self.max_blob = min(max_blob, CALIBRATION_MAX_BLOB)
        self._state = [CalibrationState.NOT_READ] * NUM_HANDS
        self._blob = [b""] * NUM_HANDS
        self._crc = [0] * NUM_HANDS

    def state(self, hand: int) -> CalibrationState:
        return self._state[_check_hand(hand)]

    def blob(self, hand: int) -> bytes:
        return self._blob[_check_hand(hand)]

    def total(self, hand: int) -> int:
        hand = _check_hand(hand)
        return len(self._blob[hand]) if self._state[hand] is CalibrationState.READY else 0

    def crc32(self, hand: int) -> int:
        hand = _check_hand(hand)
        return self._crc[hand] if self._state[hand] is CalibrationState.READY else 0

    @property
    def ready(self) -> bool:
        return any(state is CalibrationState.READY for state in self._state)

    def set_state(self, hand: int, state: CalibrationState | int) -> None:
        hand = _check_hand(hand)
        try:
            state = CalibrationState(state)
        except ValueError as exc:
            raise ProtocolError(f"bad calibration state {state!r}") from exc
        if state is CalibrationState.READY:
            if not self._blob[hand]:
                raise ProtocolError("READY requires a cached blob; use set_blob")
        else:
            self._blob[hand] = b""
            self._crc[hand] = 0
        self._state[hand] = state

    def set_blob(self, hand: int, blob: bytes) -> None:
        hand = _check_hand(hand)
        blob = bytes(blob)
        if not blob:
            raise ProtocolError("config block is empty")
        if len(blob) > self.max_blob:
            raise ProtocolError(f"config block is {len(blob)} bytes, limit is {self.max_blob}")
        self._blob[hand] = blob
        self._crc[hand] = calibration_crc32(blob)
        self._state[hand] = CalibrationState.READY

    def window(self, hand: int, offset: int = 0) -> bytes:
        hand = _check_hand(hand)
        blob = self._blob[hand] if self._state[hand] is CalibrationState.READY else b""
        return encode_calibration_window(hand, self._state[hand], blob, offset)

    def describe(self, hand: int) -> dict[str, int | str]:
        hand = _check_hand(hand)
        return {
            "state": CALIBRATION_STATE_NAMES[int(self._state[hand])],
            "totalLen": self.total(hand),
            "crc32": self.crc32(hand),
        }
