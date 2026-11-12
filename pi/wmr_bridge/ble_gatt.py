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
from .bluez import WMR_NAMES
from .bluez_conf import DEFAULT_MAIN_CONF as BLUEZ_MAIN_CONF
from .bluez_conf import read_just_works_repairing
from .bridge import Bridge
from .decode import record_from_frame
from .hid_gamepad import (
    NEUTRAL_REPORT,
    REPORT_ID,
    REPORT_MAP as HID_REPORT_MAP_VALUE,
    HandInput,
    HidHistory,
    build_report,
    clamp,
)
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
HID_SERVICE = "00001812-0000-1000-8000-00805f9b34fb"
HID_INFORMATION = "00002a4a-0000-1000-8000-00805f9b34fb"
HID_REPORT_MAP = "00002a4b-0000-1000-8000-00805f9b34fb"
HID_CONTROL_POINT = "00002a4c-0000-1000-8000-00805f9b34fb"
HID_REPORT = "00002a4d-0000-1000-8000-00805f9b34fb"
HID_PROTOCOL_MODE = "00002a4e-0000-1000-8000-00805f9b34fb"
HID_REPORT_REFERENCE = "00002908-0000-1000-8000-00805f9b34fb"
FF11_RAW = "0000ff11-0000-1000-8000-00805f9b34fb"
FF12_DECODED = "0000ff12-0000-1000-8000-00805f9b34fb"
FF14_BATCHED = "0000ff14-0000-1000-8000-00805f9b34fb"
FF15_ECHO = "0000ff15-0000-1000-8000-00805f9b34fb"
FF13_CONFIG = "0000ff13-0000-1000-8000-00805f9b34fb"
FF16_VERSION = "0000ff16-0000-1000-8000-00805f9b34fb"
FF17_CONTROL = "0000ff17-0000-1000-8000-00805f9b34fb"

APP_PATH = "/org/wmr/bridge"
SERVICE_PATH = f"{APP_PATH}/service/ff10"
HID_SERVICE_PATH = f"{APP_PATH}/service/1812"
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
# HID Information: bcdHID 1.11, country code 0, Normally Connectable. The
# firmware's hids_device_init() uses the same value.
HID_INFORMATION_VALUE = bytes((0x11, 0x01, 0x00, 0x02))
HID_PROTOCOL_REPORT = 0x01
# At most one HID input report per interval; samples in between are coalesced.
HID_MIN_INTERVAL_S = 0.010
# A hand with no sample for this long is released, matching WMR_HID_STALE_MS.
HID_STALE_S = 0.250
# Gamepad appearance (GAP 0x03C4): the icon Android shows for the LE entry.
GAMEPAD_APPEARANCE = 0x03C4
# Fast advertising while neither Classic HID link is up: there is no
# controller traffic to protect, and a phone finds the bridge promptly.
ADV_FAST_MIN_INTERVAL_MS = 150
ADV_FAST_MAX_INTERVAL_MS = 250
# Slow advertising leaves airtime for the two Classic HID links on the same
# radio once a controller connects (see docs).
ADV_MIN_INTERVAL_MS = 1000
ADV_MAX_INTERVAL_MS = 1285
# A newly flashed Pi is pairable for this long after BLE starts even when a
# stale bond exists. After it, pairability follows the stored-bond state.
DEFAULT_PAIRING_WINDOW_S = 180.0
# Keep the agent and pairability open this long after the last Agent1 request.
# BlueZ stores the bond before the phone finishes service discovery and enables
# the HID report, so closing the window on the first bond check can release the
# agent while the phone is still using it.
PAIRING_AGENT_GRACE_S = 10.0
# Classic HID profile UUID. A Device1 entry that lists it is a BR/EDR bond
# (the WMR controllers), never an LE central's bond.
CLASSIC_HID_UUID = "00001124-0000-1000-8000-00805f9b34fb"
PAIRING_REJECTED_HINT = (
    "pairing rejected by WMR Bridge: no BLE pairing window is open. Run "
    "'wmr-bridge pair' on the Pi to open one, or remove the old bond in your "
    "phone's Bluetooth settings and restart the service"
)


@dataclass(frozen=True)
class GattDescriptorSpec:
    """One descriptor under a characteristic (the HID Report Reference)."""

    uuid: str
    flags: tuple[str, ...]
    value: bytes


@dataclass(frozen=True)
class GattCharacteristicSpec:
    """One FF10/HID value attribute and the security/notification flags it needs."""

    uuid: str
    flags: tuple[str, ...]
    service: str = FF10_SERVICE
    descriptors: tuple[GattDescriptorSpec, ...] = ()


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

# HOGP (0x1812). Report Map and the input report are byte for byte the
# firmware's src/wmr_hid.c; every value is encrypted because Android binds a
# HID device only over a bonded link. The Report Reference descriptor (0x2908)
# is exported because BlueZ only synthesises the CCCD: without it a HOGP host
# cannot tell which report id the characteristic carries. BlueZ creates the
# Report CCCD itself.
HID_CHARACTERISTICS = (
    GattCharacteristicSpec(HID_INFORMATION, ("read", "encrypt-read"), HID_SERVICE),
    GattCharacteristicSpec(HID_REPORT_MAP, ("read", "encrypt-read"), HID_SERVICE),
    GattCharacteristicSpec(
        HID_CONTROL_POINT, ("write-without-response", "encrypt-write"), HID_SERVICE
    ),
    GattCharacteristicSpec(
        HID_REPORT,
        ("read", "notify", "encrypt-read", "encrypt-write"),
        HID_SERVICE,
        (
            GattDescriptorSpec(
                HID_REPORT_REFERENCE,
                ("read",),
                bytes((REPORT_ID, 0x01)),  # input report, id 1
            ),
        ),
    ),
    GattCharacteristicSpec(
        HID_PROTOCOL_MODE,
        ("read", "write", "encrypt-read", "encrypt-write"),
        HID_SERVICE,
    ),
)

