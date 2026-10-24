"""BlueZ / Linux hidraw glue for the host bridge.

The Pi acts as the Bluetooth Classic HID **host**. BlueZ's input plugin
(``bluetoothd`` with the ``input`` plugin enabled, plus the kernel ``hidp``
module) owns the HIDP L2CAP channels and exposes each controller as a
``/dev/hidrawN`` node. Speaking HIDP ourselves would collide with the kernel's
L2CAP server on PSM 0x11/0x13, so the supported route is:

    controller --Classic BT--> bluetoothd/hidp --> /dev/hidrawN --> this module

Linux hidraw input reports are delivered as ``report_id`` followed by the
report payload. A WMR report is report ID ``0x01`` plus 44 payload bytes, while
the firmware's 46-byte frame is ``0xA1`` (HID DATA/input transaction header),
``0x01`` and the 44 payload bytes. :func:`normalize_report` accepts either shape
and always returns the 46-byte frame so the shared decoder can be used.

This module has no third-party dependency and no import-time hardware access.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

HIDRAW_ROOT = "/sys/class/hidraw"
WMR_NAMES = {
    "motion controller - left": 0,
    "motion controller - right": 1,
}
HAND_NAMES = {0: "LEFT", 1: "RIGHT"}

_HIDRAW_RE = re.compile(r"^hidraw\d+$")


@dataclass(frozen=True)
class HidrawDevice:
    path: str
    name: str
    uniq: str = ""
    phys: str = ""

    @property
    def is_wmr(self) -> bool:
        return self.name.strip().casefold() in WMR_NAMES

    @property
    def hand(self) -> int | None:
        return WMR_NAMES.get(self.name.strip().casefold())

    def matches(self, spec: str) -> bool:
        """True if ``spec`` names this device (address, path, or name part)."""

        spec = spec.strip().casefold()
        if not spec:
            return False
        return (
            spec == self.uniq.casefold()
            or spec == os.path.basename(self.path).casefold()
            or spec == self.path.casefold()
            or spec in self.name.casefold()
        )


def _read_uevent(sysfs_root: str, node: str) -> dict[str, str]:
    path = os.path.join(sysfs_root, node, "device", "uevent")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            values[key] = value
    return values


def discover_hidraw(sysfs_root: str = HIDRAW_ROOT, include_all: bool = False) -> list[HidrawDevice]:
    """List hidraw nodes, newest order by node name.

    With ``include_all`` false (the default) only nodes whose HID name matches a
    WMR controller are returned.
    """

    try:
        nodes = sorted(
            os.listdir(sysfs_root),
            key=lambda name: (0, int(name[6:])) if _HIDRAW_RE.match(name) else (1, 0),
        )
    except OSError:
        return []
    devices: list[HidrawDevice] = []
    for node in nodes:
        if not _HIDRAW_RE.match(node):
            continue
        values = _read_uevent(sysfs_root, node)
        device = HidrawDevice(
            path=os.path.join("/dev", node),
            name=values.get("HID_NAME", ""),
            uniq=values.get("HID_UNIQ", ""),
            phys=values.get("HID_PHYS", ""),
        )
        if include_all or device.is_wmr:
            devices.append(device)
    return devices


def normalize_report(raw: bytes) -> bytes | None:
    """Return the firmware's 46-byte frame for a hidraw input report.

    Accepted shapes:

    * 46 bytes starting ``A1 01`` - already a firmware frame (replay, tests)
    * 45 bytes starting ``01``    - Linux hidraw form (report ID + payload)
    * 44 bytes                    - payload only, defensively accepted

    Other report IDs (for example the controller's configuration responses) are
    ignored by returning ``None``.
    """

    if len(raw) == 46 and raw[0] == 0xA1 and raw[1] == 0x01:
        return bytes(raw)
    if len(raw) == 45 and raw[0] == 0x01:
        return b"\xA1" + bytes(raw)
    if len(raw) == 44:
        return b"\xA1\x01" + bytes(raw)
    return None


def hand_from_name(name: str) -> int | None:
    return WMR_NAMES.get(name.strip().casefold())


def hand_from_override(device: HidrawDevice, left_spec: str | None, right_spec: str | None) -> int | None:
    """Resolve a hand from explicit ``--left`` / ``--right`` selectors first."""

    left = bool(left_spec and device.matches(left_spec))
    right = bool(right_spec and device.matches(right_spec))
    if left and right:
        raise ValueError(f"{device.path} matches both --left and --right")
    if left:
        return 0
    if right:
        return 1
    return device.hand


class HidrawReader:
    """Blocking/non-blocking reader for one hidraw node."""

    def __init__(self, path: str, read_size: int = 128) -> None:
        self.path = path
        self.read_size = read_size
        self._fd: int | None = None

    def open(self) -> int:
        if self._fd is None:
            self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        return self._fd

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise RuntimeError("reader is not open")
        return self._fd

    def fileno(self) -> int:
        return self.fd

    def read(self) -> bytes | None:
        """Read one report; ``None`` when no report is pending.

        A real read error (for example ``ENODEV`` after the controller
        disconnected) is raised so the caller can close the node.
        """

        try:
            return os.read(self.fd, self.read_size)
        except BlockingIOError:
            return None

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None

    def __enter__(self) -> "HidrawReader":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
