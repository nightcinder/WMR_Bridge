"""BlueZ D-Bus BLE GATT fallback peripheral for the Pi host bridge.

The Pi normally serves the FF11/FF12/FF14 payloads over a WebSocket on Wi-Fi.
When that LAN path is unreachable, a phone can fall back to the same payloads
over BLE. This module registers the firmware's FF10 GATT service (FF11-FF17)
with BlueZ through ``org.bluez.GattManager1`` and advertises the bridge with
``org.bluez.LEAdvertisingManager1``.

Two rules from the transport review shape the implementation:

* The stream is gated per central by BlueZ's CCCD state, not per bridge. A
  phone with notifications enabled keeps receiving FF11/FF12/FF14 even while
  another client is on TCP; a phone with notifications off receives nothing.
* FF14 is capped at 50 Hz because the Pi shares ``hci0`` with the two Classic
  HID controller links.

FF16 carries the plaintext LAN endpoint and ``bridge_id`` so a central can
upgrade to TCP without bonding. FF13 carries the session token and therefore
keeps an ``encrypt-read`` flag; bonding is only accepted while the pairing
window is open.

The D-Bus objects are only built when the fallback is enabled. ``dbus-next``
(``python3-dbus-next``) is imported lazily so the rest of the daemon and the
host test suite need no Bluetooth stack or third-party Python package.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from . import __version__
from .bridge import Bridge
from .protocol import (
    BATCH_HEADER_SIZE,
    BATCH_MAX_COUNT,
    DECODED_SIZE,
    DEFAULT_FRAGMENT_CAPACITY,
    DecodedRecord,
    Frame,
    ProtocolError,
    encode_batch,
    encode_raw_messages,
    pack_record,
)

LOG = logging.getLogger("wmr_bridge.ble_gatt")

FF10_SERVICE = "0000ff10-0000-1000-8000-00805f9b34fb"
FF11_RAW = "0000ff11-0000-1000-8000-00805f9b34fb"
FF12_DECODED = "0000ff12-0000-1000-8000-00805f9b34fb"
FF14_BATCHED = "0000ff14-0000-1000-8000-00805f9b34fb"
FF15_ECHO = "0000ff15-0000-1000-8000-00805f9b34fb"
FF13_CONFIG = "0000ff13-0000-1000-8000-00805f9b34fb"
FF16_VERSION = "0000ff16-0000-1000-8000-00805f9b34fb"
FF17_CONTROL = "0000ff17-0000-1000-8000-00805f9b34fb"

APP_PATH = "/org/wmr/bridge"
SERVICE_PATH = f"{APP_PATH}/service/ff10"
ADVERTISEMENT_PATH = f"{APP_PATH}/advertisement"
AGENT_PATH = f"{APP_PATH}/agent"

# Same opcodes and result codes as FF17 in docs/PROTOCOL.md.
CTRL_DISCOVER = 0x01
CTRL_CONNECT = 0x02
CTRL_DISCONNECT = 0x03
CTRL_FORGET_ADDRS = 0x04
CTRL_FORGET_BOND = 0x05

RESULT_OK = 0x00
RESULT_BUSY = 0x01
RESULT_NO_ADDRESS = 0x02
RESULT_NOTHING_TO_DO = 0x03
RESULT_NOT_CONNECTED = 0x04
RESULT_NOT_ENABLED = 0x05
RESULT_INVALID_PARAM = 0x06

DEFAULT_MTU = 23
DEFAULT_BATCH_HZ = 50.0
# Slow advertising leaves airtime for the two Classic HID links on the same
# radio while keeping a phone able to find the bridge promptly.
ADV_MIN_INTERVAL_MS = 1000
ADV_MAX_INTERVAL_MS = 1285


@dataclass(frozen=True)
class GattCharacteristicSpec:
    """One FF10 value attribute and the security/notification flags it needs."""

    uuid: str
    flags: tuple[str, ...]


# Order mirrors wmr_bridge.gatt: FF11, FF12, FF14, FF15, FF13, FF16, FF17.
# FF16 is the only plaintext value; every other value keeps the firmware's
# encrypted write/read posture. FF13 is encrypt-read because it carries the
# session token next to the calibration windows.
CHARACTERISTICS = (
    GattCharacteristicSpec(FF11_RAW, ("read", "notify", "encrypt-read", "encrypt-write")),
    GattCharacteristicSpec(FF12_DECODED, ("notify", "encrypt-write")),
    GattCharacteristicSpec(FF14_BATCHED, ("notify", "encrypt-write")),
    GattCharacteristicSpec(
        FF15_ECHO, ("write-without-response", "notify", "encrypt-write")
    ),
    GattCharacteristicSpec(
        FF13_CONFIG, ("read", "write", "notify", "encrypt-read", "encrypt-write")
    ),
    GattCharacteristicSpec(FF16_VERSION, ("read",)),
    GattCharacteristicSpec(
        FF17_CONTROL, ("read", "write", "notify", "encrypt-read", "encrypt-write")
    ),
)

CHARACTERISTIC_BY_UUID = {spec.uuid: spec for spec in CHARACTERISTICS}


def characteristic_path(uuid: str) -> str:
    """Stable object path for one FF10 characteristic."""

    short = uuid[4:8]
    return f"{SERVICE_PATH}/{short}"


class PairingAgentPolicy:
    """Decisions for ``org.bluez.Agent1`` requests on the peripheral adapter.

    A phone bonding over LE is the normal case, not a controller special case:
    before the agent existed, BlueZ had nobody to confirm the request and
    Android reported "pairing rejected by WMR Bridge". The adapter's
    ``Pairable`` flag already bounds when a new bond may be created, so the
    agent accepts every device BlueZ presents, including the WMR controllers
    that the Classic auto-pairer bonds on the same adapter.
    """

    def confirm(self, device: str) -> bool:
        return True

    def authorize(self, device: str) -> bool:
        return True

    def authorize_service(self, device: str, uuid: str) -> bool:
        return True


class GattBackend(Protocol):
    """The BlueZ side of the peripheral, kept injectable for the host tests."""

    async def start(self, app: "GattApplication") -> None: ...

    async def stop(self) -> None: ...

    async def set_pairable(self, pairable: bool) -> None: ...

    def notify(self, uuid: str, value: bytes) -> None: ...


class GattApplication:
    """Transport-independent FF10 state: values, CCCDs and stream encoding."""

    def __init__(
        self,
        bridge: Bridge,
        *,
        hosts: list[str] | Callable[[], list[str]] | None = None,
        port: int = 8765,
        path: str = "/wmr",
        name: str = "WMR Bridge",
        version: str = __version__,
        token: str | None = None,
        batch_hz: float = DEFAULT_BATCH_HZ,
        clock: Callable[[], float] = time.monotonic,
        fragment_capacity: int = DEFAULT_FRAGMENT_CAPACITY,
        control_handler: Callable[[int, int | None], int] | None = None,
    ) -> None:
        self.bridge = bridge
        self.port = port
        self.path = path
        self.name = name
        self.version = version
        self.token = token if token is not None else bridge.token
        self.batch_hz = max(1.0, batch_hz)
        self._clock = clock
        self._hosts = hosts if callable(hosts) else (lambda: list(hosts or []))
        self.fragment_capacity = max(13, fragment_capacity)
        self.control_handler = control_handler
        self.pairing_open = False
        self._mtu = DEFAULT_MTU
        self._subscribed: set[str] = set()
        self._last_value: dict[str, bytes] = {}
        self._emitter: Callable[[str, bytes], None] = lambda _uuid, _value: None
        self._batch: list[bytes] = []
        self._batch_timer: asyncio.TimerHandle | None = None
        self._last_batch = float("-inf")
        self._batch_interval = 1.0 / self.batch_hz
        self._ff13_cursor: tuple[int, int] | None = None
        self.dropped_records = 0
        self.last_command = 0
        self.last_result = RESULT_OK

    # ---- application description ----------------------------------------

    @property
    def characteristics(self) -> tuple[GattCharacteristicSpec, ...]:
        return CHARACTERISTICS

    @property
    def needs_decoded(self) -> bool:
        """Decode only while a central watches FF12/FF14; FF11 is raw."""

        return FF12_DECODED in self._subscribed or FF14_BATCHED in self._subscribed

    @property
    def subscribed(self) -> frozenset[str]:
        return frozenset(self._subscribed)

    def is_notifying(self, uuid: str) -> bool:
        return uuid in self._subscribed

    # ---- metadata --------------------------------------------------------

    def lan_hosts(self) -> list[str]:
        return list(self._hosts())

    def endpoint_document(self) -> dict[str, Any]:
        """Plaintext FF16 document: enough to reach the TCP listener."""

        return {
            "type": "wmr_ble_endpoint",
            "protocol": 1,
            "bridge_id": self.bridge.bridge_id,
            "name": self.name,
            "version": self.version,
            "hosts": self.lan_hosts(),
            "port": self.port,
            "path": self.path,
            "authRequired": self.token is not None,
        }

    def session_document(self) -> dict[str, Any]:
        """Encrypt-read FF13 document: the token is never in FF16."""

        document: dict[str, Any] = {
            "type": "wmr_ble_session",
            "protocol": 1,
            "bridge_id": self.bridge.bridge_id,
            "version": self.version,
            "port": self.port,
            "path": self.path,
            "authRequired": self.token is not None,
            "pairingOpen": self.pairing_open,
        }
        if self.token is not None:
            document["token"] = self.token
        return document

    def ff16_value(self) -> bytes:
        return json.dumps(self.endpoint_document(), separators=(",", ":")).encode("utf-8")

    def ff13_value(self) -> bytes:
        return json.dumps(self.session_document(), separators=(",", ":")).encode("utf-8")

    def advertisement_data(self) -> dict[str, Any]:
        """Legacy advertising data; the bridge id is repeated as service data.

        A Pi's BLE address can be random, so a scanner cannot key on it. The
        first eight bytes are enough to match the advertisement with the
        ``bridge_id`` read from FF16. The interval is deliberately slow so
        advertising does not steal airtime from the Classic controller links.
        """

        tag = self.bridge.bridge_id.encode("ascii", "replace")[:8]
        return {
            "type": "peripheral",
            # BlueZ only adds the LE General Discoverable flag to the
            # advertisement when this is set. Without it the adapter still
            # advertises FF10 (ActiveInstances 1) but a phone's pairing list
            # filters the bridge out.
            "discoverable": True,
            "service_uuids": [FF10_SERVICE],
            "local_name": self.name,
            "service_data": {FF10_SERVICE: b"WMB1" + tag},
            "include_tx_power": False,
            "min_interval_ms": ADV_MIN_INTERVAL_MS,
            "max_interval_ms": ADV_MAX_INTERVAL_MS,
        }

    # ---- read/write ------------------------------------------------------

    def _option(self, options: dict[str, Any] | None, key: str, default: Any) -> Any:
        if not options:
            return default
        value = options.get(key, default)
        return getattr(value, "value", value)

    def _note_mtu(self, options: dict[str, Any] | None) -> None:
        mtu = self._option(options, "mtu", self._mtu)
        if isinstance(mtu, int) and 23 <= mtu <= 517:
            self._mtu = mtu

    @property
    def mtu(self) -> int:
        return self._mtu

    @property
    def notification_limit(self) -> int:
        """Largest ATT notification value at the negotiated MTU."""

        return self._mtu - 3

    @property
    def batch_max_count(self) -> int:
        """Records that fit one FF14 batch, capped by the protocol maximum.

        Zero means the MTU is too small for even one 56-byte record.
        """

        if self.notification_limit < BATCH_HEADER_SIZE + DECODED_SIZE:
            return 0
        count = (self.notification_limit - BATCH_HEADER_SIZE) // DECODED_SIZE
        return min(count, BATCH_MAX_COUNT)

    @property
    def fragment_size(self) -> int:
        """Largest FF11 notification value for the current ATT MTU."""

        return max(13, min(self.fragment_capacity, self._mtu - 3))

    def read_value(self, uuid: str, options: dict[str, Any] | None = None) -> bytes:
        self._note_mtu(options)
        if uuid == FF11_RAW:
            return self.ff11_status()
        if uuid == FF16_VERSION:
            return self.ff16_value()
        if uuid == FF13_CONFIG:
            if self._ff13_cursor is not None:
                hand, offset = self._ff13_cursor
                try:
                    return self.bridge.calibration_window(hand, offset)
                except ProtocolError:
                    return b""
            return self.ff13_value()
        if uuid == FF17_CONTROL:
            return self.ff17_status()
        return self._last_value.get(uuid, b"")

    def write_value(
        self, uuid: str, value: bytes, options: dict[str, Any] | None = None
    ) -> int:
        """Handle an ATT write; returns an FF17 result code (0 is OK)."""

        self._note_mtu(options)
        if uuid == FF15_ECHO:
            return self._echo(value)
        if uuid == FF13_CONFIG:
            return self._write_config_cursor(value)
        if uuid == FF17_CONTROL:
            result = self._write_control(value)
            self.notify_control_status()
            return result
        return RESULT_OK

    def notify_control_status(self) -> None:
        """Push the FF17 last_command/last_result value after a write."""

        self._notify(FF17_CONTROL, self.ff17_status())

    def _write_config_cursor(self, value: bytes) -> int:
        if not value:
            self._ff13_cursor = None
            return RESULT_OK
        if len(value) != 5:
            return RESULT_INVALID_PARAM
        hand = value[0]
        offset = int.from_bytes(value[1:5], "little")
        if hand not in (0, 1):
            return RESULT_INVALID_PARAM
        self._ff13_cursor = (hand, offset)
        return RESULT_OK

    def _echo(self, value: bytes) -> int:
        if len(value) != 4 or value[0] != 1:
            return RESULT_INVALID_PARAM
        if not self.is_notifying(FF15_ECHO):
            return RESULT_NOT_CONNECTED
        ping_id = int.from_bytes(value[2:4], "little")
        now_us = self.bridge.now_us()
        answer = struct.pack("<BBHQIHH", 1, 0, ping_id, now_us, 0, 0, 0)
        self._notify(FF15_ECHO, answer)
        return RESULT_OK

    def ff17_status(self) -> bytes:
        payload = bytearray(20)
        payload[0] = 1
        for hand, stats in enumerate(self.bridge.hands):
            base = 1 + 7 * hand
            if stats.connected:
                payload[base] |= 0x01
            tail = _address_tail(stats.address)
            payload[base + 3 : base + 6] = tail
        payload[17] = self.last_command
        payload[18] = self.last_result
        return bytes(payload)

    def ff11_status(self) -> bytes:
        """The 16-byte FF11 status read documented in docs/PROTOCOL.md.

        The Pi has no HID-descriptor handshake, so the descriptor bits stay
        zero; the remaining fields mirror the firmware's status value.
        """

        payload = bytearray(16)
        payload[0] = 1
        for hand, stats in enumerate(self.bridge.hands):
            if stats.connected:
                payload[1] |= 1 << hand
        uptime_s = (self.bridge.now_us() - self.bridge.started_us) // 1_000_000
        payload[4:8] = (max(0, uptime_s) & 0xFFFFFFFF).to_bytes(4, "little")
        for hand, stats in enumerate(self.bridge.hands):
            base = 8 + 4 * hand
            payload[base : base + 4] = (stats.reports & 0xFFFFFFFF).to_bytes(4, "little")
        return bytes(payload)

    def _write_control(self, value: bytes) -> int:
        if not 1 <= len(value) <= 2:
            return self._record_control(RESULT_INVALID_PARAM)
        opcode = value[0]
        self.last_command = opcode
        if opcode not in (
            CTRL_DISCOVER,
            CTRL_CONNECT,
            CTRL_DISCONNECT,
            CTRL_FORGET_ADDRS,
            CTRL_FORGET_BOND,
        ):
            return self._record_control(RESULT_INVALID_PARAM)
        hand: int | None = None
        if opcode in (CTRL_CONNECT, CTRL_DISCONNECT, CTRL_FORGET_BOND):
            if len(value) != 2 or value[1] not in (0, 1):
                return self._record_control(RESULT_INVALID_PARAM)
            hand = value[1]
        if hand is not None and hand not in self.bridge.allowed_hands:
            self.last_command = opcode
            return self._record_control(RESULT_NOT_ENABLED)
        self.last_command = opcode
        if self.control_handler is not None:
            result = self.control_handler(opcode, hand)
        else:
            # The Pi's Classic side is managed by HidrawManager/BluezAutoPairer.
            # Answering NOTHING_TO_DO is honest: the command is understood but
            # this transport has no action to start.
            result = RESULT_NOTHING_TO_DO
        return self._record_control(result)

    def _record_control(self, result: int) -> int:
        self.last_result = result
        return result

    # ---- stream delivery -------------------------------------------------

    def set_emitter(self, emit: Callable[[str, bytes], None]) -> None:
        self._emitter = emit

    def deliver(self, frame: Frame, record: DecodedRecord | None = None) -> None:
        if self.is_notifying(FF11_RAW):
            for fragment in encode_raw_messages(frame, self.fragment_size):
                self._notify(FF11_RAW, fragment)
        if record is None:
            return
        packed = pack_record(record)
        if self.is_notifying(FF12_DECODED):
            if len(packed) <= self.notification_limit:
                self._notify(FF12_DECODED, packed)
            else:
                self.dropped_records += 1
        if self.is_notifying(FF14_BATCHED):
            if self.batch_max_count:
                self._queue_batch(packed)
            else:
                self.dropped_records += 1

    def _notify(self, uuid: str, value: bytes) -> None:
        if uuid not in self._subscribed:
            return
        self._last_value[uuid] = value
        try:
            self._emitter(uuid, value)
        except Exception:  # a closed bus must not take the TCP path down
            LOG.exception("BLE notification for %s failed", uuid)

    def start_notify(self, uuid: str) -> None:
        if uuid not in CHARACTERISTIC_BY_UUID:
            return
        self._subscribed.add(uuid)

    def stop_notify(self, uuid: str) -> None:
        self._subscribed.discard(uuid)
        if uuid == FF14_BATCHED:
            self._discard_batch()

    def _queue_batch(self, packed: bytes) -> None:
        self._batch.append(packed)
        if len(self._batch) >= self.batch_max_count:
            self.flush_batches()
            return
        now = self._clock()
        if now - self._last_batch >= self._batch_interval:
            self.flush_batches()
        else:
            self._schedule_batch_flush()

    def _schedule_batch_flush(self) -> None:
        if self._batch_timer is not None:
            return
        delay = max(0.001, self._batch_interval - (self._clock() - self._last_batch))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._batch_timer = loop.call_later(delay, self._flush_batch_timer)

    def _flush_batch_timer(self) -> None:
        self._batch_timer = None
        self.flush_batches()

    def flush_batches(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        if not self._batch:
            return
        records = self._batch
        self._batch = []
        self._last_batch = self._clock()
        max_count = max(1, self.batch_max_count)
        for start in range(0, len(records), max_count):
            chunk = records[start : start + max_count]
            if BATCH_HEADER_SIZE + len(chunk) * DECODED_SIZE > self.notification_limit:
                self.dropped_records += len(chunk)
                continue
            try:
                payload = encode_batch(chunk)
            except ProtocolError:
                self.dropped_records += len(chunk)
                continue
            self._notify(FF14_BATCHED, payload)

    def _discard_batch(self) -> None:
        if self._batch_timer is not None:
            self._batch_timer.cancel()
            self._batch_timer = None
        self._batch.clear()


def _address_tail(address: str) -> bytes:
    """Last three MAC bytes, or zero when no address is known."""

    parts = address.replace("-", ":").split(":")
    if len(parts) != 6:
        return b"\x00\x00\x00"
    try:
        return bytes(int(part, 16) for part in parts[3:])
    except ValueError:
        return b"\x00\x00\x00"


def build_dbus_interfaces(app: GattApplication) -> dict[str, type[Any]]:
    """Build the dbus-next service interfaces BlueZ will export for *app*.

    dbus-next is imported lazily so the host suite and the daemon run without
    ``python3-dbus-next``. The classes are returned instead of being defined
    inside :meth:`DbusGattBackend.start` so a host test can build them against
    the installed dbus-next without a live system bus.

    BlueZ discovers the tree with ``ObjectManager.GetManagedObjects`` on the
    application root. dbus-next 0.2.3 answers that call itself from the
    interfaces exported on the bus and ignores a ``GetManagedObjects`` method
    defined on an exported interface, so every object BlueZ must see -- above
    all the ``GattService1`` at :data:`SERVICE_PATH` -- has to be exported by
    :func:`export_gatt_application`. BlueZ creates the CCC descriptor for a
    notify/indicate characteristic itself, so the app exports none.
    """

    try:
        from dbus_next import Variant
        from dbus_next.constants import PropertyAccess
        from dbus_next.errors import DBusError
        from dbus_next.service import ServiceInterface, dbus_property, method
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise RuntimeError(
            "BLE fallback needs python3-dbus-next (apt install python3-dbus-next)"
        ) from exc

    def variant(value: Any, signature: str) -> Any:
        return Variant(signature, value)

    class Service(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.GattService1")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return FF10_SERVICE

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Characteristics(self) -> "ao":
            return [characteristic_path(spec.uuid) for spec in CHARACTERISTICS]

    class Characteristic(ServiceInterface):
        def __init__(self, spec: GattCharacteristicSpec) -> None:
            super().__init__("org.bluez.GattCharacteristic1")
            self.spec = spec
            self.value = b""

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return self.spec.uuid

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> "o":
            return SERVICE_PATH

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return list(self.spec.flags)

        @dbus_property(access=PropertyAccess.READ)
        def Notifying(self) -> "b":
            return app.is_notifying(self.spec.uuid)

        @dbus_property(access=PropertyAccess.READ)
        def MTU(self) -> "q":
            return app.mtu

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> "ay":
            return self.value

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":
            self.value = app.read_value(self.spec.uuid, options)
            return self.value

        @method()
        def WriteValue(self, value: "ay", options: "a{sv}"):
            result = app.write_value(self.spec.uuid, bytes(value), options)
            if result != RESULT_OK:
                # BlueZ turns a D-Bus error into an ATT error, so a client
                # sees a refused FF13 cursor/FF17 opcode as a failed write.
                raise DBusError(
                    "org.bluez.Error.Failed",
                    f"FF10 write refused (result 0x{result:02x})",
                )

        @method()
        def StartNotify(self):
            app.start_notify(self.spec.uuid)
            self.emit_properties_changed(
                {"Notifying": variant(True, "b")}, []
            )

        @method()
        def StopNotify(self):
            app.stop_notify(self.spec.uuid)
            self.emit_properties_changed(
                {"Notifying": variant(False, "b")}, []
            )

    class Advertisement(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.LEAdvertisement1")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> "s":
            return "peripheral"

        @dbus_property(access=PropertyAccess.READ)
        def Discoverable(self) -> "b":
            return bool(app.advertisement_data()["discoverable"])

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return [FF10_SERVICE]

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> "s":
            return app.name

        @dbus_property(access=PropertyAccess.READ)
        def MinInterval(self) -> "u":
            return app.advertisement_data()["min_interval_ms"]

        @dbus_property(access=PropertyAccess.READ)
        def MaxInterval(self) -> "u":
            return app.advertisement_data()["max_interval_ms"]

        @dbus_property(access=PropertyAccess.READ)
        def ServiceData(self) -> "a{sv}":
            data = app.advertisement_data()["service_data"][FF10_SERVICE]
            return {FF10_SERVICE: variant(data, "ay")}

        @dbus_property(access=PropertyAccess.READ)
        def IncludeTxPower(self) -> "b":
            return False

        @method()
        def Release(self):
            return None

    class Agent(ServiceInterface):
        """NoInputNoOutput agent for LE Just Works bonding from a phone."""

        def __init__(self) -> None:
            super().__init__("org.bluez.Agent1")
            self.policy = PairingAgentPolicy()

        @method()
        def Release(self):
            return None

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            # NoInputNoOutput means BlueZ must not ask, but a peer can still
            # request legacy pairing; reject it instead of inventing a code.
            raise DBusError(
                "org.bluez.Error.Rejected", "WMR Bridge has no pairing input"
            )

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            return None

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            raise DBusError(
                "org.bluez.Error.Rejected", "WMR Bridge has no pairing input"
            )

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            return None

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            if not self.policy.confirm(device):
                raise DBusError(
                    "org.bluez.Error.Rejected", "pairing confirmation refused"
                )

        @method()
        def RequestAuthorization(self, device: "o"):
            if not self.policy.authorize(device):
                raise DBusError(
                    "org.bluez.Error.Rejected", "pairing authorization refused"
                )

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            if not self.policy.authorize_service(device, uuid):
                raise DBusError(
                    "org.bluez.Error.Rejected", "service authorization refused"
                )

        @method()
        def Cancel(self):
            return None

    return {
        "Service": Service,
        "Characteristic": Characteristic,
        "Advertisement": Advertisement,
        "Agent": Agent,
    }


def export_gatt_application(
    bus: Any, interfaces: dict[str, type[Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Export the FF10 service and characteristics for ``GetManagedObjects``.

    Returns the characteristic interfaces by UUID (for notifications) and the
    exported object paths (for ``unexport``). dbus-next derives the
    ``GetManagedObjects`` reply from these exports, so the service object must
    be exported too or BlueZ answers ``RegisterApplication`` with "No valid
    service object found".
    """

    bus.export(SERVICE_PATH, interfaces["Service"]())
    exported = [SERVICE_PATH]
    characteristics: dict[str, Any] = {}
    for spec in CHARACTERISTICS:
        path = characteristic_path(spec.uuid)
        characteristic = interfaces["Characteristic"](spec)
        bus.export(path, characteristic)
        exported.append(path)
        characteristics[spec.uuid] = characteristic
    return characteristics, exported