HID_CHARACTERISTIC_BY_UUID = {spec.uuid: spec for spec in HID_CHARACTERISTICS}


def characteristic_path(uuid: str) -> str:
    """Stable object path for one FF10 characteristic."""

    short = uuid[4:8]
    return f"{SERVICE_PATH}/{short}"


def hid_characteristic_path(uuid: str) -> str:
    """Stable object path for one HID-over-GATT characteristic."""

    short = uuid[4:8]
    return f"{HID_SERVICE_PATH}/{short}"


def descriptor_path(characteristic: str, uuid: str) -> str:
    """Stable object path for a descriptor under *characteristic*."""

    return f"{characteristic}/{uuid[4:8]}"


class PairingAgentPolicy:
    """Decisions for ``org.bluez.Agent1`` requests on the peripheral adapter.

    A phone bonding over LE is the normal case, not a controller special case:
    before the agent existed, BlueZ had nobody to confirm the request and
    Android reported "pairing rejected by WMR Bridge". The adapter's
    ``Pairable`` flag already bounds when a new bond may be created, so the
    agent accepts every device BlueZ presents, including the WMR controllers
    that the Classic auto-pairer bonds on the same adapter.

    ``on_request`` lets the peripheral re-read the stored bond state after a
    pairing attempt; a successful phone bond then closes a boot-window
    pairability instead of leaving the adapter open indefinitely.
    """

    def __init__(self, on_request: Callable[[str], None] | None = None) -> None:
        self._on_request = on_request

    def _note(self, device: str) -> None:
        if self._on_request is not None:
            self._on_request(device)

    def _accept(self, method: str, device: str) -> bool:
        self._note(device)
        LOG.info(
            "BLE pairing agent: method=%s device=%s result=accepted",
            method,
            device,
        )
        return True

    def confirm(self, device: str) -> bool:
        return self._accept("RequestConfirmation", device)

    def authorize(self, device: str) -> bool:
        return self._accept("RequestAuthorization", device)

    def authorize_service(self, device: str, uuid: str) -> bool:
        self._note(device)
        LOG.info(
            "BLE pairing agent: method=AuthorizeService device=%s uuid=%s "
            "result=accepted",
            device,
            uuid,
        )
        return True


def log_just_works_repairing(
    path: str = str(BLUEZ_MAIN_CONF),
) -> None:
    """Explain whether BlueZ will send a Just Works re-pair to the agent.

    Under the default ``never`` bluetoothd answers the kernel's confirm hint
    ``0x01`` itself with a negative reply, so the phone fails with "Numeric
    comparison failed" before ``RequestConfirmation``/``RequestAuthorization``
    is called.  The Pi package sets ``confirm``; a source run can log this
    warning and the next capture names the missing setting.
    """

    setting = read_just_works_repairing(path)
    if setting in ("confirm", "always"):
        LOG.info(
            "BlueZ JustWorksRepairing = %s: a phone may re-pair over a stored bond",
            setting,
        )
    else:
        LOG.warning(
            "BlueZ JustWorksRepairing = %s: BlueZ rejects a phone's Just Works "
            "re-pair before the pairing agent; set JustWorksRepairing = confirm "
            "in %s and restart bluetooth",
            setting,
            path,
        )


