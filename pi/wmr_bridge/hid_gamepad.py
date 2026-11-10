"""HID gamepad report map and mapping for the Pi's BLE HID service.

This is a Python port of the pure part of ``src/wmr_hid.c``: the 9-byte report
descriptor and the delta-free mapper from two controller states to the HID
input report. The firmware and this module share the vectors in
``tests/vectors/hid_gamepad.txt`` and ``tests/vectors/hid_report_descriptor.txt``,
so a change on either side fails the other's tests.

Only the mapping rules used by the firmware are here. The firmware has no
orientation fusion and passes no tilt, and the Pi feeds the mapper the FF12
record's Q15 stick/trackpad values divided by 32767, so the same float inputs
produce the same bytes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .decode import (
    BTN_HOME,
    BTN_MENU,
    BTN_SQUEEZE,
    BTN_THUMBSTICK_CLICK,
    BTN_TRACKPAD_CLICK,
    BTN_TRACKPAD_TOUCH,
)

REPORT_ID = 0x01
REPORT_SIZE = 9
HAT_CENTRED = 15

# Byte for byte ``hid_report_descriptor`` in src/wmr_hid.c, which is the
# companion package's ``hidReportDescriptor``. tests/vectors/
# hid_report_descriptor.txt pins it.
REPORT_MAP = bytes(
    (
        0x05, 0x01,  # Usage Page (Generic Desktop)
        0x09, 0x05,  # Usage (Game Pad)
        0xA1, 0x01,  # Collection (Application)
        0x85, REPORT_ID,  # Report ID
        0x05, 0x09,  # Usage Page (Button)
        0x19, 0x01,  # Usage Minimum (1)
        0x29, 0x10,  # Usage Maximum (16)
        0x15, 0x00,  # Logical Minimum (0)
        0x25, 0x01,  # Logical Maximum (1)
        0x75, 0x01,  # Report Size (1)
        0x95, 0x10,  # Report Count (16)
        0x81, 0x02,  # Input (Data, Variable, Absolute)
        0x05, 0x01,  # Usage Page (Generic Desktop)
        0x09, 0x39,  # Usage (Hat switch)
        0x15, 0x00,  # Logical Minimum (0)
        0x25, 0x07,  # Logical Maximum (7)
        0x35, 0x00,  # Physical Minimum (0)
        0x46, 0x3B, 0x01,  # Physical Maximum (315)
        0x65, 0x14,  # Unit (degrees)
        0x75, 0x04,  # Report Size (4)
        0x95, 0x01,  # Report Count (1)
        0x81, 0x42,  # Input (Data, Variable, Absolute, Null state)
        0x65, 0x00,  # Unit (none)
        0x75, 0x04,  # Report Size (4)
        0x95, 0x01,  # Report Count (1)
        0x81, 0x03,  # Input (Constant): padding
        0x05, 0x01,  # Usage Page (Generic Desktop)
        0x09, 0x30,  # Usage (X)
        0x09, 0x31,  # Usage (Y)
        0x09, 0x32,  # Usage (Z)
        0x09, 0x35,  # Usage (Rz)
        0x15, 0x81,  # Logical Minimum (-127)
        0x25, 0x7F,  # Logical Maximum (127)
        0x75, 0x08,  # Report Size (8)
        0x95, 0x04,  # Report Count (4)
        0x81, 0x02,  # Input (Data, Variable, Absolute)
        0x05, 0x02,  # Usage Page (Simulation Controls)
        0x09, 0xC5,  # Usage (Brake)
        0x09, 0xC4,  # Usage (Accelerator)
        0x15, 0x00,  # Logical Minimum (0)
        0x26, 0xFF, 0x00,  # Logical Maximum (255)
        0x75, 0x08,  # Report Size (8)
        0x95, 0x02,  # Report Count (2)
        0x81, 0x02,  # Input (Data, Variable, Absolute)
        0xC0,  # End Collection
    )
)

NEUTRAL_REPORT = bytes((0, 0, HAT_CENTRED, 0, 0, 0, 0, 0, 0))

# Button bit positions in the report, which are also the Button usage numbers
# the host sees. C and Z have no WMR control but still occupy their slots, so
# X..R3 sit two bits later than the companion's GamepadButton order.
GP_A = 0
GP_B = 1
GP_C = 2
GP_X = 3
GP_Y = 4
GP_Z = 5
GP_L1 = 6
GP_R1 = 7
GP_L2 = 8
GP_R2 = 9
GP_SELECT = 10
GP_START = 11
GP_GUIDE = 12
GP_L3 = 13
GP_R3 = 14
GP_DPAD_UP = 15
GP_DPAD_DOWN = 16
GP_DPAD_LEFT = 17
GP_DPAD_RIGHT = 18

GP_MASK_REPORT = 0x7FFF  # bits 0..14 are the 15 used HID buttons

TRACKPAD_NONE = 0
TRACKPAD_DPAD = 1
TRACKPAD_FACE = 2

# sin(22.5 deg): a component above this share of the radius presses its
# direction, which gives 8 equal sectors.
_DIAGONAL = 0.38268343236508984


def clamp(value: float, low: float, high: float) -> float:
    """Clamp with the C mapper's treatment of NaN/infinity."""

    if not math.isfinite(value):
        value = 0.0
    if value < low:
        return low
    if value > high:
        return high
    return value


