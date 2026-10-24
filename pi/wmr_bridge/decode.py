"""Port of ``src/wmr_decode.c`` and ``src/wmr_decoded.c`` to Python.

Only the parts needed by the Linux/Pi host bridge are here: the 46-byte
controller input report is decoded into the same 56-byte FF12 version 1 record
that the ESP32 firmware sends. Values stay uncalibrated (``flags`` bit 0 clear)
because calibration is applied by the companion app, exactly as for FF12.

The integer/float conversions mirror the C reference, including float32
rounding and round-half-away-from-zero for Q15. ``pi/tests/test_decode.py``
pins the result against records produced by the C implementation.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from fractions import Fraction

from .protocol import DecodedRecord, Frame

FRAME_LENGTH = 46
REPORT_ID = 0x01

# Button bits, same positions as the controller report.
BTN_THUMBSTICK_CLICK = 0x01
BTN_HOME = 0x02
BTN_MENU = 0x04
BTN_SQUEEZE = 0x08
BTN_TRACKPAD_CLICK = 0x10
BTN_BT_PAIRING = 0x20
BTN_TRACKPAD_TOUCH = 0x40

_F32 = struct.Struct("<f")


def _f32(value: float) -> float:
    """Round to IEEE 754 binary32, the C ``float`` type."""

    return _F32.unpack(_F32.pack(value))[0]


def _round_half_even(value: Fraction) -> int:
    quotient, remainder = divmod(value.numerator, value.denominator)
    twice = 2 * remainder
    if twice > value.denominator or (twice == value.denominator and quotient % 2):
        quotient += 1
    return quotient


def _f32_fraction(value: Fraction) -> float:
    """Round an exact rational to binary32 (round to nearest, ties to even).

    Python's float is binary64, and rounding a binary64 quotient to binary32
    can round twice. The decoder must match the C reference bit for bit, so
    the arithmetic is done on exact rationals and rounded once.
    """

    if value == 0:
        return 0.0
    sign = -1.0 if value < 0 else 1.0
    value = abs(value)
    exponent = value.numerator.bit_length() - value.denominator.bit_length()
    if Fraction(2) ** exponent > value:
        exponent -= 1
    elif Fraction(2) ** (exponent + 1) <= value:
        exponent += 1
    if exponent < -126:
        mantissa = _round_half_even(value / Fraction(2) ** -149)
        return sign * float(Fraction(mantissa) * Fraction(2) ** -149)
    mantissa = _round_half_even(value / Fraction(2) ** (exponent - 23))
    if mantissa >= 1 << 24:
        exponent += 1
        mantissa >>= 1
    if exponent > 127:
        return sign * math.inf
    return sign * float(Fraction(mantissa) * Fraction(2) ** (exponent - 23))


def _f32_mul_int(value: int, constant: float) -> float:
    """``(float)value * constant`` with C float semantics."""

    return _f32_fraction(Fraction(value) * Fraction(constant))


def _f32_div_int(value: int, divisor: float) -> float:
    """``(float)value / divisor`` with C float semantics."""

    return _f32_fraction(Fraction(value) / Fraction(divisor))


_GYRO_SCALE = _f32(0.00001)
_ACCEL_DIVISOR = 49000.0
_STICK_DIVISOR = float(0x07FF)
_TRIGGER_DIVISOR = 255.0
_TRACKPAD_CENTRE = 0x32
_TRACKPAD_DIVISOR = 0x32


def _read24s(payload: bytes, offset: int) -> int:
    value = payload[offset] | (payload[offset + 1] << 8) | (payload[offset + 2] << 16)
    if value & 0x800000:
        value -= 0x1000000
    return value


def _clamp_max1(value: float) -> float:
    return 1.0 if value > 1.0 else value


def _q15(value: float) -> int:
    value = min(1.0, max(-1.0, value))
    scaled = _f32_fraction(Fraction(value) * 32767)
    if scaled >= 0.0:
        return int(math.floor(_f32_fraction(Fraction(scaled) + Fraction(1, 2))))
    return int(math.ceil(_f32_fraction(Fraction(scaled) - Fraction(1, 2))))


@dataclass(frozen=True)
class ControllerState:
    buttons: int
    thumbstick_x: float
    thumbstick_y: float
    trigger: float
    trackpad_x: float
    trackpad_y: float
    battery: int
    accel_raw: tuple[int, int, int]
    accel: tuple[float, float, float]
    temperature_raw: int
    gyro_raw: tuple[int, int, int]
    gyro: tuple[float, float, float]
    ticks: int

    @property
    def thumbstick_click(self) -> bool:
        return bool(self.buttons & BTN_THUMBSTICK_CLICK)

    @property
    def home(self) -> bool:
        return bool(self.buttons & BTN_HOME)

    @property
    def menu(self) -> bool:
        return bool(self.buttons & BTN_MENU)

    @property
    def squeeze(self) -> bool:
        return bool(self.buttons & BTN_SQUEEZE)

    @property
    def trackpad_click(self) -> bool:
        return bool(self.buttons & BTN_TRACKPAD_CLICK)

    @property
    def bt_pairing(self) -> bool:
        return bool(self.buttons & BTN_BT_PAIRING)

    @property
    def trackpad_touch(self) -> bool:
        return bool(self.buttons & BTN_TRACKPAD_TOUCH)


def decode_frame(frame: bytes) -> ControllerState | None:
    """Decode a 46-byte HID input report, or return ``None`` if it is not one."""

    if len(frame) != FRAME_LENGTH or frame[0] != 0xA1 or frame[1] != REPORT_ID:
        return None
    p = frame[2:]

    stick_x_raw = p[1] | ((p[2] & 0x0F) << 8)
    stick_y_raw = (p[2] >> 4) | (p[3] << 4)

    accel_raw = (
        _read24s(p, 8),
        _read24s(p, 11),
        _read24s(p, 14),
    )
    gyro_raw = (
        _read24s(p, 19),
        _read24s(p, 22),
        _read24s(p, 25),
    )

    return ControllerState(
        buttons=p[0],
        thumbstick_x=_clamp_max1(_f32_div_int(stick_x_raw - 0x07FF, _STICK_DIVISOR)),
        thumbstick_y=_clamp_max1(_f32_div_int(stick_y_raw - 0x07FF, _STICK_DIVISOR)),
        trigger=_f32_div_int(p[4], _TRIGGER_DIVISOR),
        trackpad_x=0.0
        if p[5] == 0xFF
        else _f32_div_int(p[5] - _TRACKPAD_CENTRE, _TRACKPAD_DIVISOR),
        trackpad_y=0.0
        if p[6] == 0xFF
        else _f32_div_int(p[6] - _TRACKPAD_CENTRE, _TRACKPAD_DIVISOR),
        battery=p[7],
        accel_raw=accel_raw,
        accel=tuple(_f32_div_int(value, _ACCEL_DIVISOR) for value in accel_raw),  # type: ignore[arg-type]
        temperature_raw=struct.unpack_from("<h", p, 17)[0],
        gyro_raw=gyro_raw,
        gyro=tuple(_f32_mul_int(value, _GYRO_SCALE) for value in gyro_raw),  # type: ignore[arg-type]
        ticks=struct.unpack_from("<I", p, 28)[0],
    )


def record_from_state(
    state: ControllerState,
    hand: int,
    sequence: int,
    rx_time_us: int,
) -> DecodedRecord:
    """Build the FF12 record from a decoded state (as the C helper does)."""

    return DecodedRecord(
        hand=hand,
        sequence=sequence,
        rx_time_us=rx_time_us,
        device_ticks=state.ticks,
        gyro=state.gyro,
        accel=state.accel,
        temperature_raw=state.temperature_raw,
        stick=(_q15(state.thumbstick_x), _q15(state.thumbstick_y)),
        trackpad=(_q15(state.trackpad_x), _q15(state.trackpad_y)),
        trigger=int(
            _f32_fraction(
                Fraction(_f32_fraction(Fraction(state.trigger) * 255)) + Fraction(1, 2)
            )
        )
        & 0xFF,
        battery=state.battery,
        buttons=state.buttons,
        flags=0,
    )


def record_from_frame(frame: Frame) -> DecodedRecord | None:
    """Decode a raw :class:`Frame` into a record, or ``None`` if invalid."""

    state = decode_frame(frame.data)
    if state is None or not 0 <= frame.hand < 2:
        return None
    return record_from_state(state, frame.hand, frame.sequence, frame.rx_time_us)


def extend_ticks(previous: int, ticks: int) -> int:
    """Extend a 32-bit controller tick counter across wraps (C parity)."""

    low = previous & 0xFFFFFFFF
    high = previous & ~0xFFFFFFFF
    if ticks < low:
        high += 1 << 32
    return high | ticks


def gyro_magnitude(state: ControllerState) -> float:
    """Diagnostic helper: gyro magnitude in rad/s."""

    return math.sqrt(sum(value * value for value in state.gyro))
