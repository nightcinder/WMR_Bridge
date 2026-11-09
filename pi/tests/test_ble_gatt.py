"""BlueZ BLE GATT fallback: FF10 registration, metadata and per-CCCD streaming."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from wmr_bridge.__main__ import _build_parser
from wmr_bridge.ble_gatt import (
    APP_PATH,
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
    RESULT_INVALID_PARAM,
    RESULT_NOTHING_TO_DO,
    RESULT_NOT_CONNECTED,
    BluezGattPeripheral,
    GattApplication,
    characteristic_path,
    descriptor_path,
)
from wmr_bridge.bridge import Bridge
from wmr_bridge.inputs import read_capture
from wmr_bridge.protocol import Reassembler, parse_batch, unpack_record

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
SAMPLE_DATA = read_capture(SAMPLES)[0][1]


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, bytes]] = []
        self.pairable: bool | None = None
        self.started = False
        self.app: GattApplication | None = None

    async def start(self, app: GattApplication) -> None:
        self.app = app
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def set_pairable(self, pairable: bool) -> None:
        self.pairable = pairable

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
    def test_ff10_service_exposes_ff11_through_ff17(self) -> None:
        objects = _application().managed_objects()
        self.assertIn("org.freedesktop.DBus.ObjectManager", objects[APP_PATH])
        service = objects["/org/wmr/bridge/service/ff10"]["org.bluez.GattService1"]
        self.assertEqual(service["UUID"], FF10_SERVICE)
        self.assertTrue(service["Primary"])
        expected = {characteristic_path(spec.uuid) for spec in CHARACTERISTICS}
        self.assertEqual(set(service["Characteristics"]), expected)
        self.assertEqual(set(service["Characteristics"]), set(expected))
        exposed = {
            properties["UUID"]
            for path, interfaces in objects.items()
            if "org.bluez.GattCharacteristic1" in interfaces
            for properties in interfaces.values()
        }
        self.assertEqual(
            exposed,
            {FF11_RAW, FF12_DECODED, FF14_BATCHED, FF15_ECHO, FF13_CONFIG, FF16_VERSION, FF17_CONTROL},
        )
        for spec in CHARACTERISTICS:
            path = characteristic_path(spec.uuid)
            properties = objects[path]["org.bluez.GattCharacteristic1"]
            self.assertEqual(properties["Flags"], list(spec.flags))
            if spec.has_cccd:
                self.assertEqual(properties["Descriptors"], [descriptor_path(spec.uuid)])
                descriptor = objects[descriptor_path(spec.uuid)]["org.bluez.GattDescriptor1"]
                self.assertEqual(descriptor["Characteristic"], path)
                self.assertEqual(descriptor["UUID"], "00002902-0000-1000-8000-00805f9b34fb")
            else:
                self.assertNotIn("Descriptors", properties)

    def test_ff16_is_plaintext_and_ff13_requires_encryption(self) -> None:
        application = _application(token="s3cret")
        ff16 = CHARACTERISTIC_BY_UUID[FF16_VERSION]
        ff13 = CHARACTERISTIC_BY_UUID[FF13_CONFIG]
        self.assertNotIn("encrypt-read", ff16.flags)
        self.assertIn("encrypt-read", ff13.flags)

    def test_advertisement_carries_the_bridge_id_prefix(self) -> None:
        bridge = Bridge(bridge_id="wmr-pi-abc12345")
        data = _application(bridge=bridge).advertisement_data()
        self.assertEqual(data["service_uuids"], [FF10_SERVICE])
        self.assertEqual(data["local_name"], "WMR Bridge")
        self.assertEqual(data["service_data"][FF10_SERVICE], b"WMB1wmr-pi-a")


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

    def test_cli_ble_fallback_is_off_by_default(self) -> None:
        parser = _build_parser()
        self.assertFalse(parser.parse_args([]).ble_fallback)
        args = parser.parse_args(["--ble-fallback"])
        self.assertTrue(args.ble_fallback)
        self.assertEqual(args.ble_adapter, "hci0")
        self.assertEqual(args.ble_pairing_window, 120.0)


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

    async def test_stop_notify_stops_the_stream(self) -> None:
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

    async def test_pairing_window_closes(self) -> None:
        self.assertTrue(self.app.pairing_open)
        self.assertEqual(self.backend.pairable, True)
        await self.peripheral.close_pairing_window()
        self.assertFalse(self.app.pairing_open)
        self.assertEqual(self.backend.pairable, False)

    async def test_ff16_is_read_without_a_bond(self) -> None:
        raw = self.app.read_value(FF16_VERSION)
        self.assertEqual(json.loads(raw)["bridge_id"], self.bridge.bridge_id)
        self.assertEqual(self.app.read_value(FF13_CONFIG), self.app.ff13_value())
