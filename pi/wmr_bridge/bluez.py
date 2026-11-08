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

import asyncio
import logging
import os
import re
import select
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

try:  # pragma: no cover - pty is POSIX only; the Pi bridge is Linux-only
    import pty
except ImportError:  # pragma: no cover
    pty = None  # type: ignore[assignment]

HIDRAW_ROOT = "/sys/class/hidraw"
WMR_NAMES = {
    "motion controller - left": 0,
    "motion controller - right": 1,
}
HAND_NAMES = {0: "LEFT", 1: "RIGHT"}

_HIDRAW_RE = re.compile(r"^hidraw\d+$")
_ADDRESS_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")
_DEVICE_RE = re.compile(
    r"^(?:\[(?:NEW|CHG)\]\s+)?Device\s+([0-9A-Fa-f:]{17})(?:\s+(.*))?$"
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

LOG = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class BluezDevice:
    """One device known to BlueZ, with the flags ``bluetoothctl info`` reports."""

    address: str
    name: str = ""
    paired: bool = False
    trusted: bool = False
    connected: bool = False

    @property
    def is_wmr(self) -> bool:
        return self.name.strip().casefold() in WMR_NAMES

    @property
    def hand(self) -> int | None:
        return WMR_NAMES.get(self.name.strip().casefold())


def _clean(text: str) -> str:
    return _ANSI_RE.sub("", text)


def parse_devices(output: str) -> list[BluezDevice]:
    """Parse device lines from ``bluetoothctl devices`` or ``scan on`` output.

    ``scan on`` reports ``[NEW] Device <MAC> <name>`` and
    ``[CHG] Device <MAC> Name: <name>``, while ``devices`` prints
    ``Device <MAC> <name>``. Other ``[CHG]`` properties (RSSI, UUIDs) are
    ignored; duplicate addresses keep the last non-empty name.
    """

    devices: dict[str, BluezDevice] = {}
    for line in _clean(output).splitlines():
        line = line.strip()
        match = _DEVICE_RE.match(line)
        if not match:
            continue
        address = match.group(1).upper()
        rest = (match.group(2) or "").strip()
        if line.startswith("[CHG]"):
            key, sep, value = rest.partition(":")
            if not sep or key.strip() != "Name":
                continue
            rest = value.strip()
        previous = devices.get(address)
        if previous is not None and not rest:
            rest = previous.name
        devices[address] = BluezDevice(address=address, name=rest)
    return list(devices.values())


def parse_info(output: str, address: str = "") -> BluezDevice | None:
    """Parse ``bluetoothctl info <address>`` output into a :class:`BluezDevice`."""

    name = ""
    flags = {"Paired": False, "Trusted": False, "Connected": False}
    for line in _clean(output).splitlines():
        line = line.strip()
        if line.startswith("Device "):
            fields = line.split()
            if len(fields) > 1 and _ADDRESS_RE.match(fields[1]):
                address = fields[1].upper()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "Name":
            name = value
        elif key in flags:
            flags[key] = value.casefold() == "yes"
    if not address:
        return None
    return BluezDevice(
        address=address.upper(),
        name=name,
        paired=flags["Paired"],
        trusted=flags["Trusted"],
        connected=flags["Connected"],
    )


Runner = Callable[[list[str], float], "subprocess.CompletedProcess[str] | None"]
Session = Callable[[float, Callable[[BluezDevice], bool]], "list[BluezDevice] | None"]


def _run_command(args: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


_FAILURE_MARKERS = (
    "No default controller available",
    "Failed to start discovery",
    "Failed to set power",
)


def scan_and_pair(
    command: str,
    scan_seconds: float,
    should_pair: Callable[[BluezDevice], bool],
    *,
    settle_seconds: float = 1.0,
    pair_timeout: float = 20.0,
) -> list[BluezDevice] | None:
    """Discover controllers and pair matches inside one ``bluetoothctl`` session.

    A separate ``bluetoothctl --timeout N scan on`` exits before a follow-up
    process can pair, and bluetoothd removes temporary unbonded devices when
    discovery stops. Keeping one interactive client alive from ``scan on``
    through ``pair`` preserves them. A pty keeps ``bluetoothctl``'s output
    line-buffered so the ``[NEW]``/``[CHG]`` lines arrive while it scans.

    Returns ``None`` when the client cannot be started or the scan output
    reports a failure (no adapter, discovery refused, power failure). A client
    that is still running after the pair timeout is killed; the devices found
    up to that point are returned so the caller can re-read the bond state.
    """

    if pty is None:
        return None
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError:
        return None
    try:
        proc = subprocess.Popen(
            [command],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
    except (OSError, ValueError):
        os.close(master_fd)
        os.close(slave_fd)
        return None
    os.close(slave_fd)

    chunks: list[bytes] = []

    def drain(seconds: float) -> None:
        end = time.monotonic() + max(0.0, seconds)
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            ready, _, _ = select.select([master_fd], [], [], remaining)
            if not ready:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            chunks.append(chunk)

    def output() -> str:
        return b"".join(chunks).decode("utf-8", errors="replace")

    try:
        os.write(master_fd, b"scan on\n")
        drain(scan_seconds)
        discovered = parse_devices(output())
        to_pair = [device for device in discovered if should_pair(device)]
        for device in to_pair:
            os.write(master_fd, f"pair {device.address}\n".encode())
        drain(settle_seconds)
        os.write(master_fd, b"scan off\nquit\n")
        deadline = time.monotonic() + max(5.0, pair_timeout * len(to_pair))
        while proc.poll() is None and time.monotonic() < deadline:
            drain(0.1)
        if proc.poll() is None:
            # Pairing may still be settling. Keep what was discovered and let
            # _ensure() re-read the bond state on the next pass.
            proc.kill()
            proc.wait()
        drain(0.1)
    except (BrokenPipeError, OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    text = output()
    if any(marker in text for marker in _FAILURE_MARKERS):
        return None
    return parse_devices(text)


class BluezAutoPairer:
    """Pair, trust and connect WMR controllers through ``bluetoothctl``.

    BlueZ keeps the bonds; this class only drives the command-line client so the
    Pi package stays dependency-free. Connected hands are left alone. A hand
    that already has a ``Paired: yes`` and ``Trusted: yes`` bond is also left
    alone: BlueZ accepts the controller's incoming HID connection when it is
    switched on, so an offline controller is never woken with an inquiry burst
    or an outgoing ``connect``.

    Only hands with no usable bond trigger discovery. One interactive
    ``bluetoothctl`` session runs ``scan on`` and issues ``pair`` before it
    exits, because bluetoothd drops temporary devices once discovery stops.
    Matching controllers are then trusted and connected. Bursts that find
    nothing to do back off up to ``max_poll_interval``.

    Without ``bluetoothctl`` or a working adapter the pairer logs one warning
    and disables itself. ``HidrawManager`` keeps running in that case, so a
    manually paired controller still works.
    """

    def __init__(
        self,
        bridge: object,
        *,
        allowed_hands: Iterable[int] = (0, 1),
        command: str = "bluetoothctl",
        scan_seconds: float = 8.0,
        poll_interval: float = 5.0,
        max_poll_interval: float = 60.0,
        runner: Runner | None = None,
        session: Session | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.bridge = bridge
        self.allowed_hands = frozenset(allowed_hands)
        self.command = command
        self.scan_seconds = max(1.0, scan_seconds)
        self.poll_interval = max(1.0, poll_interval)
        self.max_poll_interval = max(self.poll_interval, max_poll_interval)
        self._runner = runner or _run_command
        self._session = session
        self._which = which
        self._task: asyncio.Task[None] | None = None
        self._disabled = False
        self._adapter_checked = False
        self._adapter_ready = False

    def hands_ready(self) -> bool:
        """True when every allowed hand already has an open hidraw node."""

        hands = getattr(self.bridge, "hands")
        return all(hands[hand].connected for hand in self.allowed_hands)

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="bluez-auto-pair")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        idle = 0
        while True:
            try:
                acted = await asyncio.to_thread(self._tick)
            except Exception:  # keep the daemon alive; log the reason
                LOG.exception("automatic pairing iteration failed")
                acted = False
            idle = 0 if acted else idle + 1
            await asyncio.sleep(self._next_delay(idle))

    def _next_delay(self, idle: int) -> float:
        """Back off repeated inquiry bursts that found nothing to do."""

        if idle <= 0:
            return self.poll_interval
        return min(self.max_poll_interval, self.poll_interval * (2 ** (idle - 1)))

    def _run(self, *args: str, timeout: float) -> subprocess.CompletedProcess[str] | None:
        return self._runner([self.command, *args], timeout)

    def _tick(self) -> bool:
        """Ensure the adapter and service one missing hand; report any action."""

        if self._disabled:
            return False
        if self._which(self.command) is None:
            LOG.warning(
                "bluetoothctl is not installed; automatic controller pairing is off "
                "(install bluez or pass --no-auto-pair to silence this)"
            )
            self._disabled = True
            return False
        if not self._ensure_adapter():
            return False
        if self.hands_ready():
            return True

        missing = [
            hand for hand in sorted(self.allowed_hands) if not self._hand_connected(hand)
        ]
        acted = False
        scan_hands = set(missing)
        for device in self._devices():
            hand = device.hand
            if not device.is_wmr or hand not in scan_hands:
                continue
            info = self._info(device.address) or device
            name = info.name or device.name
            if info.paired and info.trusted:
                # Already bonded: let BlueZ accept the controller's page-in.
                scan_hands.discard(hand)
                continue
            if info.paired:
                LOG.info("auto-pair: trusting %s (%s)", device.address, name)
                acted = self._command("trust", device.address) or acted
                scan_hands.discard(hand)
                continue
            acted = self._ensure(device) or acted
            scan_hands.discard(hand)

        if not scan_hands:
            return acted
        discovered = self._scan_and_pair(scan_hands)
        if discovered is None:
            LOG.warning(
                "auto-pair: bluetoothctl could not scan; automatic controller "
                "pairing is off until restart (check 'rfkill list bluetooth' and "
                "'systemctl status bluetooth', or pass --no-auto-pair to silence this)"
            )
            self._disabled = True
            return acted
        for device in discovered:
            if device.is_wmr and device.hand in scan_hands:
                acted = self._ensure(device) or acted
        return acted

    def _hand_connected(self, hand: int) -> bool:
        hands = getattr(self.bridge, "hands")
        try:
            return bool(hands[hand].connected)
        except (IndexError, KeyError, TypeError):
            return False

    def _ensure_adapter(self) -> bool:
        """Power the adapter on once; disable the pairer if there is none."""

        if self._adapter_checked:
            return self._adapter_ready
        self._adapter_checked = True
        result = self._run("power", "on", timeout=10.0)
        output = ""
        if result is not None:
            output = f"{result.stdout or ''}\n{result.stderr or ''}"
        if (
            result is None
            or result.returncode != 0
            or "No default controller available" in output
            or "Failed to set power" in output
        ):
            LOG.warning(
                "auto-pair: no working Bluetooth adapter; automatic controller "
                "pairing is off (check 'rfkill list bluetooth' and "
                "'systemctl status bluetooth', or pass --no-auto-pair to silence this)"
            )
            self._disabled = True
            self._adapter_ready = False
            return False
        self._adapter_ready = True
        return True

    def _devices(self) -> list[BluezDevice]:
        result = self._run("devices", timeout=10.0)
        if result is None or result.returncode != 0:
            return []
        return parse_devices(result.stdout or "")

    def _scan_and_pair(self, hands: Iterable[int]) -> list[BluezDevice] | None:
        wanted = frozenset(hands)

        def should_pair(device: BluezDevice) -> bool:
            return bool(device.is_wmr and device.hand in wanted)

        if self._session is not None:
            return self._session(self.scan_seconds, should_pair)
        return scan_and_pair(self.command, self.scan_seconds, should_pair)

    def _info(self, address: str) -> BluezDevice | None:
        result = self._run("info", address, timeout=10.0)
        if result is None or result.returncode != 0:
            return None
        return parse_info(result.stdout or "", address)

    def _command(self, action: str, address: str) -> bool:
        result = self._run(action, address, timeout=20.0)
        if result is None or result.returncode != 0:
            LOG.debug("bluetoothctl %s %s failed", action, address)
            return False
        return True

    def _ensure(self, device: BluezDevice) -> bool:
        info = self._info(device.address) or device
        name = info.name or device.name
        bonded = info.paired and info.trusted
        acted = False
        if not info.paired:
            LOG.info("auto-pair: pairing %s (%s)", device.address, name)
            if not self._command("pair", device.address):
                return acted
            acted = True
        if not info.trusted:
            LOG.info("auto-pair: trusting %s (%s)", device.address, name)
            acted = self._command("trust", device.address) or acted
        if not info.connected and not bonded:
            LOG.info("auto-pair: connecting %s (%s)", device.address, name)
            acted = self._command("connect", device.address) or acted
        return acted
