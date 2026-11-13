"""The Wi-Fi bridge: one stream per client, with BLE-compatible payloads.

A client connects over WebSocket, receives a ``hello``, optionally authenticates,
then subscribes to exactly one of ``raw``, ``decoded`` or ``batched``. The
binary messages that follow are byte-for-byte the BLE notification values the
ESP32 firmware would have sent on FF11, FF12 or FF14. See ``docs/PI_BRIDGE.md``.

Sequence numbers are assigned here, once per received report and independently
of whether anybody is subscribed, matching the firmware's counter semantics.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import hmac
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from . import __version__
from .decode import ControllerState, decode_frame, record_from_state
from .protocol import (
    BATCH_MAX_COUNT,
    CALIBRATION_HEADER_SIZE,
    CALIBRATION_MAX_BLOB,
    CALIBRATION_STATE_NAMES,
    CALIBRATION_VERSION,
    CALIBRATION_WINDOW_MAX,
    DECODED_SIZE,
    DEFAULT_FRAGMENT_CAPACITY,
    CalibrationCache,
    CalibrationState,
    DecodedRecord,
    Frame,
    ProtocolError,
    encode_batch,
    encode_raw_messages,
    pack_record,
)
from . import ws as wsmod

STREAMS = ("raw", "decoded", "batched")
HAND_NAMES = ("LEFT", "RIGHT")
# Activity thresholds for the controller idle policy. Values match the plan in
# any button, a trigger past its dead zone, meaningful stick
# deflection, or motion above the stationary MEMS bias/noise floor.
ACTIVITY_TRIGGER_THRESHOLD = 0.05
ACTIVITY_STICK_THRESHOLD = 0.10
ACTIVITY_GYRO_SUM_THRESHOLD = 0.15
ACTIVITY_GYRO_AXIS_THRESHOLD = 0.08
MACHINE_ID_PATH = Path("/etc/machine-id")
# wmr-bridge.service sets StateDirectory=wmr-bridge, so systemd creates this
# directory writable by the service user even under ProtectSystem=strict.
DEFAULT_UPGRADE_DIR = Path("/var/lib/wmr-bridge")
# wmr-bridge-upgrade.service sets RuntimeDirectory=wmr-bridge-upgrade, so root
# owns this directory and the lock inside it. The state directory is writable
# by the service user, and flock follows a symlink planted at the lock name, so
# the lock must never live there.
DEFAULT_UPGRADE_LOCK = Path("/run/wmr-bridge-upgrade/upgrade.lock")
# The root oneshot removes the trigger before it runs the installer. A trigger
# still present after this long was never picked up (for example because the
# path unit stopped after a refused start), so it must not pin the bridge in
# "busy" for every later request.
UPGRADE_TRIGGER_TTL_S = 120.0
# Stay below the root unit's StartLimitBurst=3 / StartLimitIntervalSec=600:
# a refused start stops wmr-bridge-upgrade.path and nothing consumes the
# trigger again. The window carries a margin over the unit's interval, and the
# times are persisted so a daemon restart (the install restarts it) does not
# forget earlier starts.
UPGRADE_RATE_WINDOW_S = 660.0
UPGRADE_RATE_BURST = 3
# `inspect_wifi()`/`inspect_uplink()` shell out to `iw`/NetworkManager with
# subprocess timeouts, so re-sample at most this often and off the event loop.
NETWORK_REFRESH_INTERVAL_S = 10.0
LOG = logging.getLogger(__name__)


def default_bridge_id(
    hostname: str | None = None, machine_id_path: Path = MACHINE_ID_PATH
) -> str:
    """Derive a stable, human-readable identity for this bridge.

    The hostname label makes the id recognisable on a discovery list; the digest
    of ``/etc/machine-id`` keeps two identically named hosts apart and survives
    daemon restarts. When ``/etc/machine-id`` is unavailable the hostname is the
    digest input, so the id is still stable across restarts on that host.
    """

    host = hostname or socket.gethostname()
    slug = "".join(ch if ch.isalnum() else "-" for ch in host.lower())
    label = "-".join(part for part in slug.split("-") if part)[:32].strip("-")
    label = label or "wmr-bridge"
    try:
        seed = machine_id_path.read_text(encoding="utf-8").strip()
    except OSError:
        seed = ""
    digest = hashlib.sha256((seed or host).encode("utf-8")).hexdigest()[:10]
    return f"{label}-{digest}"


@dataclass
class HandStats:
    reports: int = 0
    decoded: int = 0
    errors: int = 0
    delivered: int = 0
    dropped: int = 0
    connected: bool = False
    address: str = ""
    last_rx_us: int = 0
    last_activity_us: int = 0
    idle_sleeping: bool = False
    # The last observed report is kept as the reference for the next one. A
    # controller resting off-centre (Right's stick is (-0.064, 0.148)) or with a
    # static touch bit must not look active forever; only a change from this
    # baseline counts.
    last_report_active: bool = False
    _activity_ref: ControllerState | None = field(default=None, repr=False)

    def to_json(self, sequence: int, now_us: int) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "address": self.address,
            "reports": self.reports,
            "decoded": self.decoded,
            "errors": self.errors,
            "delivered": self.delivered,
            "dropped": self.dropped,
            "sequence": sequence,
            "lastRxAgeMs": None if not self.last_rx_us else round((now_us - self.last_rx_us) / 1000.0, 1),
            "lastActivityAgeMs": None
            if not self.last_activity_us
            else round((now_us - self.last_activity_us) / 1000.0, 1),
            "idleSleep": self.idle_sleeping,
        }

    def reset_activity(self) -> None:
        """Drop the per-hand reference so the next report starts a fresh baseline."""

        self._activity_ref = None
        self.last_report_active = False

    def note_activity(self, state: ControllerState, now_us: int) -> bool:
        """Compare a report with this hand's baseline and latch activity.

        The first report after a connect/disconnect establishes the reference.
        A later report counts as activity when its buttons changed or its
        trigger, stick or gyro moved past the detector thresholds; activity
        moves the reference forward so a held button or off-centre rest pose is
        not reported active on every subsequent frame.
        """

        reference = self._activity_ref
        if reference is None:
            self._activity_ref = state
            self.last_report_active = False
            return False
        gx, gy, gz = state.gyro
        rx, ry, rz = reference.gyro
        active = (
            state.buttons != reference.buttons
            or abs(state.trigger - reference.trigger) > ACTIVITY_TRIGGER_THRESHOLD
            or abs(state.thumbstick_x - reference.thumbstick_x) > ACTIVITY_STICK_THRESHOLD
            or abs(state.thumbstick_y - reference.thumbstick_y) > ACTIVITY_STICK_THRESHOLD
            or abs(gx - rx) > ACTIVITY_GYRO_AXIS_THRESHOLD
            or abs(gy - ry) > ACTIVITY_GYRO_AXIS_THRESHOLD
            or abs(gz - rz) > ACTIVITY_GYRO_AXIS_THRESHOLD
            or abs(gx - rx) + abs(gy - ry) + abs(gz - rz) > ACTIVITY_GYRO_SUM_THRESHOLD
        )
        self.last_report_active = active
        if active:
            self._activity_ref = state
            self.last_activity_us = now_us
        return active


def _parse_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _calibration_hand(value: Any, allowed: tuple[int, ...]) -> int | None:
    """Resolve a control-message hand (``"LEFT"`` or ``0``) from the filter."""

    if isinstance(value, str):
        folded = value.strip().casefold()
        hand = next(
            (index for index, name in enumerate(HAND_NAMES) if name.casefold() == folded),
            None,
        )
    elif isinstance(value, int) and not isinstance(value, bool):
        hand = value
    else:
        return None
    return hand if hand in allowed else None


def _tokens_equal(candidate: str, expected: str) -> bool:
    """Compare tokens as UTF-8 bytes.

    ``hmac.compare_digest`` rejects non-ASCII strings with ``TypeError``; bytes
    comparison keeps the constant-time property and turns a malformed client
    token into an ordinary authentication failure.
    """

    try:
        return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))
    except UnicodeEncodeError:
        return False


class FrameSink(Protocol):
    """A non-WebSocket consumer of the ingest stream (for example BLE GATT)."""

    needs_decoded: bool

    def deliver(self, frame: Frame, record: DecodedRecord | None) -> None: ...


class Subscriber:
    """One connected client and its outbound queue."""

    def __init__(self, bridge: "Bridge", websocket: wsmod.WebSocket) -> None:
        self.bridge = bridge
        self.websocket = websocket
        self.stream: str | None = None
        self.authed = bridge.token is None
        self.dropped = 0
        self.sent = 0
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=bridge.queue_size)
        self._batch: list[tuple[int, bytes]] = []
        self._batch_timer: asyncio.TimerHandle | None = None
        self._sender = asyncio.create_task(self._send_loop())
        self.closed = False

    # ---- outbound --------------------------------------------------------

    async def send_json(self, value: dict[str, Any]) -> None:
        await self.websocket.send_json(value)

    def _enqueue(self, payload: bytes) -> bool:
        if self.closed:
            return False
        try:
            self._queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.dropped += 1
            self.bridge.stats_dropped += 1
            return False

    async def _send_loop(self) -> None:
        try:
            while True:
                payload = await self._queue.get()
                await self.websocket.send_binary(payload)
                self.sent += 1
        except (wsmod.WebSocketClosed, ConnectionError, OSError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            self.closed = True

    # ---- stream delivery -------------------------------------------------

    def deliver(self, frame: Frame, record: DecodedRecord | None = None) -> None:
        if self.closed:
            return
        if self.stream == "raw":
            for message in encode_raw_messages(frame, self.bridge.fragment_capacity):
                if self._enqueue(message):
                    self.bridge.hands[frame.hand].delivered += 1
                else:
                    self.bridge.hands[frame.hand].dropped += 1
        elif self.stream in ("decoded", "batched"):
            if record is None:
                return
            packed = pack_record(record)
            if self.stream == "decoded":
                if self._enqueue(packed):
                    self.bridge.hands[frame.hand].delivered += 1
                else:
                    self.bridge.hands[frame.hand].dropped += 1
            else:
                self._batch.append((frame.hand, packed))
                if len(self._batch) >= self.bridge.batch_max:
                    self._flush_batch()
                elif self._batch_timer is None:
                    self._batch_timer = asyncio.get_running_loop().call_later(
                        self.bridge.batch_hold_s, self._flush_batch
                    )

    def _flush_batch(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        if not self._batch:
            return
        records = self._batch
        self._batch = []
        if self.closed:
            return
        try:
            payload = encode_batch([packed for _hand, packed in records])
        except ProtocolError:
            return
        if self._enqueue(payload):
            self.bridge.stats_batches += 1
            for hand, _packed in records:
                self.bridge.hands[hand].delivered += 1
        else:
            for hand, _packed in records:
                self.bridge.hands[hand].dropped += 1

    def reset_stream(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        self._batch.clear()
        # Binary payloads already queued belong to the stream being left. The
        # control reply is sent directly (not through this queue), so leaving
        # them would deliver old-stream bytes after `unsubscribed`.
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.stream = None

    def close(self) -> None:
        self.closed = True
        self.reset_stream()
        self._sender.cancel()


class Bridge:
    """Transport-independent bridge state (no sockets, easy to unit test)."""

    def __init__(
        self,
        *,
        token: str | None = None,
        queue_size: int = 512,
        batch_max: int = BATCH_MAX_COUNT,
        batch_hold_ms: float = 5.0,
        fragment_capacity: int = DEFAULT_FRAGMENT_CAPACITY,
        auth_timeout_s: float = 5.0,
        allowed_hands: tuple[int, ...] | list[int] = (0, 1),
        calibration: bool | CalibrationCache = True,
        bridge_id: str | None = None,
        wifi: dict[str, Any] | None = None,
        uplink: dict[str, Any] | None = None,
        network: Callable[[], tuple[dict[str, Any] | None, dict[str, Any] | None]] | None = None,
        network_refresh_s: float = NETWORK_REFRESH_INTERVAL_S,
        upgrade_dir: str | os.PathLike[str] | None = None,
        upgrade_lock: str | os.PathLike[str] | None = None,
    ) -> None:
        if not 1 <= batch_max <= BATCH_MAX_COUNT:
            raise ValueError(f"batch_max must be 1..{BATCH_MAX_COUNT}")
        if batch_hold_ms < 0:
            raise ValueError("batch_hold_ms cannot be negative")
        hands = set(allowed_hands)
        if not hands or not hands <= {0, 1}:
            raise ValueError("allowed_hands must be a non-empty subset of (0, 1)")
        self.allowed_hands = tuple(hand for hand in (0, 1) if hand in hands)
        self.bridge_id = bridge_id or default_bridge_id()
        self.wifi = dict(wifi) if wifi is not None else None
        self.uplink = dict(uplink) if uplink is not None else None
        self._network = network
        self._network_refresh_s = max(0.0, network_refresh_s)
        self._network_refreshed_at: float | None = None
        self._network_lock = asyncio.Lock()
        self.token = token
        self.upgrade_dir = Path(upgrade_dir) if upgrade_dir is not None else DEFAULT_UPGRADE_DIR
        self.upgrade_lock = (
            Path(upgrade_lock) if upgrade_lock is not None else DEFAULT_UPGRADE_LOCK
        )
        self.queue_size = max(1, queue_size)
        # Set by the daemon when the BLE peripheral is up: opens or closes the
        # manual pairing window for the CLI's `wmr-bridge pair` command.
        self.pairing_handler: (
            Callable[[str, float | None], Awaitable[dict[str, Any]]] | None
        ) = None
        self.batch_max = batch_max
        self.batch_hold_s = batch_hold_ms / 1000.0
        self.fragment_capacity = max(13, fragment_capacity)
        self.auth_timeout_s = auth_timeout_s
        self.hands = [HandStats(), HandStats()]
        self.sequence = [0, 0]
        self.subscribers: set[Subscriber] = set()
        self.sinks: list[FrameSink] = []
        self._hand_listeners: list[Callable[[], None]] = []
        self.started_us = time.monotonic_ns() // 1000
        self.stats_dropped = 0
        self.stats_batches = 0
        if calibration is False:
            self.calibration: CalibrationCache | None = None
        elif isinstance(calibration, CalibrationCache):
            self.calibration = calibration
        else:
            self.calibration = CalibrationCache()

    # ---- ingest ----------------------------------------------------------

    def add_sink(self, sink: FrameSink) -> None:
        """Add a transport sink (BLE GATT) that is not a WebSocket client."""

        if sink not in self.sinks:
            self.sinks.append(sink)

    def remove_sink(self, sink: FrameSink) -> None:
        try:
            self.sinks.remove(sink)
        except ValueError:
            pass

    def add_hand_listener(self, listener: Callable[[], None]) -> None:
        """Watch controller connect/disconnect events (the BLE advert tier)."""

        if listener not in self._hand_listeners:
            self._hand_listeners.append(listener)

    def remove_hand_listener(self, listener: Callable[[], None]) -> None:
        try:
            self._hand_listeners.remove(listener)
        except ValueError:
            pass

    @property
    def stream_client_active(self) -> bool:
        """True while a WebSocket client is subscribed to any stream."""

        return any(subscriber.stream for subscriber in self.subscribers)

    def ingest(
        self,
        hand: int,
        data: bytes,
        rx_time_us: int | None = None,
        *,
        state: ControllerState | None = None,
    ) -> Frame | None:
        """Assign a sequence number and fan one report out to subscribers.

        ``state`` may carry an already-decoded frame so a transport that needs
        the activity flag does not force a second decode. The decoder is the
        fast binary32-exact path; its Fraction reference is only a test oracle.
        """

        if not 0 <= hand < 2:
            raise ValueError(f"bad hand {hand}")
        if hand not in self.allowed_hands or not data:
            return None
        now_us = time.monotonic_ns() // 1000 if rx_time_us is None else rx_time_us
        self.sequence[hand] = (self.sequence[hand] + 1) & 0xFFFFFFFF
        stats = self.hands[hand]
        stats.reports += 1
        stats.last_rx_us = now_us
        try:
            frame = Frame(hand=hand, sequence=self.sequence[hand], data=bytes(data), rx_time_us=now_us)
        except ProtocolError:
            stats.errors += 1
            return None
        if state is None:
            state = decode_frame(frame.data)
        if state is not None:
            stats.note_activity(state, now_us)
        subscribers = tuple(subscriber for subscriber in self.subscribers if subscriber.stream)
        sinks = tuple(self.sinks)
        if not subscribers and not sinks:
            return frame
        record = None
        if any(subscriber.stream in ("decoded", "batched") for subscriber in subscribers) or any(
            sink.needs_decoded for sink in sinks
        ):
            if state is None:
                stats.errors += 1
            else:
                record = record_from_state(state, hand, self.sequence[hand], now_us)
                stats.decoded += 1
        for subscriber in subscribers:
            subscriber.deliver(frame, record)
        for sink in sinks:
            try:
                sink.deliver(frame, record)
            except Exception:  # a BLE/bus failure must not stop the TCP stream
                LOG.exception("frame sink failed")
        return frame

    def set_hand_connected(self, hand: int, connected: bool, address: str = "") -> None:
        if not 0 <= hand < 2:
            return
        stats = self.hands[hand]
        changed = stats.connected != connected
        if connected and not stats.connected:
            # docs/PROTOCOL.md documents the FF11 counters as reports received
            # on the current connection, so a reconnect starts a fresh count.
            stats.reports = 0
            # A new connection is the start of a fresh idle window even when
            # the controller is motionless.
            stats.last_activity_us = self.now_us()
        if changed:
            # Both ends of a connection start a fresh activity baseline: the
            # next report is the reference, not a movement away from the last
            # session's pose.
            stats.reset_activity()
        stats.connected = connected
        if address:
            stats.address = address
        if changed:
            for listener in tuple(self._hand_listeners):
                try:
                    listener()
                except Exception:
                    LOG.exception("hand listener failed")

    # ---- calibration -----------------------------------------------------

    def set_calibration_state(self, hand: int, state: CalibrationState | int) -> None:
        """Mark a hand's config-block read in progress, failed, or cleared."""

        if self.calibration is not None:
            self.calibration.set_state(hand, state)

    def set_calibration_blob(self, hand: int, blob: bytes) -> None:
        """Cache a completed controller config block for FF13 forwarding."""

        if self.calibration is not None:
            self.calibration.set_blob(hand, blob)

    def calibration_window(self, hand: int, offset: int = 0) -> bytes:
        if self.calibration is None:
            raise ProtocolError("calibration is disabled")
        return self.calibration.window(hand, offset)

    def _calibration_info(self) -> dict[str, Any] | None:
        if self.calibration is None:
            return None
        return {
            "version": CALIBRATION_VERSION,
            "headerSize": CALIBRATION_HEADER_SIZE,
            "windowMax": CALIBRATION_WINDOW_MAX,
            "maxBlob": CALIBRATION_MAX_BLOB,
            "messageTag": wsmod.CALIBRATION_MESSAGE_TAG,
            "states": list(CALIBRATION_STATE_NAMES),
            "hands": [
                {"hand": HAND_NAMES[hand], **self.calibration.describe(hand)}
                for hand in range(2)
            ],
        }

    # ---- status ----------------------------------------------------------

    @property
    def hand_filter(self) -> list[str]:
        """Configured hands, e.g. ``["LEFT"]`` for a single-hand daemon."""

        return [HAND_NAMES[hand] for hand in self.allowed_hands]

    def now_us(self) -> int:
        return time.monotonic_ns() // 1000

    async def refresh_network(self) -> None:
        """Re-sample the uplink and Wi-Fi state off the event loop.

        The probe shells out to ``iw``/``nmcli``, so it must not run inside the
        loop. Samples are throttled to ``network_refresh_s`` so repeated
        ``hello`` messages (including from unauthenticated peers) cannot force
        continuous probes.
        """

        if self._network is None:
            return
        async with self._network_lock:
            now = time.monotonic()
            if (
                self._network_refreshed_at is not None
                and now - self._network_refreshed_at < self._network_refresh_s
            ):
                return
            try:
                wifi, uplink = await asyncio.to_thread(self._network)
            except Exception:  # a failed probe must not break the handshake
                LOG.exception("network snapshot failed")
                self._network_refreshed_at = time.monotonic()
                return
            self.wifi = wifi
            self.uplink = uplink
            self._network_refreshed_at = time.monotonic()

    # ---- upgrade trigger --------------------------------------------------

    @property
    def upgrade_trigger(self) -> Path:
        return self.upgrade_dir / "upgrade.trigger"

    @property
    def upgrade_capable(self) -> bool:
        """True only when the daemon may ask root to upgrade.

        A bridge without a token treats every client as authenticated, so it
        must never expose the trigger. systemd creates the state directory for
        the service user; a checkout run usually has no such directory.
        """

        if self.token is None:
            return False
        try:
            return self.upgrade_dir.is_dir() and os.access(self.upgrade_dir, os.W_OK)
        except OSError:
            return False

    @property
    def upgrade_attempts(self) -> Path:
        return self.upgrade_dir / "upgrade.attempts"

    def upgrade_busy(self) -> bool:
        """True while a fresh trigger is pending or root holds the lock."""

        age = self._trigger_age()
        if age is not None and age < UPGRADE_TRIGGER_TTL_S:
            return True
        try:
            fd = os.open(self.upgrade_lock, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            # Root owns the lock directory, so the daemon usually cannot create
            # the file; probing the existing one read-only takes the same flock.
            try:
                fd = os.open(self.upgrade_lock, os.O_RDONLY)
            except OSError:
                # No upgrade is running; let a write attempt report real errors.
                return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)

    def upgrade_rate_limited(self) -> bool:
        """True when another trigger would exhaust the root unit's start limit.

        systemd counts service starts, so the daemon keeps its own persisted
        history and stays one below ``StartLimitBurst``. A refused start would
        stop ``wmr-bridge-upgrade.path`` and leave the trigger unconsumed.
        """

        return len(self._recent_upgrade_starts()) >= UPGRADE_RATE_BURST

    def _trigger_age(self) -> float | None:
        """Age of the pending trigger in seconds, or ``None`` when there is none."""

        try:
            return time.time() - self.upgrade_trigger.stat().st_mtime
        except OSError:
            return None

    def _clear_stale_trigger(self) -> None:
        age = self._trigger_age()
        if age is None or age < UPGRADE_TRIGGER_TTL_S:
            return
        try:
            self.upgrade_trigger.unlink()
        except FileNotFoundError:
            return
        except OSError:
            LOG.warning("could not remove stale upgrade trigger %s", self.upgrade_trigger)
        else:
            LOG.warning("removed stale upgrade trigger (%.0f s old)", age)

    def _load_upgrade_starts(self) -> list[float]:
        try:
            text = self.upgrade_attempts.read_text(encoding="utf-8")
        except OSError:
            return []
        starts = []
        for line in text.splitlines():
            try:
                starts.append(float(line))
            except ValueError:
                continue
        return starts

    def _recent_upgrade_starts(self) -> list[float]:
        cutoff = time.time() - UPGRADE_RATE_WINDOW_S
        return [started for started in self._load_upgrade_starts() if started > cutoff]

    def _record_upgrade_start(self) -> None:
        starts = self._recent_upgrade_starts()
        starts.append(time.time())
        temp = self.upgrade_dir / ".upgrade.attempts.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, "".join(f"{started:.3f}\n" for started in starts).encode())
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.upgrade_attempts)
        except OSError:
            # Rate limiting is a guard, not the feature: never fail the request
            # because its bookkeeping could not be saved.
            LOG.warning("could not record upgrade attempts in %s", self.upgrade_dir)
            try:
                os.unlink(temp)
            except OSError:
                pass

    def request_upgrade(self) -> str:
        """Write the argument-free trigger, returning ``started`` or ``error``.

        The trigger file is always empty: the root oneshot deletes it before
        running the installed installer, so nothing a client sends can reach
        the privileged process. A trigger root never picked up is removed
        first, so a stale file cannot be mistaken for a queued upgrade.
        """

        self._clear_stale_trigger()
        temp = self.upgrade_dir / ".upgrade.trigger.tmp"
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.upgrade_trigger)
            dir_fd = os.open(self.upgrade_dir, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            except OSError:
                # Some filesystems reject fsync on a directory; the path unit
                # only needs the name to be visible.
                pass
            finally:
                os.close(dir_fd)
        except OSError:
            LOG.exception("could not write the upgrade trigger in %s", self.upgrade_dir)
            try:
                os.unlink(temp)
            except OSError:
                pass
            return "error"
        self._record_upgrade_start()
        LOG.info("upgrade trigger written to %s", self.upgrade_trigger)
        return "started"

    def status(self) -> dict[str, Any]:
        now = self.now_us()
        hands = []
        for index, stats in enumerate(self.hands):
            item = stats.to_json(self.sequence[index], now)
            item["hand"] = HAND_NAMES[index]
            hands.append(item)
        payload: dict[str, Any] = {
            "type": "status",
            "version": __version__,
            "handFilter": self.hand_filter,
            "uptimeS": round((now - self.started_us) / 1_000_000.0, 1),
            "hostTimeUs": now,
            "hands": hands,
            "clients": len(self.subscribers),
            "subscribed": sum(1 for s in self.subscribers if s.stream),
            "dropped": self.stats_dropped,
            "batches": self.stats_batches,
        }
        calibration = self._calibration_info()
        if calibration is not None:
            payload["calibration"] = calibration
        if self.wifi is not None:
            payload["wifi"] = self.wifi
        if self.uplink is not None:
            payload["uplink"] = self.uplink
        return payload

    def hello(self) -> dict[str, Any]:
        transport = (
            "ethernet"
            if (self.uplink or {}).get("kind") == "ethernet"
            else "wifi"
        )
        payload: dict[str, Any] = {
            "type": "hello",
            "protocol": 1,
            "transport": transport,
            "device": "wmr-pi",
            "bridge_id": self.bridge_id,
            "version": __version__,
            "handFilter": self.hand_filter,
            "hostname": socket.gethostname(),
            "authRequired": self.token is not None,
            "upgradeCapable": self.upgrade_capable,
            "streams": list(STREAMS),
            "recordSize": DECODED_SIZE,
            "batchMax": BATCH_MAX_COUNT,
            "fragmentCapacity": self.fragment_capacity,
        }
        calibration = self._calibration_info()
        if calibration is not None:
            payload["calibration"] = calibration
        if self.wifi is not None:
            payload["wifi"] = self.wifi
        if self.uplink is not None:
            payload["uplink"] = self.uplink
        return payload

    # ---- client session --------------------------------------------------

    async def run_client(self, websocket: wsmod.WebSocket) -> None:
        subscriber = Subscriber(self, websocket)
        self.subscribers.add(subscriber)
        peer = websocket.peer
        LOG.info("client connected: %s", peer)
        try:
            await self.refresh_network()
            await subscriber.send_json(self.hello())
            if self.token is not None:
                if not await self._authenticate(subscriber):
                    LOG.warning("client %s failed authentication", peer)
                    return
            while not subscriber.closed:
                try:
                    opcode, payload = await websocket.recv()
                except wsmod.WebSocketClosed:
                    return
                if opcode == wsmod.OP_BINARY:
                    await subscriber.send_json({"type": "error", "code": "binary_not_accepted"})
                    continue
                message = _parse_json(payload.decode("utf-8", errors="replace"))
                if message is None:
                    await subscriber.send_json({"type": "error", "code": "bad_json"})
                    continue
                if not await self._handle_control(subscriber, message):
                    return
        except (ConnectionError, OSError, wsmod.WebSocketError, wsmod.WebSocketClosed):
            pass
        finally:
            subscriber.close()
            self.subscribers.discard(subscriber)
            LOG.info("client disconnected: %s", peer)
            if not websocket.closed:
                await websocket.close()

    async def _authenticate(self, subscriber: Subscriber) -> bool:
        try:
            opcode, payload = await asyncio.wait_for(
                subscriber.websocket.recv(), timeout=self.auth_timeout_s
            )
        except (asyncio.TimeoutError, wsmod.WebSocketClosed):
            await subscriber.websocket.close(4401, "authentication required")
            return False
        message = _parse_json(payload.decode("utf-8", errors="replace")) if opcode == wsmod.OP_TEXT else None
        if (
            message is None
            or message.get("type") != "auth"
            or not isinstance(message.get("token"), str)
            or self.token is None
            or not _tokens_equal(message["token"], self.token)
        ):
            await subscriber.send_json({"type": "error", "code": "auth_failed"})
            await subscriber.websocket.close(4401, "authentication failed")
            return False
        subscriber.authed = True
        await subscriber.send_json({"type": "auth_ok"})
        return True

    async def _handle_control(self, subscriber: Subscriber, message: dict[str, Any]) -> bool:
        kind = message.get("type")

        if kind == "subscribe":
            stream = message.get("stream")
            if stream not in STREAMS:
                await subscriber.send_json({"type": "error", "code": "bad_stream"})
                return True
            if subscriber.stream and subscriber.stream != stream:
                await subscriber.send_json({"type": "error", "code": "stream_active"})
                return True
            if subscriber.stream == stream:
                await subscriber.send_json({"type": "subscribed", "stream": stream})
                return True
            subscriber.stream = stream
            await subscriber.send_json(
                {
                    "type": "subscribed",
                    "stream": stream,
                    "recordSize": DECODED_SIZE,
                    "batchMax": self.batch_max,
                    "fragmentCapacity": self.fragment_capacity,
                }
            )
            LOG.info("client %s subscribed to %s", subscriber.websocket.peer, stream)
            return True

        if kind == "unsubscribe":
            subscriber.reset_stream()
            await subscriber.send_json({"type": "unsubscribed"})
            LOG.info("client %s unsubscribed", subscriber.websocket.peer)
            return True

        if kind == "status":
            await subscriber.send_json(self.status())
            return True

        if kind == "ping":
            reply: dict[str, Any] = {
                "type": "pong",
                "id": message.get("id"),
                "serverTimeUs": self.now_us(),
            }
            if "t" in message:
                reply["t"] = message["t"]
            await subscriber.send_json(reply)
            return True

        if kind == "hello":
            await self.refresh_network()
            await subscriber.send_json(self.hello())
            return True

        if kind == "auth":
            if subscriber.authed:
                await subscriber.send_json({"type": "auth_ok"})
            else:
                await subscriber.send_json({"type": "error", "code": "auth_failed"})
            return True

        if kind == "calibration":
            if self.calibration is None:
                await subscriber.send_json({"type": "error", "code": "calibration_unavailable"})
                return True
            hand = _calibration_hand(message.get("hand"), self.allowed_hands)
            if hand is None:
                await subscriber.send_json({"type": "error", "code": "bad_hand"})
                return True
            offset = message.get("offset", 0)
            if (
                not isinstance(offset, int)
                or isinstance(offset, bool)
                or not 0 <= offset <= 0xFFFFFFFF
            ):
                await subscriber.send_json({"type": "error", "code": "bad_offset"})
                return True
            window = self.calibration.window(hand, offset)
            await subscriber.websocket.send_binary(wsmod.pack_calibration_message(window))
            return True

        if kind == "pairing":
            if not subscriber.authed:
                await subscriber.send_json({"type": "error", "code": "auth_failed"})
                return True
            action = message.get("action")
            if action not in ("open", "close"):
                await subscriber.send_json({"type": "error", "code": "bad_action"})
                return True
            seconds = message.get("seconds")
            if seconds is not None and (
                not isinstance(seconds, (int, float))
                or isinstance(seconds, bool)
                or not 0 < seconds <= 86400
            ):
                await subscriber.send_json({"type": "error", "code": "bad_seconds"})
                return True
            handler = self.pairing_handler
            if handler is None:
                await subscriber.send_json(
                    {"type": "error", "code": "pairing_unavailable"}
                )
                return True
            try:
                result = await handler(
                    action, None if seconds is None else float(seconds)
                )
            except Exception:
                LOG.exception("pairing control failed")
                await subscriber.send_json({"type": "error", "code": "pairing_failed"})
                return True
            await subscriber.send_json({"type": "pairing", **result})
            return True

        if kind == "upgrade":
            if not subscriber.authed or self.token is None:
                # A tokenless bridge marks every subscriber authed, so check
                # the token itself before touching the trigger.
                await subscriber.send_json(
                    {"type": "upgrade_status", "state": "error", "code": "auth_failed"}
                )
                return True
            if not self.upgrade_capable:
                await subscriber.send_json(
                    {"type": "upgrade_status", "state": "error", "code": "unavailable"}
                )
                return True
            if self.upgrade_busy() or self.upgrade_rate_limited():
                await subscriber.send_json({"type": "upgrade_status", "state": "busy"})
                return True
            state = self.request_upgrade()
            reply: dict[str, Any] = {"type": "upgrade_status", "state": state}
            if state == "error":
                reply["code"] = "unavailable"
            await subscriber.send_json(reply)
            return True

        if kind == "close":
            await subscriber.websocket.close(wsmod.CLOSE_NORMAL, "client requested close")
            return False

        await subscriber.send_json({"type": "error", "code": "unknown_type"})
        return True
