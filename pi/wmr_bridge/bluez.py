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
RFKILL_ROOT = "/sys/class/rfkill"
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


# WMR controllers stop streaming and can power down when the host only reads
# from hidraw: they expect Monado's two output-report commands that re-enable
# streaming. hidraw writes the report ID first, and the controller's output
# report is 64 bytes, so the five command bytes are zero-padded.
WMR_ENABLE_REPORTS = (
    bytes((0x06, 0x03, 0x01, 0x00, 0x02)),
    bytes((0x06, 0x03, 0x02, 0xE1, 0x02)),
)
WMR_OUTPUT_REPORT_SIZE = 64
KEEP_ALIVE_INTERVAL = 10.0
# The controller drops a back-to-back second enable command; firmware and
# Monado separate them by WMR_CFG_SPACING_MS (10 ms).
WMR_ENABLE_SPACING = 0.010


class HidrawReader:
    """Blocking/non-blocking reader for one hidraw node.

    The node is opened read-write when possible so the two 0x06 streaming-enable
    reports can be written on open and then again as a periodic keep-alive. A
    read-only fallback (for example a udev rule that grants only read access)
    still delivers input reports, but keep-alive writes are then skipped.
    """

    def __init__(
        self,
        path: str,
        read_size: int = 128,
        *,
        keep_alive_interval: float = KEEP_ALIVE_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.read_size = read_size
        self.keep_alive_interval = max(0.0, keep_alive_interval)
        self._clock = clock
        self._fd: int | None = None
        self._writable = False
        self._last_keep_alive: float | None = None

    @property
    def writable(self) -> bool:
        return self._writable

    def open(self) -> int:
        if self._fd is None:
            flags = os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            try:
                self._fd = os.open(self.path, os.O_RDWR | flags)
            except OSError as exc:
                LOG.warning(
                    "%s: cannot open read-write (%s); input works but the 0x06 "
                    "streaming keep-alive is disabled",
                    self.path,
                    exc,
                )
                self._fd = os.open(self.path, os.O_RDONLY | flags)
                self._writable = False
            else:
                self._writable = True
            self.send_enable()
        return self._fd

    def send_enable(self) -> bool:
        """Write both 0x06 streaming-enable reports; report whether they went out."""

        if self._fd is None or not self._writable:
            return False
        sent = True
        for index, command in enumerate(WMR_ENABLE_REPORTS):
            if index:
                time.sleep(WMR_ENABLE_SPACING)
            report = command.ljust(WMR_OUTPUT_REPORT_SIZE, b"\x00")
            try:
                os.write(self._fd, report)
            except OSError as exc:
                LOG.warning("%s: 0x06 enable write failed: %s", self.path, exc)
                sent = False
        if sent:
            self._last_keep_alive = self._clock()
        return sent

    def keep_alive(self, now: float | None = None) -> bool:
        """Re-send the enable reports when the keep-alive interval has elapsed."""

        if self._fd is None or not self._writable:
            return False
        current = self._clock() if now is None else now
        if (
            self._last_keep_alive is not None
            and current - self._last_keep_alive < self.keep_alive_interval
        ):
            return False
        return self.send_enable()

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
                self._writable = False
                self._last_keep_alive = None

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


@dataclass(frozen=True)
class BluezAdapter:
    """Adapter state from ``bluetoothctl show``."""

    address: str
    name: str = ""
    powered: bool = False
    pairable: bool = False
    discoverable: bool = False
    discovering: bool = False


def parse_adapter(output: str) -> BluezAdapter | None:
    """Parse ``bluetoothctl show`` into a :class:`BluezAdapter`."""

    address = ""
    name = ""
    flags = {"Powered": False, "Pairable": False, "Discoverable": False, "Discovering": False}
    for line in _clean(output).splitlines():
        line = line.strip()
        match = re.match(r"^Controller\s+([0-9A-Fa-f:]{17})", line)
        if match:
            address = match.group(1).upper()
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
    return BluezAdapter(
        address=address,
        name=name,
        powered=flags["Powered"],
        pairable=flags["Pairable"],
        discoverable=flags["Discoverable"],
        discovering=flags["Discovering"],
    )


@dataclass(frozen=True)
class RfkillSwitch:
    """One entry from ``/sys/class/rfkill``."""

    path: str
    name: str = ""
    type: str = ""
    soft: bool = False
    hard: bool = False

    @property
    def blocked(self) -> bool:
        return self.soft or self.hard


def _read_sysfs(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def read_rfkill(root: str = RFKILL_ROOT) -> list[RfkillSwitch]:
    """Read the kernel rfkill switches; an absent directory means no info."""

    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return []
    switches: list[RfkillSwitch] = []
    for entry in entries:
        if not entry.startswith("rfkill"):
            continue
        path = os.path.join(root, entry)
        switches.append(
            RfkillSwitch(
                path=path,
                name=_read_sysfs(os.path.join(path, "name")),
                type=_read_sysfs(os.path.join(path, "type")).casefold(),
                soft=_read_sysfs(os.path.join(path, "soft")) == "1",
                hard=_read_sysfs(os.path.join(path, "hard")) == "1",
            )
        )
    return switches


def bluetooth_blocked(root: str = RFKILL_ROOT) -> bool:
    """True when any rfkill switch for Bluetooth is soft- or hard-blocked."""

    return any(
        switch.blocked
        for switch in read_rfkill(root)
        if switch.type == "bluetooth"
    )


@dataclass(frozen=True)
class BluezStatus:
    """Everything ``wmr-bridge status`` reports about pairing."""

    adapter: BluezAdapter | None
    devices: tuple[BluezDevice, ...]
    hidraw: tuple[HidrawDevice, ...]


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


def read_adapter(command: str = "bluetoothctl", runner: Runner = _run_command) -> BluezAdapter | None:
    """Read adapter power/pairable/discovering state from ``bluetoothctl show``."""

    result = runner([command, "show"], 10.0)
    if result is None or result.returncode != 0:
        return None
    return parse_adapter(result.stdout or "")


def read_devices(command: str = "bluetoothctl", runner: Runner = _run_command) -> list[BluezDevice]:
    result = runner([command, "devices"], 10.0)
    if result is None or result.returncode != 0:
        return []
    return parse_devices(result.stdout or "")


def read_device_info(
    command: str, address: str, runner: Runner = _run_command
) -> BluezDevice | None:
    result = runner([command, "info", address], 10.0)
    if result is None or result.returncode != 0:
        return None
    return parse_info(result.stdout or "", address)


def disconnect_device(
    address: str,
    command: str = "bluetoothctl",
    runner: Runner = _run_command,
    timeout: float = 20.0,
) -> bool:
    """Drop the Classic ACL link, which lets an idle controller power off."""

    if not address:
        return False
    result = runner([command, "disconnect", address], timeout)
    output = ""
    if result is not None:
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
    folded = output.casefold()
    if "notconnected" in folded or "not connected" in folded:
        # Blocking a device in BlueZ drops its ACL (device_block calls
        # disconnect_all), so the explicit disconnect can lose that race. A
        # link that is already down is the state we wanted.
        LOG.info("bluetoothctl %s already disconnected", address)
        return True
    if result is None or result.returncode != 0 or "failed" in folded:
        LOG.warning(
            "bluetoothctl disconnect %s failed; the controller may stay connected",
            address,
        )
        return False
    LOG.info("bluetoothctl disconnected %s", address)
    return True


def set_device_blocked(
    address: str,
    blocked: bool,
    command: str = "bluetoothctl",
    runner: Runner = _run_command,
    timeout: float = 20.0,
) -> bool:
    """Tell BlueZ to refuse (``block``) or allow (``unblock``) incoming links.

    Blocking a bonded device keeps a controller that is still shutting down
    from paging the ACL back up during the idle power-off cooldown. The bond is
    kept; ``unblock`` lets a later button press reconnect normally.
    """

    if not address:
        return False
    action = "block" if blocked else "unblock"
    result = runner([command, action, address], timeout)
    output = ""
    if result is not None:
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
    if result is None or result.returncode != 0 or "failed" in output.casefold():
        LOG.warning(
            "bluetoothctl %s %s failed; the reconnect guard is not applied",
            action,
            address,
        )
        return False
    LOG.info("bluetoothctl %s %s", action, address)
    return True


def collect_status(
    command: str = "bluetoothctl",
    runner: Runner = _run_command,
    sysfs_root: str = HIDRAW_ROOT,
) -> BluezStatus:
    """Gather adapter, WMR bond and hidraw state without the daemon running."""

    nodes = discover_hidraw(sysfs_root)
    adapter = read_adapter(command, runner)
    listed = {device.address: device for device in read_devices(command, runner)}
    for node in nodes:
        if node.uniq:
            listed.setdefault(node.uniq.upper(), BluezDevice(address=node.uniq.upper()))
    devices: list[BluezDevice] = []
    for address in sorted(listed):
        info = read_device_info(command, address, runner) or listed[address]
        devices.append(info)
    return BluezStatus(adapter=adapter, devices=tuple(devices), hidraw=tuple(nodes))


_FAILURE_MARKERS = (
    "No default controller available",
    "Failed to start discovery",
    "Failed to set power",
)

_PAIR_RESULT_RE = re.compile(r"Pairing successful|Failed to pair(?::\s*(.*))?")


def pair_results(output: str) -> list[tuple[bool, str]]:
    """Return ``(succeeded, reason)`` for each ``bluetoothctl`` pair reply."""

    results: list[tuple[bool, str]] = []
    for match in _PAIR_RESULT_RE.finditer(_clean(output)):
        succeeded = match.group(0) == "Pairing successful"
        results.append((succeeded, (match.group(1) or "").strip()))
    return results


def scan_and_pair(
    command: str,
    scan_seconds: float,
    should_pair: Callable[[BluezDevice], bool],
    *,
    claim_agent: bool = True,
    settle_seconds: float = 1.0,
    pair_timeout: float = 20.0,
) -> list[BluezDevice] | None:
    """Discover controllers and pair matches inside one ``bluetoothctl`` session.

    A separate ``bluetoothctl --timeout N scan on`` exits before a follow-up
    process can pair, and bluetoothd removes temporary unbonded devices when
    discovery stops. Keeping one interactive client alive from ``scan on``
    through ``pair`` preserves them. A pty keeps ``bluetoothctl``'s output
    line-buffered so the ``[NEW]``/``[CHG]`` lines arrive while it scans. The
    session registers a ``NoInputNoOutput`` agent first: without one BlueZ
    rejects every pair attempt, which is the failure that made a real Pi never
    bond a controller. It also waits for each pair reply before quitting,
    because ``pair`` is asynchronous.

    ``claim_agent`` false is used while the BLE fallback's pairing window is
    open. The bridge's own ``NoInputNoOutput`` agent is the adapter default
    then, and bluetoothctl's agent must not replace it: bluetoothctl's startup
    agent has an empty capability, and BlueZ clears its default when the
    client exits, so a phone pairing around the scan would see the wrong IO
    capability or no agent at all. In that mode the client unregisters its own
    agent (``agent off``) and ``pair`` uses the bridge's default agent.

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

    to_pair: list[BluezDevice] = []
    try:
        if claim_agent:
            # bluetoothctl's built-in agent is registered with an empty
            # capability unless one is requested, and bluetoothd rejects that.
            # Pairing then fails with org.bluez.Error.AuthenticationFailed and
            # no bond is made. Register a NoInputNoOutput agent (Just Works)
            # before touching the controller.
            os.write(master_fd, b"agent NoInputNoOutput\n")
            # RegisterAgent is asynchronous in bluetoothctl. Wait for its reply
            # before default-agent, which would otherwise race it.
            # bluetoothctl's own startup registration with an empty capability
            # also prints "Failed to register agent", so only our success reply
            # counts here.
            agent_deadline = time.monotonic() + 2.0
            while time.monotonic() < agent_deadline:
                drain(0.05)
                if "Agent registered" in output():
                    break
            os.write(master_fd, b"default-agent\n")
        else:
            # The bridge's agent is already the adapter default. Drop the
            # client's startup agent so `pair` cannot select its empty
            # capability instead.
            os.write(master_fd, b"agent off\n")
        os.write(master_fd, b"pairable on\nscan on\n")
        drain(scan_seconds)
        discovered = parse_devices(output())
        to_pair = [device for device in discovered if should_pair(device)]
        for device in to_pair:
            LOG.info("auto-pair: pairing %s (%s)", device.address, device.name or "?")
            os.write(master_fd, f"pair {device.address}\n".encode())
        # cmd_pair is asynchronous: quitting before the reply arrives tears
        # down the agent and aborts the pairing. Wait for a result per device.
        deadline = time.monotonic() + max(5.0, pair_timeout * max(1, len(to_pair)))
        while to_pair and proc.poll() is None and time.monotonic() < deadline:
            drain(0.1)
            if len(pair_results(output())) >= len(to_pair):
                break
        drain(settle_seconds)
        os.write(master_fd, b"scan off\nquit\n")
        deadline = time.monotonic() + 5.0
        while proc.poll() is None and time.monotonic() < deadline:
            drain(0.1)
        if proc.poll() is None:
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
    results = pair_results(text)
    for index, device in enumerate(to_pair):
        if index >= len(results):
            LOG.warning("auto-pair: pairing %s gave no result (timed out)", device.address)
            continue
        succeeded, reason = results[index]
        if succeeded:
            LOG.info("auto-pair: paired %s", device.address)
        else:
            LOG.warning("auto-pair: pairing %s failed: %s", device.address, reason or "unknown error")
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

    ``on_scan_end`` runs after every session, including a failed one. The BLE
    peripheral uses it as a backstop to reclaim the default pairing agent if a
    ``bluetoothctl`` session took it.

    While one controller is already connected, an inquiry would starve its ACL
    link on the Pi's shared radio, so scans for the missing hand are throttled:
    they repeat no faster than ``active_poll_interval`` and run for at most
    ``active_scan_seconds``.

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
        scan_seconds: float = 10.0,
        poll_interval: float = 5.0,
        max_poll_interval: float = 30.0,
        active_scan_seconds: float = 2.0,
        active_poll_interval: float = 60.0,
        runner: Runner | None = None,
        session: Session | None = None,
        pairing_window_open: Callable[[], bool] | None = None,
        on_scan_end: Callable[[], None] | None = None,
        which: Callable[[str], str | None] = shutil.which,
        rfkill_root: str = RFKILL_ROOT,
    ) -> None:
        self.bridge = bridge
        self.allowed_hands = frozenset(allowed_hands)
        self.command = command
        self.scan_seconds = max(1.0, scan_seconds)
        self.poll_interval = max(1.0, poll_interval)
        self.max_poll_interval = max(self.poll_interval, max_poll_interval)
        self.active_scan_seconds = max(1.0, active_scan_seconds)
        self.active_poll_interval = max(self.max_poll_interval, active_poll_interval)
        self._runner = runner or _run_command
        self._session = session
        self._pairing_window_open = pairing_window_open
        self._on_scan_end = on_scan_end
        self._which = which
        self._rfkill_root = rfkill_root
        self._task: asyncio.Task[None] | None = None
        self._disabled = False
        self._adapter_ready = False
        self._adapter_warned = False

    def hands_ready(self) -> bool:
        """True when every allowed hand already has an open hidraw node."""

        hands = getattr(self.bridge, "hands")
        return all(hands[hand].connected for hand in self.allowed_hands)

    def _any_hand_connected(self) -> bool:
        return any(self._hand_connected(hand) for hand in self.allowed_hands)

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
        """Back off repeated inquiry bursts that found nothing to do.

        A connected hand shares the Pi's radio with any inquiry for the missing
        hand, so its scans are spaced out to ``active_poll_interval`` even
        before the normal idle backoff would reach that far.

        ``idle`` grows without bound while a hand stays missing, so the
        exponent is capped before float conversion. ``2 ** idle`` raised
        ``OverflowError`` after enough iterations and killed the daemon.
        """

        if idle <= 0:
            delay = self.poll_interval
        else:
            exponent = min(idle - 1, 32)
            delay = min(
                self.max_poll_interval, self.poll_interval * float(2 ** exponent)
            )
        if self._any_hand_connected():
            return max(delay, self.active_poll_interval)
        return delay

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
            # An unpaired device must be in pairing mode to be seen, and only
            # the interactive scan session has a pairing agent. Leave the hand
            # in scan_hands instead of trying a separate pair command.

        if not scan_hands:
            return acted
        scan_seconds = self.scan_seconds
        if self._any_hand_connected():
            scan_seconds = min(scan_seconds, self.active_scan_seconds)
        LOG.info(
            "auto-pair: scanning %.0f s for %s; put the controller in pairing mode",
            scan_seconds,
            ", ".join(HAND_NAMES[hand] for hand in sorted(scan_hands)),
        )
        LOG.info("auto-pair: Adapter1.Pairable -> on for the controller scan")
        discovered = None
        try:
            discovered = self._scan_and_pair(scan_hands, scan_seconds)
        finally:
            self._settle_pairable()
            # The scan's bluetoothctl client registered its own agent as the
            # default and BlueZ drops that default when the client exits. Let
            # the BLE peripheral reclaim its Just Works agent before a phone
            # tries to bond.
            if self._on_scan_end is not None:
                self._on_scan_end()
        if discovered is None:
            LOG.warning(
                "auto-pair: bluetoothctl could not scan; automatic controller "
                "pairing is off until restart (check 'rfkill list bluetooth' and "
                "'systemctl status bluetooth', or pass --no-auto-pair to silence this)"
            )
            self._disabled = True
            return acted
        found = [
            device for device in discovered
            if device.is_wmr and device.hand in scan_hands
        ]
        if not found:
            LOG.info("auto-pair: no WMR controller answered this scan")
        for device in found:
            LOG.info("auto-pair: found %s (%s)", device.address, device.name or "?")
            acted = self._ensure(device) or acted
        return acted

    def _settle_pairable(self) -> None:
        """Leave ``Adapter1.Pairable`` off once a scan ends.

        ``Pairable`` is a single adapter flag shared with the BLE fallback's
        bounded pairing window. The scan session opens it for Classic pairing;
        this closes it again on every exit path (including a failed scan),
        unless the BLE window is still open and wants it left on.
        """

        if self._pairing_window_open is not None and self._pairing_window_open():
            return
        result = self._run("pairable", "off", timeout=10.0)
        if result is not None and result.returncode == 0:
            LOG.info(
                "auto-pair: Adapter1.Pairable -> off after the controller scan"
            )

    def _hand_connected(self, hand: int) -> bool:
        hands = getattr(self.bridge, "hands")
        try:
            return bool(hands[hand].connected)
        except (IndexError, KeyError, TypeError):
            return False

    def _ensure_adapter(self) -> bool:
        """Power the adapter on, clearing a soft rfkill block when possible.

        A stock Pi OS image soft-blocks Bluetooth at boot. The service runs as
        an unprivileged user, so the systemd unit clears the block as root
        before start; this covers a block that appears later (or a daemon
        started by hand) and retries instead of disabling pairing for the life
        of the process. Only a missing ``bluetoothctl`` is permanent.
        """

        if self._adapter_ready:
            return True
        if bluetooth_blocked(self._rfkill_root):
            self._clear_rfkill()
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
            self._adapter_ready = False
            self._warn_adapter(bluetooth_blocked(self._rfkill_root))
            return False
        self._adapter_ready = True
        self._adapter_warned = False
        # Pairable is only opened inside the scan session (and by the BLE
        # fallback's pairing window), so a handler that never scans does not
        # leave the adapter bonding-enabled indefinitely.
        return True

    def _clear_rfkill(self) -> None:
        """Best-effort ``rfkill unblock bluetooth``; needs root in the unit."""

        if self._which("rfkill") is None:
            LOG.debug("auto-pair: rfkill is not installed; cannot clear the Bluetooth block")
            return
        result = self._runner(["rfkill", "unblock", "bluetooth"], 10.0)
        if result is not None and result.returncode == 0:
            LOG.info("auto-pair: cleared the rfkill Bluetooth block")
        else:
            LOG.debug(
                "auto-pair: 'rfkill unblock bluetooth' failed; the systemd unit "
                "clears the block as root"
            )

    def _warn_adapter(self, blocked: bool) -> None:
        if self._adapter_warned:
            LOG.debug("auto-pair: Bluetooth adapter still unavailable; retrying")
            return
        self._adapter_warned = True
        hint = (
            "the adapter is still rfkill-blocked; run 'sudo rfkill unblock bluetooth'"
            if blocked
            else "check 'systemctl status bluetooth'"
        )
        LOG.warning(
            "auto-pair: Bluetooth adapter is not usable yet (%s); retrying "
            "instead of disabling automatic pairing (pass --no-auto-pair to "
            "silence this)",
            hint,
        )

    def _devices(self) -> list[BluezDevice]:
        result = self._run("devices", timeout=10.0)
        if result is None or result.returncode != 0:
            return []
        return parse_devices(result.stdout or "")

    def _scan_and_pair(
        self, hands: Iterable[int], seconds: float | None = None
    ) -> list[BluezDevice] | None:
        wanted = frozenset(hands)

        def should_pair(device: BluezDevice) -> bool:
            return bool(device.is_wmr and device.hand in wanted)

        scan_seconds = self.scan_seconds if seconds is None else seconds
        if self._session is not None:
            return self._session(scan_seconds, should_pair)
        claim_agent = not (
            self._pairing_window_open is not None and self._pairing_window_open()
        )
        return scan_and_pair(
            self.command, scan_seconds, should_pair, claim_agent=claim_agent
        )

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
        if not info.paired:
            # Pairing only works inside scan_and_pair(), where the agent is
            # registered. Keep the controller and retry next scan.
            LOG.warning(
                "auto-pair: %s (%s) is not paired; will retry on the next scan",
                device.address,
                name,
            )
            return False
        bonded = info.trusted
        acted = False
        if not info.trusted:
            LOG.info("auto-pair: trusting %s (%s)", device.address, name)
            acted = self._command("trust", device.address) or acted
        if not info.connected and not bonded:
            LOG.info("auto-pair: connecting %s (%s)", device.address, name)
            if self._command("connect", device.address):
                acted = True
                connected = self._info(device.address)
                if connected is not None and connected.connected:
                    LOG.info("auto-pair: connected %s (%s)", device.address, name)
                else:
                    LOG.info(
                        "auto-pair: connect sent to %s; waiting for its HID node",
                        device.address,
                    )
        return acted