@dataclass
class HidConfig:
    """The firmware's ``wmr_hid_config_t`` defaults."""

    trigger_full_scale: float = 255.0
    trigger_dead_zone: float = 0.05
    trigger_press_threshold: float = 0.55
    trigger_release_threshold: float = 0.45
    trackpad_centre_radius: float = 0.3
    left_trackpad: int = TRACKPAD_DPAD
    right_trackpad: int = TRACKPAD_FACE
    invert_stick_y: bool = False
    invert_trackpad_y: bool = False


DEFAULT_CONFIG = HidConfig()


@dataclass
class HandInput:
    """One hand in the mapper's units: -1..1 axes, raw 0..255 trigger."""

    connected: bool = False
    buttons: int = 0
    stick: tuple[float, float] = (0.0, 0.0)
    trigger: int = 0
    trackpad: tuple[float, float] = (0.0, 0.0)


@dataclass
class HidHistory:
    """L2/R2 state of the previous report, for the trigger Schmitt trigger."""

    l2: bool = False
    r2: bool = False


def _trigger_value(raw: int, config: HidConfig) -> float:
    full = config.trigger_full_scale if config.trigger_full_scale > 1.0 else 1.0
    value = clamp(float(raw) / full, 0.0, 1.0)
    dead_zone = clamp(config.trigger_dead_zone, 0.0, 0.95)
    if value <= dead_zone:
        return 0.0
    return clamp((value - dead_zone) / (1.0 - dead_zone), 0.0, 1.0)


def _trigger_pressed(value: float, config: HidConfig, was_pressed: bool) -> bool:
    if not value > 0.0:
        return False
    press = clamp(config.trigger_press_threshold, 0.0, 1.0)
    release = clamp(config.trigger_release_threshold, 0.0, press)
    return value > release if was_pressed else value >= press


def _trackpad_buttons(x: float, y: float, role: int, centre_radius: float) -> int:
    if not (math.isfinite(x) and math.isfinite(y)):
        return 0
    radius = math.hypot(x, y)
    if radius < centre_radius or radius == 0.0:
        return 0
    if role == TRACKPAD_DPAD:
        out = 0
        if y / radius > _DIAGONAL:
            out |= 1 << GP_DPAD_UP
        if y / radius < -_DIAGONAL:
            out |= 1 << GP_DPAD_DOWN
        if x / radius > _DIAGONAL:
            out |= 1 << GP_DPAD_RIGHT
        if x / radius < -_DIAGONAL:
            out |= 1 << GP_DPAD_LEFT
        return out
    if role == TRACKPAD_FACE:
        if abs(x) > abs(y):
            return 1 << (GP_B if x > 0.0 else GP_X)
        return 1 << (GP_Y if y > 0.0 else GP_A)
    return 0


