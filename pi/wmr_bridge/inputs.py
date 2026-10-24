"""Frame sources: a live BlueZ/hidraw host, or a capture replay for bench tests."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from pathlib import Path

from .bluez import (
    HidrawDevice,
    HidrawReader,
    discover_hidraw,
    hand_from_override,
    normalize_report,
)
from .bridge import Bridge

LOG = logging.getLogger(__name__)


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
        explicit_paths: Iterable[str] = (),
        include_all: bool = False,
        poll_interval: float = 1.0,
    ) -> None:
        self.bridge = bridge
        self.left_spec = left_spec
        self.right_spec = right_spec
        self.explicit_paths = list(explicit_paths)
        self.include_all = include_all
        self.poll_interval = max(0.2, poll_interval)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._readers: dict[str, HidrawReader] = {}
        self._hand_of: dict[str, int] = {}
        self._path_of_hand: dict[int, str] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        for path in self.explicit_paths:
            self._open(path)
        self._task = asyncio.create_task(self._monitor(), name="hidraw-monitor")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
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
                LOG.warning("%s (%s) is not a recognised WMR controller", path, device.name)
            else:
                LOG.warning(
                    "%s has no WMR name; pass --left/--right to assign it", path
                )
            return False
        other = self._path_of_hand.get(hand)
        if other is not None and other != path:
            LOG.warning(
                "ignoring %s: %s is already assigned to %s", path, other, "LEFT" if hand == 0 else "RIGHT"
            )
            return False
        reader = HidrawReader(path)
        try:
            reader.open()
        except OSError as exc:
            LOG.warning("cannot open %s: %s", path, exc)
            return False
        assert self._loop is not None
        self._loop.add_reader(reader.fd, self._on_readable, hand, reader)
        self._readers[path] = reader
        self._hand_of[path] = hand
        self._path_of_hand[hand] = path
        self.bridge.set_hand_connected(hand, True, device.uniq)
        LOG.info("opened %s as %s (%s)", path, "LEFT" if hand == 0 else "RIGHT", device.uniq or "no address")
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
                self.bridge.set_hand_connected(hand, False)
        reader.close()
        LOG.info("closed %s", path)

    def _on_readable(self, hand: int, reader: HidrawReader) -> None:
        for _ in range(32):
            try:
                raw = reader.read()
            except OSError as exc:
                LOG.warning("read error on %s: %s", reader.path, exc)
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
            except Exception:  # keep the monitor alive; log the reason
                LOG.exception("hidraw monitor iteration failed")
            await asyncio.sleep(self.poll_interval)