class DbusGattBackend:
    """``org.bluez.GattManager1`` / ``LEAdvertisingManager1`` implementation."""

    def __init__(self, adapter: str = "hci0") -> None:
        self.adapter = adapter
        self._bus: Any = None
        self._advertisement: Any = None
        self._adapter_interface: Any = None
        self._gatt_manager: Any = None
        self._advertising_manager: Any = None
        self._agent_manager: Any = None
        self._agent_registered = False
        self._exported: list[Any] = []
        self._characteristics: dict[str, Any] = {}
        self._variant: Any = None

    async def start(self, app: GattApplication) -> None:
        try:
            interfaces = build_dbus_interfaces(app)
            from dbus_next import Variant
            from dbus_next.aio import MessageBus
            from dbus_next.constants import BusType
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise RuntimeError(
                "BLE fallback needs python3-dbus-next (apt install python3-dbus-next)"
            ) from exc

        self._variant = Variant
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus = bus
        adapter_path = f"/org/bluez/{self.adapter}"

        try:
            introspection = await bus.introspect("org.bluez", adapter_path)
            adapter_object = bus.get_proxy_object("org.bluez", adapter_path, introspection)
            self._adapter_interface = adapter_object.get_interface("org.bluez.Adapter1")
            self._gatt_manager = adapter_object.get_interface("org.bluez.GattManager1")
            self._advertising_manager = adapter_object.get_interface(
                "org.bluez.LEAdvertisingManager1"
            )
        except Exception as exc:
            bus.disconnect()
            self._bus = None
            raise RuntimeError(f"BlueZ adapter {self.adapter} is not available: {exc}") from exc


        self._advertisement = interfaces["Advertisement"]()
        bus.export(ADVERTISEMENT_PATH, self._advertisement)
        self._exported.append(ADVERTISEMENT_PATH)
        bus.export(AGENT_PATH, interfaces["Agent"]())
        self._exported.append(AGENT_PATH)
        self._characteristics, exported = export_gatt_application(bus, interfaces)
        self._exported.extend(exported)

        try:
            manager_introspection = await bus.introspect("org.bluez", "/org/bluez")
            manager_object = bus.get_proxy_object(
                "org.bluez", "/org/bluez", manager_introspection
            )
            self._agent_manager = manager_object.get_interface(
                "org.bluez.AgentManager1"
            )
        except Exception as exc:
            LOG.warning("BlueZ pairing agent unavailable: %s", exc)
        await self._register_agent()

        try:
            await self._gatt_manager.call_register_application(APP_PATH, {})
            await self._advertising_manager.call_register_advertisement(
                ADVERTISEMENT_PATH, {}
            )
        except Exception:
            await self.stop()
            raise

    async def _register_agent(self) -> None:
        """Register the Just Works agent Android needs for LE bonding.

        BlueZ rejects an incoming pair request when no default agent is set.
        bluetoothctl in the Classic auto-pairer registers its own agent and
        that agent disappears when the session exits, so opening the window
        claims default again.
        """

        if self._agent_manager is None:
            return
        try:
            await self._agent_manager.call_register_agent(AGENT_PATH, "NoInputNoOutput")
            self._agent_registered = True
            await self._agent_manager.call_request_default_agent(AGENT_PATH)
            LOG.info("BLE pairing agent registered (NoInputNoOutput)")
        except Exception as exc:
            LOG.warning("could not register the BlueZ pairing agent: %s", exc)

    def notify(self, uuid: str, value: bytes) -> None:
        characteristic = self._characteristics.get(uuid)
        if characteristic is None:
            return
        characteristic.value = bytes(value)
        characteristic.emit_properties_changed(
            {"Value": self._variant("ay", bytes(value))}, []
        )

    async def set_pairable(self, pairable: bool) -> None:
        if self._adapter_interface is None:
            return
        await self._adapter_interface.set_pairable(bool(pairable))
        if pairable and self._agent_registered and self._agent_manager is not None:
            # The Classic auto-pairer's bluetoothctl session registers its own
            # default agent and drops it on exit; reclaim ours so a phone that
            # bonds during this window still has an agent to confirm it.
            try:
                await self._agent_manager.call_request_default_agent(AGENT_PATH)
            except Exception as exc:
                LOG.debug("could not make the pairing agent default: %s", exc)

    async def stop(self) -> None:
        bus = self._bus
        if bus is None:
            return
        if self._agent_manager is not None and self._agent_registered:
            try:
                await self._agent_manager.call_unregister_agent(AGENT_PATH)
            except Exception as exc:  # already gone is fine
                LOG.debug("unregister agent: %s", exc)
            self._agent_registered = False
        if self._advertising_manager is not None:
            try:
                await self._advertising_manager.call_unregister_advertisement(
                    ADVERTISEMENT_PATH
                )
            except Exception as exc:  # already gone is fine
                LOG.debug("unregister advertisement: %s", exc)
        if self._gatt_manager is not None:
            try:
                await self._gatt_manager.call_unregister_application(APP_PATH)
            except Exception as exc:
                LOG.debug("unregister application: %s", exc)
        for path in self._exported:
            try:
                bus.unexport(path)
            except Exception:
                pass
        self._exported.clear()
        self._characteristics.clear()
        self._bus = None
        self._adapter_interface = None
        self._gatt_manager = None
        self._advertising_manager = None
        self._agent_manager = None
        bus.disconnect()