def _hat(up: bool, down: bool, left: bool, right: bool) -> int:
    horizontal = (1 if right else 0) - (1 if left else 0)
    vertical = (1 if up else 0) - (1 if down else 0)
    return {
        (0, 1): 0,
        (1, 1): 1,
        (1, 0): 2,
        (1, -1): 3,
        (0, -1): 4,
        (-1, -1): 5,
        (-1, 0): 6,
        (-1, 1): 7,
    }.get((horizontal, vertical), HAT_CENTRED)


def _round_axis(scaled: float) -> int:
    return int(scaled + 0.5) if scaled >= 0.0 else int(scaled - 0.5)


def build_report(
    left: HandInput,
    right: HandInput,
    config: HidConfig = DEFAULT_CONFIG,
    history: HidHistory | None = None,
) -> bytes:
    """Map two hand states to the firmware's 9-byte report."""

    hands = (left, right)
    bits = 0
    sticks = [[0.0, 0.0], [0.0, 0.0]]
    triggers = [0.0, 0.0]

    for index, hand in enumerate(hands):
        if hand is None or not hand.connected:
            continue
        is_left = index == 0
        if hand.buttons & BTN_SQUEEZE:
            bits |= 1 << (GP_L1 if is_left else GP_R1)
        if hand.buttons & BTN_THUMBSTICK_CLICK:
            bits |= 1 << (GP_L3 if is_left else GP_R3)
        if hand.buttons & BTN_MENU:
            bits |= 1 << (GP_SELECT if is_left else GP_START)
        if hand.buttons & BTN_HOME:
            bits |= 1 << GP_GUIDE

        trigger = _trigger_value(hand.trigger, config)
        triggers[index] = trigger
        was_pressed = (
            (history.l2 if is_left else history.r2) if history is not None else False
        )
        if _trigger_pressed(trigger, config, was_pressed):
            bits |= 1 << (GP_L2 if is_left else GP_R2)

        stick_y = -hand.stick[1] if config.invert_stick_y else hand.stick[1]
        sticks[index][0] = clamp(hand.stick[0], -1.0, 1.0)
        sticks[index][1] = clamp(stick_y, -1.0, 1.0)

        if hand.buttons & BTN_TRACKPAD_CLICK and hand.buttons & BTN_TRACKPAD_TOUCH:
            trackpad_y = (
                -hand.trackpad[1] if config.invert_trackpad_y else hand.trackpad[1]
            )
            bits |= _trackpad_buttons(
                hand.trackpad[0],
                trackpad_y,
                config.left_trackpad if is_left else config.right_trackpad,
                config.trackpad_centre_radius,
            )

    buttons = bits & GP_MASK_REPORT
    hat = _hat(
        bool(bits & (1 << GP_DPAD_UP)),
        bool(bits & (1 << GP_DPAD_DOWN)),
        bool(bits & (1 << GP_DPAD_LEFT)),
        bool(bits & (1 << GP_DPAD_RIGHT)),
    )

    # The model has y positive up and HID has Y positive down.
    axes = (sticks[0][0], -sticks[0][1], sticks[1][0], -sticks[1][1])
    encoded = [_round_axis(clamp(axis, -1.0, 1.0) * 127.0) for axis in axes]
    brake = int(triggers[0] * 255.0 + 0.5)
    accelerator = int(triggers[1] * 255.0 + 0.5)

    if history is not None:
        history.l2 = bool(bits & (1 << GP_L2))
        history.r2 = bool(bits & (1 << GP_R2))

    return bytes(
        (
            buttons & 0xFF,
            (buttons >> 8) & 0xFF,
            hat & 0x0F,
            encoded[0] & 0xFF,
            encoded[1] & 0xFF,
            encoded[2] & 0xFF,
            encoded[3] & 0xFF,
            brake & 0xFF,
            accelerator & 0xFF,
        )
    )
