"""BlueZ BLE GATT fallback: FF10 registration, metadata and per-CCCD streaming."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import unittest
from pathlib import Path

from wmr_bridge.__main__ import _build_parser
from wmr_bridge.ble_gatt import (
    AGENT_PATH,
    APP_PATH,
    ADV_FAST_MAX_INTERVAL_MS,
    ADV_FAST_MIN_INTERVAL_MS,
    ADV_MAX_INTERVAL_MS,
    ADV_MIN_INTERVAL_MS,
    CHARACTERISTICS,
    CHARACTERISTIC_BY_UUID,
    FF10_SERVICE,
    FF11_RAW,
    FF12_DECODED,
    FF13_CONFIG,
    FF14_BATCHED,
    FF15_ECHO,
    FF16_VERSION,
    FF17_CONTROL,
    GAMEPAD_APPEARANCE,
    HID_CHARACTERISTICS,
    HID_CHARACTERISTIC_BY_UUID,
    HID_CONTROL_POINT,
    HID_INFORMATION,
    HID_INFORMATION_VALUE,
    HID_PROTOCOL_MODE,
    HID_REPORT,
    HID_REPORT_MAP,
    HID_REPORT_REFERENCE,
    HID_SERVICE_PATH,
    HID_SERVICE,
    PAIRING_AGENT_GRACE_S,
    PAIRING_REJECTED_HINT,
    RESULT_INVALID_PARAM,
    RESULT_NOTHING_TO_DO,
    RESULT_NOT_CONNECTED,
    SERVICE_PATH,
    BluezGattPeripheral,
    DbusGattBackend,
    GattApplication,
    PairingAgentPolicy,
    build_dbus_interfaces,
    characteristic_path,
    descriptor_path,
    export_gatt_application,
    hid_characteristic_path,
)
from wmr_bridge.bridge import Bridge
from wmr_bridge.hid_gamepad import (
    NEUTRAL_REPORT,
    REPORT_ID,
    REPORT_MAP,
    REPORT_SIZE,
    HandInput,
    build_report,
)
from wmr_bridge.inputs import read_capture
from wmr_bridge.decode import record_from_frame
from wmr_bridge.protocol import Frame, Reassembler, parse_batch, unpack_record

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
SAMPLE_DATA = read_capture(SAMPLES)[0][1]
HAS_DBUS_NEXT = importlib.util.find_spec("dbus_next") is not None


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    def __init__(self, pairable: bool = False, le_bond: bool = False) -> None:
        self.notifications: list[tuple[str, bytes]] = []
        self.pairable: bool | None = pairable
        self.pairable_calls: list[bool] = []
        self.le_bond = le_bond
        self.advertisement_refreshes = 0
        self.agent_reclaims = 0
        self.started = False
        self.app: GattApplication | None = None

    async def start(self, app: GattApplication) -> None:
        self.app = app
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def set_pairable(self, pairable: bool) -> None:
        self.pairable = pairable
        self.pairable_calls.append(pairable)

    async def has_le_bond(self) -> bool:
        return self.le_bond

    async def reclaim_agent(self) -> None:
        self.agent_reclaims += 1

    async def refresh_advertisement(self) -> None:
        self.advertisement_refreshes += 1

    def notify(self, uuid: str, value: bytes) -> None:
        self.notifications.append((uuid, value))

    def values(self, uuid: str) -> list[bytes]:
        return [value for item, value in self.notifications if item == uuid]


def _application(**overrides) -> GattApplication:
    options = {
        "hosts": ["192.0.2.10", "fd00::10"],
        "port": 8765,
        "path": "/wmr",
        "name": "WMR Bridge",
        "batch_hz": 50.0,
        "clock": FakeClock(),
    }
    options.update(overrides)
    bridge = options.pop("bridge", Bridge(token=options.pop("token", None)))
    return GattApplication(bridge, **options)


class RegistrationTests(unittest.TestCase):
    def test_ff10_characteristics_have_unique_paths(self) -> None:
        paths = [characteristic_path(spec.uuid) for spec in CHARACTERISTICS]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(len(CHARACTERISTIC_BY_UUID), len(CHARACTERISTICS))
        self.assertTrue(all(path.startswith(SERVICE_PATH + "/") for path in paths))
        self.assertEqual(
            {spec.uuid for spec in CHARACTERISTICS},
            {FF11_RAW, FF12_DECODED, FF14_BATCHED, FF15_ECHO, FF13_CONFIG, FF16_VERSION, FF17_CONTROL},
        )

    def test_ff16_is_plaintext_and_ff13_requires_encryption(self) -> None:
        application = _application(token="s3cret")
        ff16 = CHARACTERISTIC_BY_UUID[FF16_VERSION]
        ff13 = CHARACTERISTIC_BY_UUID[FF13_CONFIG]
        self.assertNotIn("encrypt-read", ff16.flags)
        self.assertIn("encrypt-read", ff13.flags)

    def test_hid_characteristics_cover_hogp_and_are_encrypted(self) -> None:
        self.assertEqual(
            {spec.uuid for spec in HID_CHARACTERISTICS},
            {
                HID_INFORMATION,
                HID_REPORT_MAP,
                HID_CONTROL_POINT,
                HID_REPORT,
                HID_PROTOCOL_MODE,
            },
        )
        self.assertEqual(len(HID_CHARACTERISTIC_BY_UUID), len(HID_CHARACTERISTICS))
        for spec in HID_CHARACTERISTICS:
            self.assertEqual(spec.service, HID_SERVICE)
            self.assertTrue(
                any(flag.startswith("encrypt-") for flag in spec.flags), spec.uuid
            )
        report = HID_CHARACTERISTIC_BY_UUID[HID_REPORT]
        self.assertIn("notify", report.flags)
        self.assertEqual(len(report.descriptors), 1)
        reference = report.descriptors[0]
        self.assertEqual(reference.uuid, HID_REPORT_REFERENCE)
        self.assertEqual(reference.value, bytes((REPORT_ID, 0x01)))

    def test_hid_paths_are_unique_and_separate_from_ff10(self) -> None:
        paths = [hid_characteristic_path(spec.uuid) for spec in HID_CHARACTERISTICS]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertTrue(all(path.startswith(HID_SERVICE_PATH + "/") for path in paths))
        self.assertFalse(set(paths) & {characteristic_path(spec.uuid) for spec in CHARACTERISTICS})
        report_path = hid_characteristic_path(HID_REPORT)
        self.assertEqual(
            descriptor_path(report_path, HID_REPORT_REFERENCE),
            f"{HID_SERVICE_PATH}/2a4d/2908",
        )

    def test_hid_information_matches_the_firmware(self) -> None:
        self.assertEqual(HID_INFORMATION_VALUE, bytes((0x11, 0x01, 0x00, 0x02)))
        self.assertEqual(len(REPORT_MAP), 91)

    def test_advertisement_is_a_gamepad_and_carries_the_bridge_id_prefix(self) -> None:
        bridge = Bridge(bridge_id="wmr-pi-abc12345")
        data = _application(bridge=bridge).advertisement_data()
        self.assertTrue(data["discoverable"])
        self.assertEqual(data["appearance"], GAMEPAD_APPEARANCE)
        self.assertEqual(data["service_uuids"], [HID_SERVICE, FF10_SERVICE])
        self.assertEqual(data["local_name"], "WMR Bridge")
        self.assertEqual(data["service_data"][FF10_SERVICE], b"WMB1wmr-pi-a")
        self.assertEqual(data["min_interval_ms"], ADV_FAST_MIN_INTERVAL_MS)
        self.assertEqual(data["max_interval_ms"], ADV_FAST_MAX_INTERVAL_MS)

    def test_advertising_steps_down_when_a_controller_connects(self) -> None:
        bridge = Bridge()
        application = _application(bridge=bridge)
        bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        data = application.advertisement_data()
        self.assertEqual(data["min_interval_ms"], ADV_MIN_INTERVAL_MS)
        self.assertEqual(data["max_interval_ms"], ADV_MAX_INTERVAL_MS)
        bridge.set_hand_connected(0, False)
        data = application.advertisement_data()
        self.assertEqual(data["min_interval_ms"], ADV_FAST_MIN_INTERVAL_MS)
        self.assertEqual(data["max_interval_ms"], ADV_FAST_MAX_INTERVAL_MS)


class PairingAgentTests(unittest.TestCase):
    """A phone bond must not be filtered out as a non-controller device."""

    def test_agent_accepts_a_phone_central_and_a_wmr_controller(self) -> None:
        seen: list[str] = []
        policy = PairingAgentPolicy(on_request=seen.append)
        phone = "/org/bluez/hci0/dev_D4_3A_2C_11_22_33"
        controller = "/org/bluez/hci0/dev_98_B6_E9_44_55_66"
        for device in (phone, controller):
            self.assertTrue(policy.confirm(device))
            self.assertTrue(policy.authorize(device))
            self.assertTrue(policy.authorize_service(device, FF10_SERVICE))
        self.assertEqual(seen, [phone, phone, phone, controller, controller, controller])

    def test_rejection_hint_names_how_to_open_a_window(self) -> None:
        self.assertIn("wmr-bridge pair", PAIRING_REJECTED_HINT)
        self.assertIn("remove the old bond", PAIRING_REJECTED_HINT)
        self.assertNotIn("Companion", PAIRING_REJECTED_HINT)


class FakeAdapterInterface:
    def __init__(self) -> None:
        self.pairable: bool | None = None

    async def set_pairable(self, pairable: bool) -> None:
        self.pairable = pairable


class FakeAgentManager:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str]] = []
        self.defaults: list[str] = []
        self.unregistered: list[str] = []

    async def call_register_agent(self, path: str, capability: str) -> None:
        self.registered.append((path, capability))

    async def call_request_default_agent(self, path: str) -> None:
        self.defaults.append(path)

    async def call_unregister_agent(self, path: str) -> None:
        self.unregistered.append(path)


class FakeObjectManager:
    def __init__(self, objects: dict) -> None:
        self.objects = objects

    async def call_get_managed_objects(self) -> dict:
        return self.objects


class FakeManagerObject:
    def __init__(self, objects: dict) -> None:
        self.objects = objects

    def get_interface(self, _name: str) -> FakeObjectManager:
        return FakeObjectManager(self.objects)


class FakeBondBus:
    def __init__(self, objects: dict | None = None) -> None:
        self.objects = objects or {}

    async def introspect(self, _service: str, _path: str) -> object:
        return object()

    def get_proxy_object(
        self, _service: str, _path: str, _introspection: object
    ) -> FakeManagerObject:
        return FakeManagerObject(self.objects)


def _bond(name: str, address_type: str, device_class: int) -> dict:
    return {
        "org.bluez.Device1": {
            "Paired": True,
            "Name": name,
            "AddressType": address_type,
            "Class": device_class,
        }
    }


class BondDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_controller_bond_does_not_count_as_an_le_bond(self) -> None:
        backend = DbusGattBackend()
        backend._bus = FakeBondBus(
            {"/org/bluez/hci0/dev_AA": _bond("Motion Controller - Left", "public", 0x0508)}
        )
        self.assertFalse(await backend.has_le_bond())

    async def test_classic_only_bonds_leave_the_le_door_open(self) -> None:
        keyboard = _bond("Pi Keyboard", "public", 0x2540)
        keyboard["org.bluez.Device1"]["UUIDs"] = [
            "00001124-0000-1000-8000-00805f9b34fb"
        ]
        backend = DbusGattBackend()
        backend._bus = FakeBondBus(
            {
                "/org/bluez/hci0/dev_AA": _bond(
                    "Motion Controller - Left", "public", 0x0508
                ),
                "/org/bluez/hci0/dev_BB": _bond(
                    "Motion Controller - Right", "public", 0x0540
                ),
                # A Classic keyboard bond: non-peripheral major class, proven
                # Classic by the HID profile UUID rather than by its Class.
                "/org/bluez/hci0/dev_CC": keyboard,
            }
        )
        self.assertFalse(await backend.has_le_bond())

    async def test_classic_hid_uuid_without_a_class_is_not_an_le_bond(self) -> None:
        entry = _bond("2C:4D:79:C8:C9:CC", "public", 0)
        entry["org.bluez.Device1"]["UUIDs"] = [
            "00001124-0000-1000-8000-00805f9b34fb"
        ]
        backend = DbusGattBackend()
        backend._bus = FakeBondBus({"/org/bluez/hci0/dev_AA": entry})
        self.assertFalse(await backend.has_le_bond())

    async def test_a_phone_bond_counts_as_an_le_bond(self) -> None:
        backend = DbusGattBackend()
        backend._bus = FakeBondBus(
            {"/org/bluez/hci0/dev_BB": _bond("Pixel 11 Pro", "random", 0x5A020C)}
        )
        self.assertTrue(await backend.has_le_bond())

    async def test_a_public_dual_mode_phone_bond_is_an_le_bond(self) -> None:
        # The Pixel's LE identity address is its BR/EDR address, so BlueZ
        # reports AddressType=public and a Class from an earlier inquiry. That
        # is still the phone's LE bond, not a Classic controller bond.
        backend = DbusGattBackend()
        backend._bus = FakeBondBus(
            {"/org/bluez/hci0/dev_B0": _bond("Pixel 11 Pro", "public", 0x5A020C)}
        )
        self.assertTrue(await backend.has_le_bond())

    async def test_an_unreadable_bond_list_fails_closed(self) -> None:
        class BrokenBus:
            async def introspect(self, *_args):
                raise RuntimeError("no system bus")

        backend = DbusGattBackend()
        backend._bus = BrokenBus()
        with self.assertLogs("wmr_bridge.ble_gatt", level="WARNING"):
            self.assertTrue(await backend.has_le_bond())


class BackendAgentTests(unittest.IsolatedAsyncioTestCase):
    """The Just Works agent lives only while the BLE pairing window is open."""

    def _backend(self) -> tuple[DbusGattBackend, FakeAdapterInterface, FakeAgentManager]:
        backend = DbusGattBackend()
        adapter = FakeAdapterInterface()
        agent_manager = FakeAgentManager()
        backend._adapter_interface = adapter
        backend._agent_manager = agent_manager
        return backend, adapter, agent_manager

    async def test_window_registers_default_and_close_unregisters(self) -> None:
        backend, adapter, agent_manager = self._backend()
        self.assertFalse(backend._agent_registered)
        await backend.set_pairable(True)
        self.assertEqual(agent_manager.registered, [(AGENT_PATH, "NoInputNoOutput")])
        self.assertEqual(agent_manager.defaults, [AGENT_PATH])
        self.assertTrue(adapter.pairable)
        self.assertTrue(backend._agent_registered)

        await backend.set_pairable(False)
        self.assertEqual(agent_manager.unregistered, [AGENT_PATH])
        self.assertFalse(adapter.pairable)
        self.assertFalse(backend._agent_registered)

    async def test_reopening_the_window_reclaims_default_without_reregistering(self) -> None:
        backend, _adapter, agent_manager = self._backend()
        await backend.set_pairable(True)
        await backend.set_pairable(True)
        self.assertEqual(len(agent_manager.registered), 1)
        self.assertEqual(agent_manager.defaults, [AGENT_PATH, AGENT_PATH])

    async def test_closing_without_an_open_window_leaves_the_agent_alone(self) -> None:
        backend, adapter, agent_manager = self._backend()
        await backend.set_pairable(False)
        self.assertEqual(agent_manager.unregistered, [])
        self.assertFalse(adapter.pairable)

    async def test_reclaim_without_a_registered_agent_does_nothing(self) -> None:
        backend, _adapter, agent_manager = self._backend()
        await backend.reclaim_agent()
        self.assertEqual(agent_manager.defaults, [])

    async def test_reclaim_requests_default_again_after_a_scan_took_it(self) -> None:
        backend, _adapter, agent_manager = self._backend()
        await backend.set_pairable(True)
        agent_manager.defaults.clear()
        await backend.reclaim_agent()
        self.assertEqual(agent_manager.defaults, [AGENT_PATH])
        self.assertEqual(len(agent_manager.registered), 1)
        self.assertTrue(backend._agent_default)

    async def test_a_reclaim_failure_is_logged_and_clears_the_default_flag(self) -> None:
        backend, _adapter, agent_manager = self._backend()

        async def fail(_path: str) -> None:
            raise RuntimeError("no default agent permission")

        agent_manager.call_request_default_agent = fail  # type: ignore[method-assign]
        with self.assertLogs("wmr_bridge.ble_gatt", level="WARNING"):
            await backend.set_pairable(True)
            await backend.reclaim_agent()
        self.assertFalse(backend._agent_default)


class FlakyAdvertisingManager:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0
        self.unregistrations = 0

    async def call_unregister_advertisement(self, _path: str) -> None:
        self.unregistrations += 1

    async def call_register_advertisement(self, _path: str, _options: dict) -> None:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise RuntimeError("advertisement still registered")


class AdvertisementRegistrationTests(unittest.IsolatedAsyncioTestCase):
    def _backend(
        self, failures: int
    ) -> tuple[DbusGattBackend, FlakyAdvertisingManager]:
        backend = DbusGattBackend()
        manager = FlakyAdvertisingManager(failures)
        backend._bus = object()
        backend._advertising_manager = manager
        return backend, manager

    async def test_a_transient_register_failure_is_retried(self) -> None:
        backend, manager = self._backend(failures=2)
        await backend.refresh_advertisement()
        self.assertEqual(manager.attempts, 3)
        self.assertEqual(manager.unregistrations, 1)

    async def test_a_persistent_register_failure_is_reported(self) -> None:
        backend, manager = self._backend(failures=99)
        with self.assertRaises(RuntimeError):
            await backend.refresh_advertisement()
        self.assertEqual(manager.attempts, 3)


@unittest.skipUnless(HAS_DBUS_NEXT, "python3-dbus-next is not installed")
class DbusNextInterfaceTests(unittest.TestCase):
    """Build the real dbus-next interfaces; a bad annotation fails at class creation."""

    def setUp(self) -> None:
        self.interfaces = build_dbus_interfaces(_application())

    def test_service_interfaces_build_on_the_installed_dbus_next(self) -> None:
        service = self.interfaces["Service"]()
        self.assertEqual(service.UUID, FF10_SERVICE)
        self.assertTrue(service.Primary)
        self.assertEqual(
            service.Characteristics,
            [characteristic_path(spec.uuid) for spec in CHARACTERISTICS],
        )
        self.assertEqual(
            {prop.name for prop in service.introspect().properties},
            {"UUID", "Primary", "Characteristics"},
        )
        hid_service = self.interfaces["HidService"]()
        self.assertEqual(hid_service.UUID, HID_SERVICE)
        self.assertTrue(hid_service.Primary)
        self.assertEqual(
            hid_service.Characteristics,
            [hid_characteristic_path(spec.uuid) for spec in HID_CHARACTERISTICS],
        )
        advertisement = self.interfaces["Advertisement"]()
        for spec in CHARACTERISTICS:
            characteristic = self.interfaces["Characteristic"](spec)
            self.assertEqual(characteristic.UUID, spec.uuid)
            self.assertEqual(characteristic.Service, SERVICE_PATH)
            self.assertEqual(characteristic.Flags, list(spec.flags))
            methods = {
                method.name: method for method in characteristic.introspect().methods
            }
            self.assertEqual(
                set(methods), {"ReadValue", "WriteValue", "StartNotify", "StopNotify"}
            )
            # dbus-next rejects a "None" return annotation, so no out argument here.
            self.assertEqual(methods["WriteValue"].out_args, [])
        for spec in HID_CHARACTERISTICS:
            characteristic = self.interfaces["Characteristic"](spec)
            self.assertEqual(characteristic.UUID, spec.uuid)
            self.assertEqual(characteristic.Service, HID_SERVICE_PATH)
            self.assertEqual(characteristic.Flags, list(spec.flags))
            for descriptor in spec.descriptors:
                interface = self.interfaces["Descriptor"](
                    descriptor, hid_characteristic_path(spec.uuid)
                )
                self.assertEqual(interface.UUID, descriptor.uuid)
                self.assertEqual(
                    interface.Characteristic, hid_characteristic_path(spec.uuid)
                )
                self.assertEqual(interface.Flags, list(descriptor.flags))
                self.assertEqual(interface.Value, descriptor.value)
                self.assertEqual(
                    {method.name for method in interface.introspect().methods},
                    {"ReadValue"},
                )
        self.assertEqual(advertisement.Type, "peripheral")
        self.assertTrue(advertisement.Discoverable)
        self.assertEqual(advertisement.Appearance, GAMEPAD_APPEARANCE)
        self.assertEqual(advertisement.ServiceUUIDs, [HID_SERVICE, FF10_SERVICE])
        self.assertEqual(advertisement.LocalName, "WMR Bridge")
        self.assertEqual(advertisement.ServiceData[FF10_SERVICE].signature, "ay")

    def test_agent_accepts_phone_and_controller_pairing(self) -> None:
        agent = self.interfaces["Agent"]()
        self.assertEqual(agent.introspect().name, "org.bluez.Agent1")
        self.assertEqual(
            {method.name for method in agent.introspect().methods},
            {
                "Release",
                "RequestPinCode",
                "DisplayPinCode",
                "RequestPasskey",
                "DisplayPasskey",
                "RequestConfirmation",
                "RequestAuthorization",
                "AuthorizeService",
                "Cancel",
            },
        )
        phone = "/org/bluez/hci0/dev_D4_3A_2C_11_22_33"
        controller = "/org/bluez/hci0/dev_98_B6_E9_44_55_66"
        for device in (phone, controller):
            self.assertIsNone(agent.RequestConfirmation(device, 0))
            self.assertIsNone(agent.RequestAuthorization(device))
            self.assertIsNone(agent.AuthorizeService(device, FF10_SERVICE))


@unittest.skipUnless(HAS_DBUS_NEXT, "python3-dbus-next is not installed")
class ManagedObjectsTests(unittest.TestCase):
    """Drive dbus-next's own ``GetManagedObjects`` over the exported FF10 tree.

    BlueZ registers an application by calling ``GetManagedObjects`` on the app
    root and rejecting the app unless the reply holds a ``GattService1``.
    dbus-next 0.2.3 answers that call from the interfaces exported on the bus,
    not from any method on the app object, so this exercises the same code a
    live ``RegisterApplication`` does. A session bus is not guaranteed on the
    host, so only the export registry and the reply handler are used.
    """

    def _managed_objects(self) -> dict:
        from dbus_next import Message
        from dbus_next.message_bus import BaseMessageBus

        class OfflineBus:
            """The slice of ``MessageBus`` that export and the handler touch."""

            def __init__(self) -> None:
                self._path_exports: dict = {}
                self._name_owners: dict = {}
                self._disconnected = True

            def export(self, path, interface) -> None:
                BaseMessageBus.export(self, path, interface)

            def _emit_interface_added(self, path, interface) -> None:
                pass

            def send(self, message) -> None:
                pass

        class ReplySink:
            def __init__(self) -> None:
                self.messages: list = []

            def __call__(self, message) -> None:
                self.messages.append(message)

            def send_error(self, error) -> None:
                raise error

        bus = OfflineBus()
        interface_classes = build_dbus_interfaces(_application())
        export_gatt_application(bus, interface_classes)
        request = Message(
            serial=1,
            path=APP_PATH,
            interface="org.freedesktop.DBus.ObjectManager",
            member="GetManagedObjects",
            signature="",
        )
        sink = ReplySink()
        BaseMessageBus._default_get_managed_objects_handler(bus, request, sink)
        self.assertEqual(len(sink.messages), 1)
        self.assertEqual(sink.messages[0].signature, "a{oa{sa{sv}}}")
        return sink.messages[0].body[0]

    def test_get_managed_objects_exposes_the_gatt_service(self) -> None:
        objects = self._managed_objects()
        # BlueZ: "No valid service object found" until this entry exists.
        service = objects[SERVICE_PATH]["org.bluez.GattService1"]
        self.assertEqual(service["UUID"].value, FF10_SERVICE)
        self.assertIs(service["Primary"].value, True)
        self.assertEqual(
            service["Characteristics"].value,
            [characteristic_path(spec.uuid) for spec in CHARACTERISTICS],
        )
        for spec in CHARACTERISTICS:
            properties = objects[characteristic_path(spec.uuid)][
                "org.bluez.GattCharacteristic1"
            ]
            self.assertEqual(properties["UUID"].value, spec.uuid)
            self.assertEqual(properties["Service"].value, SERVICE_PATH)
            self.assertEqual(properties["Flags"].value, list(spec.flags))

    def test_get_managed_objects_exposes_the_hid_service(self) -> None:
        objects = self._managed_objects()
        service = objects[HID_SERVICE_PATH]["org.bluez.GattService1"]
        self.assertEqual(service["UUID"].value, HID_SERVICE)
        self.assertIs(service["Primary"].value, True)
        self.assertEqual(
            service["Characteristics"].value,
            [hid_characteristic_path(spec.uuid) for spec in HID_CHARACTERISTICS],
        )
        for spec in HID_CHARACTERISTICS:
            path = hid_characteristic_path(spec.uuid)
            properties = objects[path]["org.bluez.GattCharacteristic1"]
            self.assertEqual(properties["UUID"].value, spec.uuid)
            self.assertEqual(properties["Service"].value, HID_SERVICE_PATH)
            self.assertEqual(properties["Flags"].value, list(spec.flags))

    def test_app_exports_the_report_reference_but_no_cccd(self) -> None:
        objects = self._managed_objects()
        descriptors = [
            path for path in objects if "org.bluez.GattDescriptor1" in objects[path]
        ]
        report_path = hid_characteristic_path(HID_REPORT)
        reference_path = descriptor_path(report_path, HID_REPORT_REFERENCE)
        self.assertEqual(descriptors, [reference_path])
        properties = objects[reference_path]["org.bluez.GattDescriptor1"]
        self.assertEqual(properties["UUID"].value, HID_REPORT_REFERENCE)
        self.assertEqual(properties["Characteristic"].value, report_path)
        self.assertEqual(properties["Value"].value, bytes((REPORT_ID, 0x01)))
        self.assertFalse(
            any(path.endswith("/2902") for path in descriptors),
            "BlueZ creates the CCCD itself",
        )


class MetadataTests(unittest.TestCase):
    def test_ff16_document_has_lan_endpoint_without_the_token(self) -> None:
        application = _application(token="s3cret")
        raw = application.ff16_value()
        document = json.loads(raw)
        self.assertEqual(document["type"], "wmr_ble_endpoint")
        self.assertEqual(document["bridge_id"], application.bridge.bridge_id)
        self.assertEqual(document["hosts"], ["192.0.2.10", "fd00::10"])
        self.assertEqual(document["port"], 8765)
        self.assertEqual(document["path"], "/wmr")
        self.assertTrue(document["authRequired"])
        self.assertNotIn("s3cret", raw.decode("utf-8"))
        self.assertNotIn("token", document)

    def test_ff13_document_carries_the_session_token(self) -> None:
        application = _application(token="s3cret")
        document = json.loads(application.ff13_value())
        self.assertEqual(document["type"], "wmr_ble_session")
        self.assertEqual(document["bridge_id"], application.bridge.bridge_id)
        self.assertEqual(document["token"], "s3cret")

    def test_status_without_a_token_marks_auth_not_required(self) -> None:
        application = _application()
        self.assertFalse(json.loads(application.ff16_value())["authRequired"])
        self.assertNotIn("token", json.loads(application.ff13_value()))

    def test_ff16_hosts_are_sampled_on_each_read(self) -> None:
        hosts: list[str] = []
        application = _application(hosts=lambda: list(hosts))
        self.assertEqual(json.loads(application.ff16_value())["hosts"], [])
        hosts.append("192.0.2.20")
        self.assertEqual(json.loads(application.ff16_value())["hosts"], ["192.0.2.20"])

    def test_cli_ble_is_on_by_default_and_can_be_disabled(self) -> None:
        parser = _build_parser()
        self.assertTrue(parser.parse_args([]).ble)
        self.assertFalse(parser.parse_args(["--no-ble"]).ble)
        # --ble-fallback stays accepted as a deprecated no-op alias.
        args = parser.parse_args(["--ble-fallback"])
        self.assertTrue(args.ble)
        self.assertTrue(args.ble_fallback)
        self.assertEqual(args.ble_adapter, "hci0")
        self.assertEqual(args.ble_pairing_window, 180.0)
        self.assertFalse(args.ble_continuous_pairing)
        self.assertTrue(
            parser.parse_args(["--ble-continuous-pairing"]).ble_continuous_pairing
        )


class FakeSubscriber:
    """A TCP client with a stream but no socket, to test cross-transport gating."""

    stream = "raw"
    closed = False

    def deliver(self, _frame, _record) -> None:
        pass


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.clock = FakeClock()
        self.bridge = Bridge()
        self.backend = FakeBackend()
        self.peripheral = BluezGattPeripheral(
            self.bridge,
            backend=self.backend,
            hosts=["192.0.2.10"],
            name="WMR Bridge",
            clock=self.clock,
            pairing_window_s=120.0,
        )
        await self.peripheral.start()
        self.app = self.peripheral.app

    async def asyncTearDown(self) -> None:
        await self.peripheral.close()

    async def test_no_cccd_means_no_ble_airtime(self) -> None:
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(self.backend.notifications, [])

    # ---- HID-over-GATT ------------------------------------------------

    def _sample(self, buttons: int = 0) -> bytes:
        data = bytearray(SAMPLE_DATA)
        data[2] |= buttons
        return bytes(data)

    def _expected_hid_report(self, data: bytes) -> bytes:
        record = record_from_frame(
            Frame(hand=0, sequence=1, data=data, rx_time_us=0)
        )
        assert record is not None
        hand = HandInput(
            connected=True,
            buttons=record.buttons,
            stick=(record.stick[0] / 32767.0, record.stick[1] / 32767.0),
            trigger=record.trigger,
            trackpad=(record.trackpad[0] / 32767.0, record.trackpad[1] / 32767.0),
        )
        return build_report(hand, HandInput())

    async def test_hid_needs_a_controller_and_the_report_cccd(self) -> None:
        self.app.start_notify(HID_REPORT)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(self.backend.values(HID_REPORT), [])

        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        self.bridge.ingest(0, SAMPLE_DATA)
        values = self.backend.values(HID_REPORT)
        self.assertEqual(len(values), 1)
        self.assertEqual(len(values[0]), REPORT_SIZE)
        self.assertEqual(values[0], self._expected_hid_report(SAMPLE_DATA))

    async def test_tcp_stream_suppresses_hid_after_one_neutral_report(self) -> None:
        pressed = self._sample(0x08)  # squeeze -> R1
        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        self.app.start_notify(HID_REPORT)
        self.clock.advance(0.02)
        self.bridge.ingest(0, pressed)
        before = len(self.backend.values(HID_REPORT))
        self.assertEqual(self.backend.values(HID_REPORT)[-1], self._expected_hid_report(pressed))

        self.bridge.subscribers.add(FakeSubscriber())  # type: ignore[arg-type]
        self.bridge._note_stream_state()
        after = self.backend.values(HID_REPORT)
        self.assertEqual(len(after), before + 1)
        self.assertEqual(after[-1], NEUTRAL_REPORT)

        # While TCP is active a new input must not reach the HID host.
        self.clock.advance(0.02)
        self.bridge.ingest(0, self._sample(0x02))
        self.assertEqual(len(self.backend.values(HID_REPORT)), before + 1)

        # Removing the TCP client resumes with the next sample.
        self.bridge.subscribers.clear()
        self.bridge._note_stream_state()
        self.clock.advance(0.02)
        self.bridge.ingest(0, self._sample(0x02))
        self.assertGreater(len(self.backend.values(HID_REPORT)), before + 1)

    async def test_ff11_cccd_suppresses_hid_until_released(self) -> None:
        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        self.app.start_notify(HID_REPORT)
        self.clock.advance(0.02)
        self.bridge.ingest(0, self._sample(0x08))
        before = len(self.backend.values(HID_REPORT))

        self.app.start_notify(FF11_RAW)
        values = self.backend.values(HID_REPORT)
        self.assertEqual(len(values), before + 1)
        self.assertEqual(values[-1], NEUTRAL_REPORT)

        self.clock.advance(0.02)
        self.bridge.ingest(0, self._sample(0x02))
        self.assertEqual(len(self.backend.values(HID_REPORT)), before + 1)

        self.app.stop_notify(FF11_RAW)
        self.clock.advance(0.02)
        self.bridge.ingest(0, self._sample(0x02))
        self.assertGreater(len(self.backend.values(HID_REPORT)), before + 1)

    async def test_hid_reads_follow_hogp(self) -> None:
        self.assertEqual(self.app.read_value(HID_INFORMATION), HID_INFORMATION_VALUE)
        self.assertEqual(self.app.read_value(HID_REPORT_MAP), REPORT_MAP)
        self.assertEqual(
            self.app.read_value(HID_REPORT_MAP, {"offset": 22}), REPORT_MAP[22:]
        )
        self.assertEqual(self.app.read_value(HID_PROTOCOL_MODE), b"\x01")
        self.assertEqual(self.app.write_value(HID_PROTOCOL_MODE, b"\x00"), 0)
        self.assertEqual(self.app.read_value(HID_PROTOCOL_MODE), b"\x00")
        self.assertEqual(self.app.write_value(HID_PROTOCOL_MODE, b"\x02"), RESULT_INVALID_PARAM)
        self.assertEqual(self.app.write_value(HID_CONTROL_POINT, b"\x00"), 0)
        self.assertEqual(self.app.write_value(HID_CONTROL_POINT, b"\x02"), RESULT_INVALID_PARAM)
        self.assertEqual(self.app.read_value(HID_REPORT), NEUTRAL_REPORT)

    async def test_ff11_streams_raw_fragments_when_subscribed(self) -> None:
        self.app.start_notify(FF11_RAW)
        self.bridge.ingest(0, SAMPLE_DATA)
        values = self.backend.values(FF11_RAW)
        self.assertGreaterEqual(len(values), 1)
        reassembler = Reassembler()
        frame = None
        for value in values:
            frame = reassembler.push(value) or frame
        self.assertIsNotNone(frame)
        self.assertEqual(frame.hand, 0)
        self.assertEqual(frame.data, SAMPLE_DATA)

    async def test_ff12_streams_one_record_per_frame(self) -> None:
        self.app.read_value(FF16_VERSION, {"mtu": 247})
        self.app.start_notify(FF12_DECODED)
        self.bridge.ingest(0, SAMPLE_DATA)
        values = self.backend.values(FF12_DECODED)
        self.assertEqual(len(values), 1)
        self.assertEqual(len(values[0]), 56)
        self.assertEqual(unpack_record(values[0]).hand, 0)

    async def test_tcp_subscriber_does_not_silence_ble(self) -> None:
        self.bridge.subscribers.add(FakeSubscriber())  # type: ignore[arg-type]
        self.app.start_notify(FF11_RAW)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertTrue(self.backend.values(FF11_RAW))
        self.assertEqual(self.bridge.status()["clients"], 1)

    async def test_ff14_batches_are_capped_at_50_hz(self) -> None:
        self.app.read_value(FF16_VERSION, {"mtu": 517})
        self.app.start_notify(FF14_BATCHED)
        # The first record flushes at once; later ones wait for the next 20 ms slot.
        for _ in range(5):
            self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(len(self.backend.values(FF14_BATCHED)), 1)
        self.assertEqual(len(parse_batch(self.backend.values(FF14_BATCHED)[0])), 1)
        self.clock.advance(0.019)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(len(self.backend.values(FF14_BATCHED)), 1)
        self.clock.advance(0.001)
        self.app.flush_batches()
        self.assertEqual(len(self.backend.values(FF14_BATCHED)), 2)
        self.assertEqual(len(parse_batch(self.backend.values(FF14_BATCHED)[1])), 5)

    async def test_notifications_are_sized_to_the_negotiated_mtu(self) -> None:
        self.app.start_notify(FF12_DECODED)
        self.app.start_notify(FF14_BATCHED)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(self.backend.values(FF12_DECODED), [])
        self.assertEqual(self.backend.values(FF14_BATCHED), [])
        self.assertEqual(self.app.dropped_records, 2)

        self.app.read_value(FF16_VERSION, {"mtu": 59})
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(len(self.backend.values(FF12_DECODED)), 1)
        self.assertEqual(self.backend.values(FF14_BATCHED), [])
        self.assertEqual(self.app.dropped_records, 3)

        self.app.read_value(FF16_VERSION, {"mtu": 61})
        self.clock.advance(0.02)
        self.bridge.ingest(0, SAMPLE_DATA)
        batches = [parse_batch(value) for value in self.backend.values(FF14_BATCHED)]
        self.assertEqual([len(batch) for batch in batches], [1])

    async def test_ff14_batch_is_sized_to_the_mtu(self) -> None:
        self.app.read_value(FF16_VERSION, {"mtu": 185})  # 3 records per batch
        self.app.start_notify(FF14_BATCHED)
        for _ in range(5):
            self.bridge.ingest(0, SAMPLE_DATA)
        batches = [parse_batch(value) for value in self.backend.values(FF14_BATCHED)]
        self.assertEqual([len(batch) for batch in batches], [1, 3])
        self.app.flush_batches()
        batches = [parse_batch(value) for value in self.backend.values(FF14_BATCHED)]
        self.assertEqual([len(batch) for batch in batches], [1, 3, 1])

    async def test_pending_batch_flushes_at_the_protocol_limit(self) -> None:
        self.app.read_value(FF16_VERSION, {"mtu": 517})
        self.app.start_notify(FF14_BATCHED)
        for _ in range(20):
            self.bridge.ingest(0, SAMPLE_DATA)
        batches = [parse_batch(value) for value in self.backend.values(FF14_BATCHED)]
        self.assertTrue(all(1 <= len(batch) <= 9 for batch in batches))
        self.app.flush_batches()
        batches = [parse_batch(value) for value in self.backend.values(FF14_BATCHED)]
        self.assertEqual(sum(len(batch) for batch in batches), 20)
        self.assertEqual(self.app.dropped_records, 0)

    async def test_ff11_fragment_size_follows_the_mtu(self) -> None:
        self.assertEqual(self.app.fragment_size, 20)
        self.app.read_value(FF16_VERSION, {"mtu": 185})
        self.assertEqual(self.app.fragment_size, 182)
        self.app.read_value(FF16_VERSION, {"mtu": 517})
        self.assertEqual(self.app.fragment_size, 244)
        self.app.start_notify(FF11_RAW)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertTrue(all(len(value) <= 244 for value in self.backend.values(FF11_RAW)))

    async def test_ff11_read_is_the_status_block(self) -> None:
        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        self.bridge.ingest(0, SAMPLE_DATA)
        status = self.app.read_value(FF11_RAW)
        self.assertEqual(len(status), 16)
        self.assertEqual(status[0], 1)
        self.assertEqual(status[1] & 0x01, 0x01)
        self.assertEqual(int.from_bytes(status[8:12], "little"), 1)

    async def test_stop_notify_stops_the_stream(self) -> None:
        self.app.read_value(FF16_VERSION, {"mtu": 247})
        self.app.start_notify(FF12_DECODED)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.app.stop_notify(FF12_DECODED)
        self.bridge.ingest(0, SAMPLE_DATA)
        self.assertEqual(len(self.backend.values(FF12_DECODED)), 1)

    async def test_ff15_echo_answers_only_when_subscribed(self) -> None:
        self.assertEqual(
            self.app.write_value(FF15_ECHO, b"\x01\x00\x34\x12"), RESULT_NOT_CONNECTED
        )
        self.assertEqual(self.backend.values(FF15_ECHO), [])
        self.app.start_notify(FF15_ECHO)
        self.assertEqual(self.app.write_value(FF15_ECHO, b"\x01\x00\x34\x12"), 0)
        answers = self.backend.values(FF15_ECHO)
        self.assertEqual(len(answers), 1)
        self.assertEqual(len(answers[0]), 20)
        self.assertEqual(answers[0][:4], b"\x01\x00\x34\x12")

    async def test_ff17_status_and_write_result(self) -> None:
        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        status = self.app.ff17_status()
        self.assertEqual(len(status), 20)
        self.assertEqual(status[0], 1)
        self.assertEqual(status[1] & 0x01, 0x01)
        self.assertEqual(status[4:7], bytes([0xDD, 0xEE, 0xFF]))
        self.assertEqual(self.app.write_value(FF17_CONTROL, b"\x09"), RESULT_INVALID_PARAM)
        self.assertEqual(self.app.ff17_status()[17:19], bytes([0x09, RESULT_INVALID_PARAM]))
        self.assertEqual(self.app.write_value(FF17_CONTROL, b"\x01"), RESULT_NOTHING_TO_DO)
        self.assertEqual(self.app.ff17_status()[17:19], bytes([0x01, RESULT_NOTHING_TO_DO]))

    async def test_ff17_write_notifies_the_result(self) -> None:
        self.app.start_notify(FF17_CONTROL)
        self.app.write_value(FF17_CONTROL, b"\x09")
        values = self.backend.values(FF17_CONTROL)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0][17:19], bytes([0x09, RESULT_INVALID_PARAM]))

    async def test_pairing_window_closes_when_an_le_bond_is_stored(self) -> None:
        self.backend.le_bond = True
        self.assertTrue(self.app.pairing_open)
        self.assertEqual(self.backend.pairable, True)
        await self.peripheral.close_pairing_window()
        self.assertFalse(self.app.pairing_open)
        self.assertEqual(self.backend.pairable, False)

    async def test_no_bond_keeps_the_adapter_pairable_after_boot(self) -> None:
        self.clock.advance(121)
        await self.peripheral.refresh_pairing_state()
        self.assertTrue(self.app.pairing_open)
        self.assertTrue(self.backend.pairable)

    async def test_classic_only_bonds_leave_the_adapter_pairable(self) -> None:
        class ClassicOnlyBackend(FakeBackend):
            async def has_le_bond(self) -> bool:
                backend = DbusGattBackend()
                backend._bus = FakeBondBus(
                    {
                        "/org/bluez/hci0/dev_AA": _bond(
                            "Motion Controller - Left", "public", 0x0508
                        ),
                        "/org/bluez/hci0/dev_BB": _bond(
                            "Motion Controller - Right", "public", 0x0540
                        ),
                    }
                )
                return await backend.has_le_bond()

        backend = ClassicOnlyBackend()
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        await peripheral.start()
        try:
            self.assertTrue(peripheral.app.pairing_open)
            self.assertTrue(backend.pairable)
        finally:
            await peripheral.close()

    async def test_pairability_change_logs_the_reason(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        with self.assertLogs("wmr_bridge.ble_gatt", level="INFO") as logs:
            await peripheral.start()
        output = "\n".join(logs.output)
        self.assertIn("BLE pairing window closed", output)
        self.assertIn("an LE bond is stored", output)
        self.assertIn("Adapter1.Pairable -> off", output)
        await peripheral.close()

    async def test_manual_window_logs_its_reason_and_turns_bondable_on(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        await peripheral.start()
        try:
            with self.assertLogs("wmr_bridge.ble_gatt", level="INFO") as logs:
                await peripheral.open_pairing_window(60)
            output = "\n".join(logs.output)
            self.assertIn("opened manually for 60 s", output)
            self.assertIn("manual window", output)
            self.assertIn("Adapter1.Pairable -> on", output)
            self.assertTrue(backend.pairable)
        finally:
            await peripheral.close()

    async def test_an_le_bond_closes_pairability_after_boot(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=120.0,
        )
        await peripheral.start()
        self.assertTrue(backend.pairable)
        self.clock.advance(121)
        await peripheral.refresh_pairing_state()
        self.assertFalse(peripheral.app.pairing_open)
        self.assertFalse(backend.pairable)
        await peripheral.close()

    async def test_a_stored_bond_is_applied_at_start_with_no_boot_window(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        await peripheral.start()
        try:
            self.assertFalse(peripheral.app.pairing_open)
            self.assertEqual(backend.pairable_calls, [False])
            self.assertFalse(backend.pairable)
        finally:
            await peripheral.close()

    async def test_a_manual_window_reopens_with_a_stored_bond(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=30.0,
        )
        await peripheral.start()
        self.clock.advance(31)
        await peripheral.refresh_pairing_state()
        self.assertFalse(backend.pairable)
        await peripheral.open_pairing_window(60)
        self.assertTrue(backend.pairable)
        self.clock.advance(61)
        await peripheral.refresh_pairing_state()
        self.assertFalse(backend.pairable)
        await peripheral.close()

    async def test_continuous_pairing_ignores_stored_bonds(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=30.0,
            continuous_pairing=True,
        )
        await peripheral.start()
        self.clock.advance(31)
        await peripheral.refresh_pairing_state()
        self.assertTrue(peripheral.app.pairing_open)
        self.assertTrue(backend.pairable)
        await peripheral.close()

    async def test_pairing_window_closes_on_an_already_pairable_adapter(self) -> None:
        backend = FakeBackend(pairable=True, le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=120.0,
        )
        await peripheral.start()
        self.assertTrue(backend.pairable)
        await peripheral.close_pairing_window()
        self.assertFalse(peripheral.app.pairing_open)
        self.assertFalse(backend.pairable)
        await peripheral.close()

    async def test_pairing_window_is_released_when_the_peripheral_closes(self) -> None:
        self.assertEqual(self.backend.pairable_calls, [True])
        await self.peripheral.close()
        self.assertEqual(self.backend.pairable_calls, [True, False])

    async def test_reclaim_agent_restores_default_while_the_window_is_open(self) -> None:
        await self.peripheral.reclaim_agent()
        self.assertEqual(self.backend.agent_reclaims, 1)

    async def test_an_agent_request_keeps_the_window_open_for_a_grace_period(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        await peripheral.start()
        try:
            self.assertFalse(peripheral.app.pairing_open)
            peripheral._on_pairing_request("/org/bluez/hci0/dev_AA")
            await peripheral.refresh_pairing_state()
            self.assertTrue(peripheral.app.pairing_open)
            self.assertTrue(backend.pairable)
            self.clock.advance(PAIRING_AGENT_GRACE_S + 0.1)
            await peripheral.refresh_pairing_state()
            self.assertFalse(peripheral.app.pairing_open)
            self.assertFalse(backend.pairable)
        finally:
            await peripheral.close()

    async def test_each_agent_request_extends_the_grace_period(self) -> None:
        backend = FakeBackend(le_bond=True)
        peripheral = BluezGattPeripheral(
            Bridge(),
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=0.0,
        )
        await peripheral.start()
        try:
            peripheral._on_pairing_request("/org/bluez/hci0/dev_AA")
            self.clock.advance(PAIRING_AGENT_GRACE_S - 1.0)
            peripheral._on_pairing_request("/org/bluez/hci0/dev_AA")
            self.clock.advance(2.0)
            await peripheral.refresh_pairing_state()
            self.assertTrue(peripheral.app.pairing_open)
            self.clock.advance(PAIRING_AGENT_GRACE_S)
            await peripheral.refresh_pairing_state()
            self.assertFalse(peripheral.app.pairing_open)
        finally:
            await peripheral.close()

    async def test_closing_the_window_clears_the_grace_period(self) -> None:
        self.backend.le_bond = True
        self.peripheral._on_pairing_request("/org/bluez/hci0/dev_AA")
        await self.peripheral.close_pairing_window()
        self.assertFalse(self.peripheral.app.pairing_open)

    async def test_reclaim_agent_does_nothing_when_the_window_is_closed(self) -> None:
        self.backend.le_bond = True
        await self.peripheral.close_pairing_window()
        self.assertFalse(self.peripheral.app.pairing_open)
        await self.peripheral.reclaim_agent()
        self.assertEqual(self.backend.agent_reclaims, 0)

    async def test_a_threaded_reclaim_is_scheduled_on_the_event_loop(self) -> None:
        self.peripheral.request_agent_reclaim()
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertEqual(self.backend.agent_reclaims, 1)

    async def test_a_threaded_reclaim_is_ignored_after_the_window_closes(self) -> None:
        self.backend.le_bond = True
        await self.peripheral.close_pairing_window()
        self.peripheral.request_agent_reclaim()
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertEqual(self.backend.agent_reclaims, 0)

    async def test_advertising_is_reregistered_when_the_tier_changes(self) -> None:
        self.bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
        await asyncio.sleep(0)
        self.assertEqual(self.backend.advertisement_refreshes, 1)
        self.bridge.set_hand_connected(0, False)
        await asyncio.sleep(0)
        self.assertEqual(self.backend.advertisement_refreshes, 2)

    async def test_a_disconnect_during_a_tier_switch_is_not_lost(self) -> None:
        class GatedBackend(FakeBackend):
            gate: asyncio.Event

            async def refresh_advertisement(self) -> None:
                await self.gate.wait()
                await super().refresh_advertisement()

        gate = asyncio.Event()
        backend = GatedBackend()
        backend.gate = gate
        bridge = Bridge()
        peripheral = BluezGattPeripheral(
            bridge,
            backend=backend,
            hosts=["192.0.2.10"],
            clock=self.clock,
            pairing_window_s=120.0,
        )
        await peripheral.start()
        try:
            bridge.set_hand_connected(0, True, "AA:BB:CC:DD:EE:FF")
            await asyncio.sleep(0)
            # Disconnect while the fast->slow re-register is still in flight.
            bridge.set_hand_connected(0, False)
            gate.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(backend.advertisement_refreshes, 2)
            self.assertEqual(peripheral._advertised_tier, "fast")
        finally:
            await peripheral.close()

    async def test_ff16_is_read_without_a_bond(self) -> None:
        raw = self.app.read_value(FF16_VERSION)
        self.assertEqual(json.loads(raw)["bridge_id"], self.bridge.bridge_id)
        self.assertEqual(self.app.read_value(FF13_CONFIG), self.app.ff13_value())
