"""Frame sources: a live BlueZ/hidraw host, or a capture replay for bench tests."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from .bluez import (
    KEEP_ALIVE_INTERVAL,
    HidrawDevice,
    HidrawReader,
    discover_hidraw,
    disconnect_device,
    hand_from_override,
    normalize_report,
)
from .bridge import Bridge, report_is_active

LOG = logging.getLogger(__name__)

IDLE_SLEEP_TIMEOUT = 300.0
IDLE_POWEROFF_TIMEOUT = 900.0


def read_capture(path: str | os.PathLike[str]) -> list[tuple[str, bytes]]:
    """Read a ``label hex bytes...`` capture, as used by ``samples/``."""

    entries: list[tuple[str, bytes]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 2:
                raise ValueError(f"{path}:{line_number}: expected '<label> <hex bytes>'")
            label = fields[0]
            try:
                data = bytes(int(token, 16) for token in fields[1:])
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid hex byte") from exc
            frame = normalize_report(data)
            if frame is None:
                raise ValueError(f"{path}:{line_number}: not a 44/45/46-byte WMR report")
            entries.append((label, frame))
    if not entries:
        raise ValueError(f"{path}: no frames")
    return entries


class ReplaySource:
    """Feed captured frames as if they had just arrived from controllers."""

    def __init__(
        self,
        entries: Iterable[tuple[str, bytes]],
        *,
        rate: float = 160.0,
        hand: str = "auto",
        loop: bool = True,
    ) -> None:
        self.entries = list(entries)
        self.rate = max(1.0, rate)
        self.hand_mode = hand
        self.loop = loop
        self._task: asyncio.Task[None] | None = None

    def _hands(self) -> list[int]:
        if self.hand_mode == "left":
            return [0] * len(self.entries)
        if self.hand_mode == "right":
            return [1] * len(self.entries)
        if self.hand_mode == "alternate":
            return [index % 2 for index in range(len(self.entries))]
        # auto: honour LEFT/RIGHT in the label, otherwise alternate sessions.
        session_hand: dict[str, int] = {}
        result: list[int] = []
        for label, _data in self.entries:
            folded = label.casefold()
            if "left" in folded:
                result.append(0)
                continue
            if "right" in folded:
                result.append(1)
                continue
            session = label.split("-", 1)[0].split("_", 1)[0]
            if session not in session_hand:
                session_hand[session] = len(session_hand) % 2
            result.append(session_hand[session])
        return result

    async def run(self, bridge: Bridge) -> None:
        hands = self._hands()
        loop = asyncio.get_running_loop()
        start = loop.time()
        index = 0
        while True:
            for (label, data), hand in zip(self.entries, hands):
                deadline = start + index / self.rate
                delay = deadline - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                bridge.set_hand_connected(hand, True, f"replay:{label}")
                bridge.ingest(hand, data)
                index += 1
            if not self.loop:
                return

    def start(self, bridge: Bridge) -> None:
        self._task = asyncio.create_task(self.run(bridge), name="replay")

    async def wait(self) -> None:
        """Wait for the replay task to finish (only with ``loop=False``)."""
        if self._task is not None:
            await self._task

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


class HidrawManager:
    """Watch BlueZ hidraw nodes and pump reports into the bridge."""

    def __init__(
        self,
        bridge: Bridge,
        *,
        left_spec: str | None = None,
        right_spec: str | None = None,
        allowed_hands: Iterable[int] = (0, 1),
        explicit_paths: Iterable[str] = (),
        include_all: bool = False,
        poll_interval: float = 1.0,
        keep_alive_interval: float = KEEP_ALIVE_INTERVAL,
        idle_sleep_timeout: float = IDLE_SLEEP_TIMEOUT,
        idle_poweroff_timeout: float = IDLE_POWEROFF_TIMEOUT,
        poweroff: Callable[[str], bool] | None = None,
        disconnect_command: str = "bluetoothctl",
    ) -> None:
        self.bridge = bridge
        self.left_spec = left_spec
        self.right_spec = right_spec
        self.allowed_hands = frozenset(allowed_hands)
        self.explicit_paths = list(explicit_paths)
        self.include_all = include_all
        self.poll_interval = max(0.2, poll_interval)
        self.keep_alive_interval = max(1.0, keep_alive_interval)
        self.idle_sleep_timeout = max(0.0, idle_sleep_timeout)
        self.idle_poweroff_timeout = max(0.0, idle_poweroff_timeout)
        self.disconnect_command = disconnect_command
        self._poweroff = poweroff
        self._loop: asyncio.AbstractEventLoop | None = None
        self._readers: dict[str, HidrawReader] = {}
        self._hand_of: dict[str, int] = {}
        self._path_of_hand: dict[int, str] = {}
        self._address_of_hand: dict[int, str] = {}
        self._asleep: set[int] = set()
        self._powered_off: set[int] = set()
        self._task: asyncio.Task[None] | None = None
        self._keep_alive_task: asyncio.Task[None] | None = None
        self._no_controllers_warned = False

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for path in self.explicit_paths:
            self._open(path)
        self._task = asyncio.create_task(self._monitor(), name="hidraw-monitor")
        self._keep_alive_task = asyncio.create_task(
            self._keep_alive_loop(), name="hidraw-keep-alive"
        )

    async def close(self) -> None:
        for task in (self._task, self._keep_alive_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._task = None
        self._keep_alive_task = None
        for path in list(self._readers):
            self._close(path)

    def _device_for(self, path: str) -> HidrawDevice:
        for device in discover_hidraw(include_all=True):
            if device.path == path:
                return device
        return HidrawDevice(path=path, name="", uniq="")

    def _open(self, path: str) -> bool:
        if path in self._readers:
            return True
        device = self._device_for(path)
        try:
            hand = hand_from_override(device, self.left_spec, self.right_spec)
        except ValueError as exc:
            LOG.error("%s", exc)
            return False
        if hand is None:
            if device.name:
                LOG.warning(
                    "%s (%s) is not a recognised WMR controller; pass --left/--right "
                    "with its address if it is one",
                    path,
                    device.name,
                )
            else:
                LOG.warning(
                    "%s has no WMR name; pass --left/--right to assign it", path
                )
            return False
        if hand not in self.allowed_hands:
            return False
        other = self._path_of_hand.get(hand)
        if other is not None and other != path:
            LOG.warning(
                "ignoring %s: %s is already assigned to %s", path, other, "LEFT" if hand == 0 else "RIGHT"
            )
            return False
        reader = HidrawReader(
            path, keep_alive_interval=self.keep_alive_interval
        )
        try:
            reader.open()
        except OSError as exc:
            LOG.warning("cannot open %s as %s: %s", path, "LEFT" if hand == 0 else "RIGHT", exc)
            return False
        assert self._loop is not None
        self._loop.add_reader(reader.fd, self._on_readable, hand, reader)
        self._readers[path] = reader
        self._hand_of[path] = hand
        self._path_of_hand[hand] = path
        self._address_of_hand[hand] = device.uniq
        self._asleep.discard(hand)
        self._powered_off.discard(hand)
        self.bridge.set_hand_connected(hand, True, device.uniq)
        self._no_controllers_warned = False
        LOG.info(
            "%s connected: %s (%s)",
            "LEFT" if hand == 0 else "RIGHT",
            path,
            device.uniq or "no address",
        )
        return True

    def _close(self, path: str) -> None:
        reader = self._readers.pop(path, None)
        if reader is None:
            return
        hand = self._hand_of.pop(path, None)
        if hand is not None:
            assert self._loop is not None
            try:
                self._loop.remove_reader(reader.fd)
            except (OSError, ValueError):
                pass
            if self._path_of_hand.get(hand) == path:
                del self._path_of_hand[hand]
                self._address_of_hand.pop(hand, None)
                self._asleep.discard(hand)
                self._powered_off.discard(hand)
                self.bridge.set_hand_connected(hand, False)
        reader.close()
        if hand is None:
            LOG.info("closed %s", path)
        else:
            LOG.info(
                "%s disconnected: %s after %d report(s)",
                "LEFT" if hand == 0 else "RIGHT",
                path,
                self.bridge.hands[hand].reports,
            )

    def _on_readable(self, hand: int, reader: HidrawReader) -> None:
        for _ in range(32):
            try:
                raw = reader.read()
            except OSError as exc:
                LOG.warning(
                    "read error on %s (%s): %s; check the controller and Bluetooth pairing",
                    reader.path,
                    "LEFT" if hand == 0 else "RIGHT",
                    exc,
                )
                self._close(reader.path)
                return
            if raw is None:
                return
            if not raw:
                self._close(reader.path)
                return
            frame = normalize_report(raw)
            if frame is None:
                # Configuration responses and other report IDs are not part of
                # the stream; count them separately from malformed input.
                self.bridge.hands[hand].errors += 1
                continue
            self.bridge.ingest(hand, frame)
            if report_is_active(frame):
                self._note_activity(hand, reader)

    def _note_activity(self, hand: int, reader: HidrawReader) -> None:
        """Resume streaming immediately when an idle controller moves."""

        if hand not in self._asleep and hand not in self._powered_off:
            return
        self._asleep.discard(hand)
        self._powered_off.discard(hand)
        LOG.info(
            "%s activity: resuming 0x06 keep-alives",
            "LEFT" if hand == 0 else "RIGHT",
        )
        reader.send_enable()

    def _now_us(self) -> int:
        return time.monotonic_ns() // 1000

    async def _idle_tick(self, now_us: int) -> None:
        """Apply the two-stage idle policy to every open controller."""

        for path, reader in list(self._readers.items()):
            hand = self._hand_of.get(path)
            if hand is None:
                continue
            stats = self.bridge.hands[hand]
            last_activity = stats.last_activity_us or now_us
            idle_s = max(0, now_us - last_activity) / 1_000_000.0
            label = "LEFT" if hand == 0 else "RIGHT"

            if (
                self.idle_poweroff_timeout > 0
                and idle_s >= self.idle_poweroff_timeout
            ):
                if hand in self._powered_off:
                    continue
                self._asleep.add(hand)
                self._powered_off.add(hand)
                address = self._address_of_hand.get(hand, "")
                if not address:
                    LOG.warning(
                        "%s idle for %.0f s but has no Bluetooth address; "
                        "cannot power it off",
                        label,
                        idle_s,
                    )
                    continue
                LOG.info(
                    "%s idle for %.0f s: disconnecting %s to power it off",
                    label,
                    idle_s,
                    address,
                )
                if self._poweroff is not None:
                    await asyncio.to_thread(self._poweroff, address)
                else:
                    await asyncio.to_thread(
                        disconnect_device, address, self.disconnect_command
                    )
                continue

            if (
                self.idle_sleep_timeout > 0
                and idle_s >= self.idle_sleep_timeout
            ):
                if hand not in self._asleep:
                    self._asleep.add(hand)
                    LOG.info(
                        "%s idle for %.0f s: pausing 0x06 keep-alives",
                        label,
                        idle_s,
                    )
                continue

            if hand in self._asleep:
                self._asleep.discard(hand)
                LOG.info("%s activity: resuming 0x06 keep-alives", label)
                reader.send_enable()
            reader.keep_alive()

    async def _keep_alive_loop(self) -> None:
        """Re-send the streaming-enable reports while a controller is active."""

        while True:
            # Tick faster than the configured interval and let each reader apply
            # its own clock, so a node opened mid-interval is not kept waiting
            # for up to two whole intervals before its first keep-alive.
            await asyncio.sleep(min(self.keep_alive_interval, 1.0))
            await self._idle_tick(self._now_us())

    async def _monitor(self) -> None:
        while True:
            try:
                devices = discover_hidraw(include_all=self.include_all)
                present = {device.path for device in devices}
                present.update(self.explicit_paths)
                for path in list(self._readers):
                    if path not in present:
                        self._close(path)
                for device in devices:
                    self._open(device.path)
                for path in self.explicit_paths:
                    self._open(path)
                if not self._readers and not self._no_controllers_warned:
                    LOG.warning(
                        "no WMR controllers found; put one in pairing mode and keep it "
                        "near the Pi, then check `python3 -m wmr_bridge --list`"
                    )
                    self._no_controllers_warned = True
            except Exception:  # keep the monitor alive; log the reason
                LOG.exception("hidraw monitor iteration failed")
            await asyncio.sleep(self.poll_interval)