class GattBackend(Protocol):
    """The BlueZ side of the peripheral, kept injectable for the host tests."""

    async def start(self, app: "GattApplication") -> None: ...

    async def stop(self) -> None: ...

    async def set_pairable(self, pairable: bool) -> None: ...

    async def reclaim_agent(self) -> None: ...

    async def has_le_bond(self) -> bool: ...

    async def refresh_advertisement(self) -> None: ...

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
        self.pairing_listener: Callable[[str], None] | None = None
        self.hid_suppressed = False
        self.hid_protocol_mode = HID_PROTOCOL_REPORT
        self._hid_hands = [HandInput(), HandInput()]
        self._hid_history = HidHistory()
        self._hid_last_sample = [float("-inf"), float("-inf")]
        self._hid_last_report: bytes | None = None
        self._hid_last_send = float("-inf")
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
    def hid_characteristics(self) -> tuple[GattCharacteristicSpec, ...]:
        return HID_CHARACTERISTICS

    @property
    def hid_enabled(self) -> bool:
        """True once the phone enables the input report's CCCD."""

        return HID_REPORT in self._subscribed

    @property
    def hid_report(self) -> bytes:
        return self._hid_last_report or NEUTRAL_REPORT

    @property
    def needs_decoded(self) -> bool:
        """Decode while a central watches FF12/FF14 or the HID gamepad."""

        return (
            HID_REPORT in self._subscribed
            or FF12_DECODED in self._subscribed
            or FF14_BATCHED in self._subscribed
        )

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

    @property
    def advertising_tier(self) -> str:
        """``fast`` with no Classic controller link, ``slow`` once one is up."""

        connected = any(stats.connected for stats in self.bridge.hands)
        return "slow" if connected else "fast"

    def advertisement_data(self) -> dict[str, Any]:
        """Legacy advertising data; the bridge id is repeated as service data.

        A Pi's BLE address can be random, so a scanner cannot key on it. The
        first eight bytes are enough to match the advertisement with the
        ``bridge_id`` read from FF16. Advertising is fast while there is no
        Classic controller link to protect and drops to the slow
        interval as soon as one connects. The gamepad appearance and the HID
        service UUID make Android classify the bridge as a controller.
        """

        tag = self.bridge.bridge_id.encode("ascii", "replace")[:8]
        slow = self.advertising_tier == "slow"
        return {
            "type": "peripheral",
            # BlueZ only adds the LE General Discoverable flag to the
            # advertisement when this is set. Without it the adapter still
            # advertises FF10 (ActiveInstances 1) but a phone's pairing list
            # filters the bridge out.
            "discoverable": True,
            "appearance": GAMEPAD_APPEARANCE,
            "service_uuids": [HID_SERVICE, FF10_SERVICE],
            "local_name": self.name,
            "service_data": {FF10_SERVICE: b"WMB1" + tag},
            "include_tx_power": False,
            "min_interval_ms": ADV_MIN_INTERVAL_MS if slow else ADV_FAST_MIN_INTERVAL_MS,
            "max_interval_ms": ADV_MAX_INTERVAL_MS if slow else ADV_FAST_MAX_INTERVAL_MS,
        }

    def note_pairing_request(self, device: str) -> None:
        """Tell the peripheral that BlueZ presented a device for pairing."""

        if self.pairing_listener is not None:
            self.pairing_listener(device)

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
        if uuid == HID_INFORMATION:
            return HID_INFORMATION_VALUE
        if uuid == HID_REPORT_MAP:
            offset = self._option(options, "offset", 0)
            if not isinstance(offset, int) or offset < 0:
                return b""
            return HID_REPORT_MAP_VALUE[offset:]
        if uuid == HID_REPORT:
            return self.hid_report
        if uuid == HID_PROTOCOL_MODE:
            return bytes((self.hid_protocol_mode,))
        if uuid == HID_CONTROL_POINT:
            return b""
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
        if uuid == HID_CONTROL_POINT:
            # 0 suspends the reports, 1 exits suspend. Nothing else to do: the
            # Pi only sends input reports, and the next sample resumes them.
            return RESULT_OK if value in (b"\x00", b"\x01") else RESULT_INVALID_PARAM
        if uuid == HID_PROTOCOL_MODE:
            if value not in (b"\x00", b"\x01"):
                return RESULT_INVALID_PARAM
            self.hid_protocol_mode = value[0]
            return RESULT_OK
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
        self._deliver_hid(frame, record)
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

    # ---- HID-over-GATT ---------------------------------------------------

    def _hid_stream_active(self) -> bool:
        """True while TCP or an FF11/FF12/FF14 CCCD already carries the stream."""

        return self.bridge.stream_client_active or any(
            uuid in self._subscribed for uuid in (FF11_RAW, FF12_DECODED, FF14_BATCHED)
        )

    def _hid_hand_connected(self) -> bool:
        return any(hand.connected for hand in self._hid_hands)

    def sync_hid_suppression(self) -> None:
        """Pause HID reports while another transport carries the stream.

        The phone must not get the same input twice on the same link. One
        neutral report is emitted on the transition so a button or stick held
        at the switch is released on the host.
        """

        suppressed = self._hid_stream_active()
        if suppressed == self.hid_suppressed:
            return
        self.hid_suppressed = suppressed
        if suppressed:
            self._emit_hid_neutral()
        else:
            # Resume with the next sample rather than a half-updated report.
            self._hid_last_send = float("-inf")

    def note_hand_state(self) -> None:
        """Mirror controller connect/disconnect into the HID hands."""

        changed = False
        for hand, stats in enumerate(self.bridge.hands):
            state = self._hid_hands[hand]
            if stats.connected:
                if not state.connected:
                    state.connected = True
                    changed = True
            elif state.connected:
                self._hid_hands[hand] = HandInput()
                self._hid_last_sample[hand] = float("-inf")
                changed = True
        if changed and not self._hid_hand_connected():
            self._emit_hid_neutral()

    def _expire_hid_hands(self, now: float) -> bool:
        expired = False
        for hand, state in enumerate(self._hid_hands):
            if not state.connected:
                continue
            if now - self._hid_last_sample[hand] <= HID_STALE_S:
                continue
            self._hid_hands[hand] = HandInput()
            self._hid_last_sample[hand] = float("-inf")
            expired = True
        return expired

    def _deliver_hid(self, frame: Frame, record: DecodedRecord | None) -> None:
        if not 0 <= frame.hand < 2:
            return
        now = self._clock()
        if self._expire_hid_hands(now) and not self._hid_hand_connected():
            self._emit_hid_neutral()
        if not self.bridge.hands[frame.hand].connected:
            if self._hid_hands[frame.hand].connected:
                self._hid_hands[frame.hand] = HandInput()
                self._hid_last_sample[frame.hand] = float("-inf")
                if not self._hid_hand_connected():
                    self._emit_hid_neutral()
            return
        if record is None:
            record = record_from_frame(frame)
        if record is None:
            return
        hand = self._hid_hands[frame.hand]
        hand.connected = True
        hand.buttons = record.buttons
        hand.stick = (
            clamp(record.stick[0] / 32767.0, -1.0, 1.0),
            clamp(record.stick[1] / 32767.0, -1.0, 1.0),
        )
        hand.trigger = record.trigger
        hand.trackpad = (
            clamp(record.trackpad[0] / 32767.0, -1.0, 1.0),
            clamp(record.trackpad[1] / 32767.0, -1.0, 1.0),
        )
        self._hid_last_sample[frame.hand] = now
        self.sync_hid_suppression()
        if self.hid_suppressed:
            return
        self._flush_hid(now)

    def _flush_hid(self, now: float) -> None:
        if not self.hid_enabled or not self._hid_hand_connected():
            return
        if now - self._hid_last_send < HID_MIN_INTERVAL_S:
            return
        report = build_report(
            self._hid_hands[0], self._hid_hands[1], history=self._hid_history
        )
        if report == self._hid_last_report:
            return
        self._hid_last_send = now
        self._hid_last_report = report
        self._notify(HID_REPORT, report)

    def _emit_hid_neutral(self) -> None:
        """Release held inputs once, even while the stream suppresses HID."""

        if not self.hid_enabled or self._hid_last_report in (None, NEUTRAL_REPORT):
            return
        self._hid_last_report = NEUTRAL_REPORT
        self._hid_last_send = self._clock()
        self._notify(HID_REPORT, NEUTRAL_REPORT)

    def _notify(self, uuid: str, value: bytes) -> None:
        if uuid not in self._subscribed:
            return
        self._last_value[uuid] = value
        try:
            self._emitter(uuid, value)
        except Exception:  # a closed bus must not take the TCP path down
            LOG.exception("BLE notification for %s failed", uuid)

    def start_notify(self, uuid: str) -> None:
        if uuid not in CHARACTERISTIC_BY_UUID and uuid not in HID_CHARACTERISTIC_BY_UUID:
            return
        self._subscribed.add(uuid)
        if uuid == HID_REPORT:
            # Firmware wmr_hid_reset(): forget the trigger history and send a
            # neutral report so the host starts from a known state.
            self._hid_history = HidHistory()
            self._hid_last_report = None
            self._hid_last_send = float("-inf")
            self.sync_hid_suppression()
            if not self.hid_suppressed and self._hid_hand_connected():
                self._hid_last_report = NEUTRAL_REPORT
                self._notify(HID_REPORT, NEUTRAL_REPORT)
        elif uuid in (FF11_RAW, FF12_DECODED, FF14_BATCHED):
            self.sync_hid_suppression()

    def stop_notify(self, uuid: str) -> None:
        self._subscribed.discard(uuid)
        if uuid == FF14_BATCHED:
            self._discard_batch()
        elif uuid == HID_REPORT or uuid in (FF11_RAW, FF12_DECODED, FF14_BATCHED):
            self.sync_hid_suppression()

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