class BluezGattPeripheral:
    """Attach a BlueZ GATT application to a :class:`Bridge`."""

    def __init__(
        self,
        bridge: Bridge,
        *,
        backend: GattBackend | None = None,
        adapter: str = "hci0",
        hosts: list[str] | Callable[[], list[str]] | None = None,
        port: int = 8765,
        path: str = "/wmr",
        name: str = "WMR Bridge",
        pairing_window_s: float = 120.0,
        batch_hz: float = DEFAULT_BATCH_HZ,
        fragment_capacity: int = DEFAULT_FRAGMENT_CAPACITY,
        control_handler: Callable[[int, int | None], int] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.app = GattApplication(
            bridge,
            hosts=hosts,
            port=port,
            path=path,
            name=name,
            token=bridge.token,
            batch_hz=batch_hz,
            fragment_capacity=fragment_capacity,
            control_handler=control_handler,
            clock=clock,
        )
        self.backend = backend or DbusGattBackend(adapter)
        self.pairing_window_s = max(0.0, pairing_window_s)
        self._started = False
        self._pairing_task: asyncio.Task[None] | None = None

    @property
    def started(self) -> bool:
        return self._started

    def pairing_window_open(self) -> bool:
        """True while the bounded bonding window is open."""
        return self.app.pairing_open

    async def start(self) -> None:
        if self._started:
            return
        await self.backend.start(self.app)
        self.app.set_emitter(self.backend.notify)
        self.app.bridge.add_sink(self.app)
        self._started = True
        if self.pairing_window_s > 0:
            await self.open_pairing_window()
        else:
            await self.close_pairing_window()
        LOG.info(
            "BLE fallback advertising FF10 as %s (bridge_id %s)",
            self.app.name,
            self.app.bridge.bridge_id,
        )

    async def open_pairing_window(self) -> None:
        self.app.pairing_open = True
        try:
            await self.backend.set_pairable(True)
        except Exception as exc:
            LOG.warning("could not make the adapter pairable: %s", exc)
        self._cancel_pairing_task()
        if self.pairing_window_s > 0:
            self._pairing_task = asyncio.create_task(
                self._close_pairing_after(self.pairing_window_s)
            )

    async def _close_pairing_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        # This task is finishing; do not cancel it from close_pairing_window.
        self._pairing_task = None
        await self.close_pairing_window()

    async def close_pairing_window(self) -> None:
        self.app.pairing_open = False
        self._cancel_pairing_task()
        # Adapter1.Pairable is shared with the Classic auto-pairer on hci0, but
        # it is the only switch that gates a new bond. Leaving it as it was (or
        # True) would keep the BLE door open after the window, so the fallback
        # always clears it; the Classic pairer sets it again for its own scan.
        try:
            await self.backend.set_pairable(False)
        except Exception as exc:
            LOG.debug("could not disable pairing: %s", exc)

    def _cancel_pairing_task(self) -> None:
        if self._pairing_task is not None:
            self._pairing_task.cancel()
            self._pairing_task = None

    async def close(self) -> None:
        if self._started:
            await self.close_pairing_window()
            self.app.bridge.remove_sink(self.app)
            self.app.set_emitter(lambda _uuid, _value: None)
            self.app.stop_notify(FF14_BATCHED)
            self._started = False
        else:
            self._cancel_pairing_task()
        await self.backend.stop()