def _dbus_value(value: Any) -> Any:
    """Unwrap a dbus-next Variant, tolerating plain values in tests."""

    return getattr(value, "value", value)


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

    class HidService(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.bluez.GattService1")

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return HID_SERVICE

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> "b":
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Characteristics(self) -> "ao":
            return [hid_characteristic_path(spec.uuid) for spec in HID_CHARACTERISTICS]

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
            # GattCharacteristic1.Service is the object path of the service,
            # not its UUID. The spec keeps the UUID for grouping.
            if self.spec.service == HID_SERVICE:
                return HID_SERVICE_PATH
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
            # dbus-next wraps this in a Variant of the property signature.
            self.emit_properties_changed({"Notifying": True}, [])

        @method()
        def StopNotify(self):
            app.stop_notify(self.spec.uuid)
            self.emit_properties_changed({"Notifying": False}, [])

    class Descriptor(ServiceInterface):
        def __init__(self, spec: GattDescriptorSpec, characteristic: str) -> None:
            super().__init__("org.bluez.GattDescriptor1")
            self.spec = spec
            self.characteristic = characteristic

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> "s":
            return self.spec.uuid

        @dbus_property(access=PropertyAccess.READ)
        def Characteristic(self) -> "o":
            return self.characteristic

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> "as":
            return list(self.spec.flags)

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> "ay":
            return self.spec.value

        @method()
        def ReadValue(self, options: "a{sv}") -> "ay":
            return self.spec.value

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
        def Appearance(self) -> "q":
            return GAMEPAD_APPEARANCE

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> "as":
            return list(app.advertisement_data()["service_uuids"])

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
            self.policy = PairingAgentPolicy(on_request=app.note_pairing_request)

        @method()
        def Release(self):
            LOG.info("BLE pairing agent: method=Release result=released")
            return None

        @method()
        def RequestPinCode(self, device: "o") -> "s":
            # NoInputNoOutput means BlueZ must not ask, but a peer can still
            # request legacy pairing; reject it instead of inventing a code.
            LOG.warning(
                "BLE pairing agent: method=RequestPinCode device=%s result=rejected "
                "(NoInputNoOutput)",
                device,
            )
            raise DBusError("org.bluez.Error.Rejected", PAIRING_REJECTED_HINT)

        @method()
        def DisplayPinCode(self, device: "o", pincode: "s"):
            LOG.info(
                "BLE pairing agent: method=DisplayPinCode device=%s result=ignored "
                "(no display)",
                device,
            )
            return None

        @method()
        def RequestPasskey(self, device: "o") -> "u":
            LOG.warning(
                "BLE pairing agent: method=RequestPasskey device=%s result=rejected "
                "(NoInputNoOutput)",
                device,
            )
            raise DBusError("org.bluez.Error.Rejected", PAIRING_REJECTED_HINT)

        @method()
        def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
            LOG.info(
                "BLE pairing agent: method=DisplayPasskey device=%s result=ignored "
                "(no display)",
                device,
            )
            return None

        @method()
        def RequestConfirmation(self, device: "o", passkey: "u"):
            if not self.policy.confirm(device):
                LOG.warning(
                    "BLE pairing agent: method=RequestConfirmation device=%s "
                    "result=rejected",
                    device,
                )
                raise DBusError(
                    "org.bluez.Error.Rejected", "pairing confirmation refused"
                )

        @method()
        def RequestAuthorization(self, device: "o"):
            if not self.policy.authorize(device):
                LOG.warning(
                    "BLE pairing agent: method=RequestAuthorization device=%s "
                    "result=rejected",
                    device,
                )
                raise DBusError(
                    "org.bluez.Error.Rejected", "pairing authorization refused"
                )

        @method()
        def AuthorizeService(self, device: "o", uuid: "s"):
            if not self.policy.authorize_service(device, uuid):
                LOG.warning(
                    "BLE pairing agent: method=AuthorizeService device=%s "
                    "uuid=%s result=rejected",
                    device,
                    uuid,
                )
                raise DBusError(
                    "org.bluez.Error.Rejected", "service authorization refused"
                )

        @method()
        def Cancel(self):
            LOG.warning(
                "BLE pairing agent: method=Cancel device=- result=cancelled"
            )
            return None

    return {
        "Service": Service,
        "HidService": HidService,
        "Characteristic": Characteristic,
        "Descriptor": Descriptor,
        "Advertisement": Advertisement,
        "Agent": Agent,
    }


def export_gatt_application(
    bus: Any, interfaces: dict[str, type[Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Export the FF10 and HID-over-GATT services for ``GetManagedObjects``.

    Returns the characteristic interfaces by UUID (for notifications) and the
    exported object paths (for ``unexport``). dbus-next derives the
    ``GetManagedObjects`` reply from these exports, so the service objects must
    be exported too or BlueZ answers ``RegisterApplication`` with "No valid
    service object found". BlueZ creates the CCCD for a notify characteristic,
    so the app exports only the HID Report Reference descriptor.
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

    bus.export(HID_SERVICE_PATH, interfaces["HidService"]())
    exported.append(HID_SERVICE_PATH)
    for spec in HID_CHARACTERISTICS:
        path = hid_characteristic_path(spec.uuid)
        characteristic = interfaces["Characteristic"](spec)
        bus.export(path, characteristic)
        exported.append(path)
        characteristics[spec.uuid] = characteristic
        for descriptor in spec.descriptors:
            descriptor_obj = interfaces["Descriptor"](descriptor, path)
            descriptor_obj_path = descriptor_path(path, descriptor.uuid)
            bus.export(descriptor_obj_path, descriptor_obj)
            exported.append(descriptor_obj_path)
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
        self._agent_default = False
        self._exported: list[Any] = []
        self._characteristics: dict[str, Any] = {}

    async def start(self, app: GattApplication) -> None:
        log_just_works_repairing()
        try:
            interfaces = build_dbus_interfaces(app)
            from dbus_next.aio import MessageBus
            from dbus_next.constants import BusType
        except ImportError as exc:  # pragma: no cover - depends on the host
            raise RuntimeError(
                "BLE fallback needs python3-dbus-next (apt install python3-dbus-next)"
            ) from exc

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

        try:
            await self._gatt_manager.call_register_application(APP_PATH, {})
            await self._advertising_manager.call_register_advertisement(
                ADVERTISEMENT_PATH, {}
            )
        except Exception:
            await self.stop()
            raise

    async def _claim_agent(self) -> None:
        """Register the Just Works agent and make it the adapter default.

        BlueZ rejects an incoming pair request when no default agent is set.
        The agent is scoped to the bounded BLE pairing window: a permanent
        Just Works default would let any nearby LE central bond while the
        Classic auto-pairer leaves ``Adapter1.Pairable`` on for its own scan,
        and a new bond grants encrypt-read on FF13/FF17. bluetoothctl in that
        auto-pairer registers its own agent for the controllers.
        """

        if self._agent_manager is None:
            return
        try:
            if not self._agent_registered:
                await self._agent_manager.call_register_agent(
                    AGENT_PATH, "NoInputNoOutput"
                )
                self._agent_registered = True
                LOG.info("BLE pairing agent registered (NoInputNoOutput)")
            # The Classic auto-pairer's bluetoothctl session may have claimed
            # default and dropped it; reclaim ours so a phone that bonds during
            # this window still has an agent to confirm it.
            await self._agent_manager.call_request_default_agent(AGENT_PATH)
            self._agent_default = True
            LOG.info("BLE pairing agent claimed as the adapter default")
        except Exception as exc:
            self._agent_default = False
            LOG.warning("could not register the BlueZ pairing agent: %s", exc)

    async def reclaim_agent(self) -> None:
        """Re-request default after another client dropped it.

        The Classic auto-pairer's ``bluetoothctl`` client takes default when
        the BLE pairing window is closed. If the window opens while that
        session is still running, or an external ``bluetoothctl`` session
        exits, BlueZ clears the default agent. Re-requesting default is cheap
        and keeps the whole pairability window covered.
        """

        if self._agent_manager is None or not self._agent_registered:
            return
        try:
            await self._agent_manager.call_request_default_agent(AGENT_PATH)
        except Exception as exc:
            self._agent_default = False
            LOG.warning("could not reclaim the BLE pairing agent: %s", exc)
            return
        self._agent_default = True
        LOG.info("BLE pairing agent reclaimed as the adapter default")

    async def _release_agent(self) -> None:
        """Drop the Just Works agent when the pairing window closes."""

        if self._agent_manager is None or not self._agent_registered:
            return
        try:
            await self._agent_manager.call_unregister_agent(AGENT_PATH)
            LOG.info("BLE pairing agent released")
        except Exception as exc:  # already gone is fine
            LOG.debug("unregister agent: %s", exc)
        finally:
            self._agent_registered = False
            self._agent_default = False

    def notify(self, uuid: str, value: bytes) -> None:
        characteristic = self._characteristics.get(uuid)
        if characteristic is None:
            return
        characteristic.value = bytes(value)
        # dbus-next wraps this in a Variant of the property signature; wrapping
        # here as well raised SignatureBodyMismatchError on every notification.
        characteristic.emit_properties_changed({"Value": bytes(value)}, [])

    async def set_pairable(self, pairable: bool) -> None:
        if self._adapter_interface is None:
            return
        # The agent exists only for this window, so a stray LE bond cannot be
        # completed Just Works while the adapter is otherwise left pairable.
        if pairable:
            await self._claim_agent()
        try:
            await self._adapter_interface.set_pairable(bool(pairable))
        finally:
            if not pairable:
                await self._release_agent()
        LOG.info("BLE adapter pairable: %s", "yes" if pairable else "no")

    async def has_le_bond(self) -> bool:
        """True when BlueZ holds a bond for a device that is not a WMR controller.

        The Pi bonds the two Classic controllers on the same adapter, so a bare
        ``Paired`` count cannot decide whether the LE door should stay open.
        Only a WMR controller name or the Classic HID UUID proves a bond is a
        controller bond. ``Class`` cannot be used: BlueZ sets it for every
        BR/EDR-capable device, so a dual-mode phone's LE bond carries both
        ``Class`` and ``AddressType=public``. An unproven bond is treated as
        the phone's LE bond, so the door closes rather than stays open.
        """

        bus = self._bus
        if bus is None:
            return False
        try:
            introspection = await bus.introspect("org.bluez", "/")
            manager_object = bus.get_proxy_object("org.bluez", "/", introspection)
            manager = manager_object.get_interface(
                "org.freedesktop.DBus.ObjectManager"
            )
            objects = await manager.call_get_managed_objects()
        except Exception as exc:
            # Fail closed: without the bond list, only the boot/manual window
            # may open pairability.
            LOG.warning("could not read BlueZ bonds: %s", exc)
            return True
        for interfaces in objects.values():
            properties = interfaces.get("org.bluez.Device1")
            if not properties or not _dbus_value(properties.get("Paired")):
                continue
            name = str(
                _dbus_value(properties.get("Name"))
                or _dbus_value(properties.get("Alias"))
                or ""
            )
            if name.strip().casefold() in WMR_NAMES:
                continue
            # A dual-mode phone's LE bond carries Class and AddressType=public
            # too, so Class is not evidence of a Classic bond. Only the Classic
            # HID profile proves this bond belongs to a controller; anything
            # unproven closes the door.
            uuids = _dbus_value(properties.get("UUIDs")) or []
            if CLASSIC_HID_UUID in {
                str(_dbus_value(uuid)).casefold() for uuid in uuids
            }:
                continue
            return True
        return False

    async def refresh_advertisement(self) -> None:
        """Re-register the advertisement so BlueZ re-reads its intervals.

        BlueZ snapshots an ``LEAdvertisement1`` object when it is registered;
        changing MinInterval/MaxInterval on the exported object afterwards does
        not reach the controller. A brief unregister/register applies the new
        two-tier interval.
        """

        if self._advertising_manager is None or self._bus is None:
            return
        try:
            await self._advertising_manager.call_unregister_advertisement(
                ADVERTISEMENT_PATH
            )
        except Exception as exc:  # already gone is fine
            LOG.debug("unregister advertisement for interval change: %s", exc)
        # Registration can race the unregister inside bluetoothd. Retry a few
        # times instead of leaving the adapter with no advertisement at all.
        error: Exception | None = None
        for attempt in range(3):
            try:
                await self._advertising_manager.call_register_advertisement(
                    ADVERTISEMENT_PATH, {}
                )
                return
            except Exception as exc:
                error = exc
                await asyncio.sleep(0.2 * (attempt + 1))
        raise RuntimeError("could not re-register the advertisement") from error

    async def stop(self) -> None:
        bus = self._bus
        if bus is None:
            return
        await self._release_agent()
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
    """Attach a BlueZ GATT application to a :class:`Bridge`.

    Pairability follows the verdict: the adapter is pairable while it
    has no LE bond, for ``pairing_window_s`` after boot, or while a manual
    window is open. ``continuous_pairing`` is the opt-in ESP32-style mode that
    keeps the adapter pairable regardless of stored bonds.
    """

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
        pairing_window_s: float = DEFAULT_PAIRING_WINDOW_S,
        continuous_pairing: bool = False,
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
        self.continuous_pairing = continuous_pairing
        self._clock = clock
        self._started = False
        self._boot_deadline: float | None = None
        self._manual_deadline: float | None = None
        self._bond_grace_deadline: float | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._bond_task: asyncio.Task[None] | None = None
        self._advertising_task: asyncio.Task[None] | None = None
        self._agent_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._advertising_dirty = False
        self._advertised_tier = self.app.advertising_tier
        self._pairable_applied: bool | None = None
        self._pairable_reason = "not started"

    @property
    def started(self) -> bool:
        return self._started

    def pairing_window_open(self) -> bool:
        """True while the adapter is pairable."""

        return self.app.pairing_open

    def manual_pairing_window_open(self) -> bool:
        """True while a manually opened window is holding the adapter pairable.

        Separate from :meth:`pairing_window_open`, which includes the stored-bond
        rule: with no bond stored the adapter stays pairable after the manual
        window closes.
        """

        deadline = self._manual_deadline
        return deadline is not None and self._clock() < deadline

    def pairable_reason(self) -> str:
        """Reason for the last computed pairability decision."""

        return self._pairable_reason

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        await self.backend.start(self.app)
        self.app.set_emitter(self.backend.notify)
        self.app.bridge.add_sink(self.app)
        self.app.bridge.add_hand_listener(self._on_hand_state_changed)
        self.app.bridge.add_stream_listener(self.app.sync_hid_suppression)
        self.app.pairing_listener = self._on_pairing_request
        self._started = True
        self._boot_deadline = (
            self._clock() + self.pairing_window_s
            if self.pairing_window_s > 0
            else None
        )
        self._advertised_tier = self.app.advertising_tier
        # A fresh start must write Adapter1.Pairable once even when the rule
        # computes the same False value the FF13 shadow started with.
        self._pairable_applied = None
        await self.refresh_pairing_state()
        self._schedule_pairing_refresh()
        LOG.info(
            "BLE fallback advertising FF10 as %s (bridge_id %s)",
            self.app.name,
            self.app.bridge.bridge_id,
        )

    # ---- advertising interval -------------------------------------------

    def _on_hand_state_changed(self) -> None:
        self.app.note_hand_state()
        # Coalesce rather than drop: a connect/disconnect that lands while a
        # tier switch is in flight must still be applied afterwards.
        self._advertising_dirty = True
        if self._advertising_task is not None and not self._advertising_task.done():
            return
        self._advertising_task = asyncio.create_task(self._drain_advertising())

    async def _drain_advertising(self) -> None:
        try:
            while self._advertising_dirty:
                self._advertising_dirty = False
                await self.refresh_advertising()
        finally:
            self._advertising_task = None

    async def refresh_advertising(self) -> None:
        """Re-register until the advertisement matches the current tier.

        The tier is re-read after every ``await`` so a connect/disconnect that
        happens while BlueZ is re-registering is not left on the old interval.
        """

        while True:
            tier = self.app.advertising_tier
            if tier == self._advertised_tier:
                return
            try:
                await self.backend.refresh_advertisement()
            except Exception as exc:
                LOG.warning("could not apply the %s advertising tier: %s", tier, exc)
                # Unknown state: force the next hand event to re-register
                # rather than trusting a shadow that may not match BlueZ.
                self._advertised_tier = None
                return
            self._advertised_tier = tier
            LOG.info("BLE advertising interval: %s tier", tier)

    # ---- pairability -----------------------------------------------------

    async def open_pairing_window(self, seconds: float | None = None) -> None:
        """Open a manual pairing window, independent of stored bonds."""

        duration = self.pairing_window_s if seconds is None else max(0.0, seconds)
        if duration > 0:
            self._manual_deadline = self._clock() + duration
            LOG.info("BLE pairing window opened manually for %g s", duration)
        else:
            self._manual_deadline = None
        await self.refresh_pairing_state()
        self._schedule_pairing_refresh()

    async def close_pairing_window(self) -> None:
        """End the manual/boot windows; pairability then follows the bond rule."""

        self._manual_deadline = None
        self._boot_deadline = None
        self._bond_grace_deadline = None
        self._cancel_pairing_task()
        await self.refresh_pairing_state()

    def _on_pairing_request(self, _device: str) -> None:
        """Re-check for a stored LE bond shortly after BlueZ presents a device."""

        # Keep the agent through the rest of the phone's exchange. The bond is
        # stored before service discovery and the HID Report CCCD write, so a
        # bond-triggered close must not release the agent immediately.
        self._bond_grace_deadline = self._clock() + PAIRING_AGENT_GRACE_S
        if self._bond_task is not None and not self._bond_task.done():
            return
        self._bond_task = asyncio.create_task(self._refresh_after_bond())

    async def reclaim_agent(self) -> None:
        """Restore the Just Works agent after a controller scan took default."""

        if not self._started or not self.app.pairing_open:
            return
        try:
            await self.backend.reclaim_agent()
        except Exception as exc:
            LOG.warning("could not reclaim the BLE pairing agent: %s", exc)

    def request_agent_reclaim(self) -> None:
        """Thread-safe request from the Classic auto-pairer's worker thread.

        A ``bluetoothctl`` scan that runs while the BLE window is closed takes
        default with its own agent, and BlueZ drops that default when the
        client exits. Scheduling the reclaim here (rather than in the pairer)
        keeps the D-Bus work on the event loop that owns the bus connection.
        """

        loop = self._loop
        if loop is None or not self._started or not self.app.pairing_open:
            return
        loop.call_soon_threadsafe(self._schedule_agent_reclaim)

    def _schedule_agent_reclaim(self) -> None:
        if not self._started or not self.app.pairing_open:
            return
        if self._agent_task is not None and not self._agent_task.done():
            return
        self._agent_task = asyncio.create_task(self.reclaim_agent())

    async def _refresh_after_bond(self) -> None:
        """Re-read bonds a few times; BlueZ stores the key after the agent call."""

        try:
            for delay in (1.0, 2.0, 7.0):
                await asyncio.sleep(delay)
                await self.refresh_pairing_state()
                if not self.app.pairing_open:
                    break
        except asyncio.CancelledError:
            raise
        self._schedule_pairing_refresh()

    async def refresh_pairing_state(self) -> None:
        """Apply the stored-bond and deadline rules to Adapter1.Pairable."""

        if not self._started:
            return
        pairable, reason = await self._pairing_decision()
        self._pairable_reason = reason
        # Compare against what was last written to the adapter, not the FF13
        # shadow: that shadow starts False, which is also a valid computed
        # value, and skipping the write would leave Adapter1.Pairable stale.
        if pairable == self._pairable_applied:
            return
        try:
            await self.backend.set_pairable(pairable)
        except Exception as exc:
            LOG.warning("could not set adapter pairability to %s: %s", pairable, exc)
            return
        self._pairable_applied = pairable
        self.app.pairing_open = pairable
        hint = (
            "; run 'wmr-bridge pair' to open a manual window"
            if not pairable and reason == "an LE bond is stored"
            else ""
        )
        LOG.info(
            "BLE pairing window %s (%s); Adapter1.Pairable -> %s%s",
            "open" if pairable else "closed",
            reason,
            "on" if pairable else "off",
            hint,
        )

    async def _pairing_decision(self) -> tuple[bool, str]:
        """Return the pairable state and the reason it was chosen."""

        if self.continuous_pairing:
            return True, "continuous pairing enabled"
        now = self._clock()
        if self._manual_deadline is not None and now < self._manual_deadline:
            return True, f"manual window, {self._manual_deadline - now:.0f} s left"
        if self._boot_deadline is not None and now < self._boot_deadline:
            return True, f"boot window, {self._boot_deadline - now:.0f} s left"
        if (
            self._bond_grace_deadline is not None
            and now < self._bond_grace_deadline
        ):
            return True, "phone pairing in progress"
        try:
            bonded = await self.backend.has_le_bond()
        except Exception as exc:
            LOG.warning("could not read the LE bond state: %s", exc)
            return False, "BlueZ bond list unreadable"
        if bonded:
            return False, "an LE bond is stored"
        return True, "no LE bond stored"

    def _next_deadline(self) -> float | None:
        now = self._clock()
        deadlines = [
            deadline
            for deadline in (
                self._boot_deadline,
                self._manual_deadline,
                self._bond_grace_deadline,
            )
            if deadline is not None and deadline > now
        ]
        return min(deadlines) if deadlines else None

    def _schedule_pairing_refresh(self) -> None:
        self._cancel_pairing_task()
        deadline = self._next_deadline()
        if deadline is None:
            return
        self._pairing_task = asyncio.create_task(
            self._refresh_pairing_after(max(0.001, deadline - self._clock()))
        )

    async def _refresh_pairing_after(self, seconds: float) -> None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        self._pairing_task = None
        await self.refresh_pairing_state()
        self._schedule_pairing_refresh()

    def _cancel_pairing_task(self) -> None:
        if self._pairing_task is not None:
            self._pairing_task.cancel()
            self._pairing_task = None

    def _cancel_tasks(self) -> None:
        self._cancel_pairing_task()
        for task in (self._bond_task, self._advertising_task, self._agent_task):
            if task is not None:
                task.cancel()
        self._bond_task = None
        self._advertising_task = None
        self._agent_task = None

    async def close(self) -> None:
        if self._started:
            self.app.bridge.remove_hand_listener(self._on_hand_state_changed)
            self.app.bridge.remove_stream_listener(self.app.sync_hid_suppression)
            self.app.pairing_listener = None
            self._cancel_tasks()
            self._bond_grace_deadline = None
            self.app.pairing_open = False
            try:
                await self.backend.set_pairable(False)
                self._pairable_applied = False
                LOG.info(
                    "BLE pairing window closed (peripheral stopped); "
                    "Adapter1.Pairable -> off"
                )
            except Exception as exc:
                LOG.debug("could not disable pairing: %s", exc)
            self.app.bridge.remove_sink(self.app)
            self.app.set_emitter(lambda _uuid, _value: None)
            self.app.stop_notify(FF14_BATCHED)
            self._started = False
        else:
            self._cancel_tasks()
        self._loop = None
        await self.backend.stop()
