"""CLI safety rails: bind default, token requirement and token-file errors."""

from __future__ import annotations

import asyncio
import io
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from wmr_bridge.__main__ import (
    _build_parser,
    _format_status,
    _is_loopback_bind,
    _local_addresses,
    _pair_window,
    _parse_cli_args,
    _print_status,
    _read_token,
    _require_auth_for_bind,
    _run,
    _validate_idle_timeouts,
    main,
)
from wmr_bridge.bluez import (
    BluezAdapter,
    BluezDevice,
    BluezStatus,
    HidrawDevice,
    set_device_blocked,
)
from wmr_bridge.bridge import Bridge
from wmr_bridge.server import BridgeServer

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"
WIFI_INFO = {
    "iface": "wlan0",
    "connected": True,
    "freqMhz": 2437,
    "band": "2.4GHz",
    "supports5Ghz": True,
    "contentionWarning": True,
    "warning": (
        "Wi-Fi is connected on 2.4 GHz, which shares the radio with "
        "Bluetooth; prefer a 5 GHz network."
    ),
}
UPLINK_INFO = {"iface": "eth0", "kind": "ethernet", "addresses": ["192.0.2.60"]}


def _args(bind: str = "127.0.0.1", insecure: bool = False):
    return _build_parser().parse_args(["--bind", bind, *(["--insecure-no-token"] if insecure else [])])


class BindTests(unittest.TestCase):
    def test_bind_defaults_to_loopback(self) -> None:
        args = _build_parser().parse_args([])
        self.assertEqual(args.bind, "127.0.0.1")
        self.assertFalse(args.insecure_no_token)
        self.assertTrue(_is_loopback_bind(args.bind))

    def test_loopback_detection(self) -> None:
        self.assertTrue(_is_loopback_bind("127.0.0.1"))
        self.assertTrue(_is_loopback_bind("::1"))
        self.assertFalse(_is_loopback_bind("0.0.0.0"))
        self.assertFalse(_is_loopback_bind("::"))
        self.assertFalse(_is_loopback_bind("192.0.2.10"))

    def test_refuses_non_loopback_without_token(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            _require_auth_for_bind(_args("0.0.0.0"), None)
        message = str(caught.exception)
        self.assertIn("0.0.0.0", message)
        self.assertIn("--token-file", message)
        self.assertIn("--token", message)
        self.assertIn("--insecure-no-token", message)

    def test_loopback_without_token_is_allowed(self) -> None:
        _require_auth_for_bind(_args("127.0.0.1"), None)

    def test_token_allows_non_loopback(self) -> None:
        _require_auth_for_bind(_args("0.0.0.0"), "s3cret")

    def test_insecure_flag_allows_non_loopback(self) -> None:
        _require_auth_for_bind(_args("0.0.0.0", insecure=True), None)


class LocalAddressTests(unittest.TestCase):
    def test_hostname_loopback_does_not_hide_the_lan_address(self) -> None:
        class Probe:
            address: tuple[str, int] = ("192.0.2.50", 12345)

            def __enter__(self) -> "Probe":
                return self

            def __exit__(self, *_exc: object) -> bool:
                return False

            def connect(self, _address: tuple[str, int]) -> None:
                pass

            def getsockname(self) -> tuple[str, int]:
                return self.address

        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.1.1", 0))]
        with mock.patch("wmr_bridge.__main__.socket.getaddrinfo", return_value=infos), \
             mock.patch("wmr_bridge.__main__.interface_ipv4", return_value=None), \
             mock.patch("wmr_bridge.__main__.socket.socket", return_value=Probe()):
            self.assertEqual(_local_addresses(), ["127.0.0.1", "192.0.2.50"])

    def test_eth0_leads_the_lan_addresses(self) -> None:
        infos = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.50", 0))]

        def address(iface: str) -> str | None:
            return {"eth0": "192.0.2.60", "wlan0": "192.0.2.50"}.get(iface)

        with mock.patch("wmr_bridge.__main__.socket.getaddrinfo", return_value=infos), \
             mock.patch("wmr_bridge.__main__.interface_ipv4", side_effect=address), \
             mock.patch("wmr_bridge.__main__.socket.socket", side_effect=OSError):
            self.assertEqual(
                _local_addresses(), ["192.0.2.60", "192.0.2.50", "127.0.0.1"]
            )

    def test_probe_failure_still_returns_loopback(self) -> None:
        with mock.patch("wmr_bridge.__main__.socket.getaddrinfo", side_effect=OSError), \
             mock.patch("wmr_bridge.__main__.interface_ipv4", return_value=None), \
             mock.patch("wmr_bridge.__main__.socket.socket", side_effect=OSError):
            self.assertEqual(_local_addresses(), ["127.0.0.1"])


class TokenFileTests(unittest.TestCase):
    def test_unreadable_token_file_names_path_uid_and_cause(self) -> None:
        path = "/etc/wmr-bridge/token"
        with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied", path)):
            with self.assertRaises(SystemExit) as caught:
                _read_token(_build_parser().parse_args(["--token-file", path]))
        message = str(caught.exception)
        self.assertIn(path, message)
        self.assertIn(f"uid {os.getuid()}", message)
        self.assertIn("root-owned", message)
        self.assertIn("chown wmr:wmr", message)


class ReplayOnceTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_once_stops_the_daemon(self) -> None:
        args = _build_parser().parse_args(
            [
                "--replay",
                str(SAMPLES),
                "--replay-rate",
                "10000",
                "--replay-once",
                "--port",
                "0",
            ]
        )
        await asyncio.wait_for(_run(args), 5)


class AutoPairIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def test_pairer_keeps_its_own_poll_interval(self) -> None:
        captured: dict[str, object] = {}

        class FakePairer:
            def __init__(self, bridge, **kwargs) -> None:
                captured.update(kwargs)

            def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        class FakeManager:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        args = _build_parser().parse_args(["--duration", "0.05", "--port", "0"])
        with mock.patch("wmr_bridge.__main__.BluezAutoPairer", FakePairer), \
             mock.patch("wmr_bridge.__main__.HidrawManager", FakeManager):
            await asyncio.wait_for(_run(args), 5)

        self.assertNotIn("poll_interval", captured)


class BlePeripheralStartupTests(unittest.IsolatedAsyncioTestCase):
    async def _run_with_ble(
        self, argv: list[str]
    ) -> tuple[list[str], dict[str, object], list[str]]:
        events: list[str] = []
        captured: dict[str, object] = {}

        class FakeBle:
            def __init__(self, _bridge, **kwargs) -> None:
                captured.update(kwargs)

            async def start(self) -> None:
                events.append("start")

            async def close(self) -> None:
                events.append("close")

        class FakeManager:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        args = _build_parser().parse_args(
            ["--duration", "0.05", "--port", "0", "--no-auto-pair", *argv]
        )
        with mock.patch("wmr_bridge.ble_gatt.BluezGattPeripheral", FakeBle), \
             mock.patch("wmr_bridge.__main__.HidrawManager", FakeManager), \
             self.assertLogs("wmr_bridge", level="INFO") as logs:
            await asyncio.wait_for(_run(args), 5)
        return events, captured, logs.output

    async def test_ble_peripheral_starts_by_default(self) -> None:
        events, captured, output = await self._run_with_ble([])
        self.assertEqual(events, ["start", "close"])
        self.assertTrue(any("BLE GATT: FF10" in line for line in output))
        self.assertFalse(any("BLE GATT disabled" in line for line in output))
        hosts = captured["hosts"]
        self.assertTrue(callable(hosts))
        self.assertIsInstance(hosts(), list)

    async def test_no_ble_skips_the_peripheral(self) -> None:
        class ForbiddenBle:
            def __init__(self, *_args, **_kwargs) -> None:
                raise AssertionError("--no-ble must not construct the peripheral")

        class FakeManager:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        args = _build_parser().parse_args(
            ["--duration", "0.05", "--port", "0", "--no-auto-pair", "--no-ble"]
        )
        with mock.patch("wmr_bridge.ble_gatt.BluezGattPeripheral", ForbiddenBle), \
             mock.patch("wmr_bridge.__main__.HidrawManager", FakeManager), \
             self.assertLogs("wmr_bridge", level="INFO"):
            await asyncio.wait_for(_run(args), 5)

    async def test_ble_fallback_alias_warns_but_still_runs(self) -> None:
        events, _captured, output = await self._run_with_ble(["--ble-fallback"])
        self.assertEqual(events, ["start", "close"])
        self.assertTrue(any("deprecated" in line for line in output))


class HandFilterTests(unittest.TestCase):
    def test_hand_option_parses_both_left_and_right(self) -> None:
        parser = _build_parser()
        self.assertEqual(parser.parse_args([]).hand, "both")
        self.assertEqual(parser.parse_args(["--hand", "left"]).hand, "left")
        self.assertEqual(parser.parse_args(["--hand", "right"]).hand, "right")

    def test_advertise_mdns_is_opt_in(self) -> None:
        parser = _build_parser()
        self.assertFalse(parser.parse_args([]).advertise_mdns)
        self.assertTrue(parser.parse_args(["--advertise-mdns"]).advertise_mdns)

    def test_auto_pair_is_on_by_default_and_can_be_disabled(self) -> None:
        parser = _build_parser()
        self.assertTrue(parser.parse_args([]).auto_pair)
        self.assertFalse(parser.parse_args(["--no-auto-pair"]).auto_pair)

    def test_hidraw_manager_ignores_disallowed_hand(self) -> None:
        from wmr_bridge.bluez import HidrawDevice
        from wmr_bridge.bridge import Bridge
        from wmr_bridge.inputs import HidrawManager

        bridge = Bridge()
        manager = HidrawManager(bridge, allowed_hands=(0,))
        manager._loop = mock.MagicMock()

        right_device = HidrawDevice("/dev/hidraw0", "Motion controller - Right", "AA:BB:CC:DD:EE:01")
        left_device = HidrawDevice("/dev/hidraw1", "Motion controller - Left", "AA:BB:CC:DD:EE:00")

        with mock.patch.object(manager, "_device_for", return_value=right_device), \
             mock.patch("wmr_bridge.inputs.HidrawReader") as reader_cls:
            self.assertFalse(manager._open("/dev/hidraw0"))
            reader_cls.assert_not_called()

        with mock.patch.object(manager, "_device_for", return_value=left_device), \
             mock.patch("wmr_bridge.inputs.HidrawReader") as reader_cls:
            reader_cls.return_value.fd = 99
            with self.assertLogs("wmr_bridge.inputs", level="INFO") as logs:
                self.assertTrue(manager._open("/dev/hidraw1"))
            self.assertIn("LEFT connected: /dev/hidraw1", logs.output[0])
            reader_cls.assert_called_once_with(
                "/dev/hidraw1", keep_alive_interval=10.0
            )
            self.assertTrue(bridge.hands[0].connected)
            self.assertFalse(bridge.hands[1].connected)

            with self.assertLogs("wmr_bridge.inputs", level="INFO") as logs:
                manager._close("/dev/hidraw1")
            self.assertIn("LEFT disconnected: /dev/hidraw1", logs.output[0])
            self.assertFalse(bridge.hands[0].connected)

    def test_hidraw_manager_passes_keep_alive_interval_to_reader(self) -> None:
        from wmr_bridge.bluez import HidrawDevice
        from wmr_bridge.bridge import Bridge
        from wmr_bridge.inputs import HidrawManager

        bridge = Bridge()
        manager = HidrawManager(bridge, allowed_hands=(0,), keep_alive_interval=4.0)
        manager._loop = mock.MagicMock()

        device = HidrawDevice("/dev/hidraw1", "Motion controller - Left", "AA:BB:CC:DD:EE:00")
        with mock.patch.object(manager, "_device_for", return_value=device), \
             mock.patch("wmr_bridge.inputs.HidrawReader") as reader_cls:
            reader_cls.return_value.fd = 99
            self.assertTrue(manager._open("/dev/hidraw1"))
            reader_cls.assert_called_once_with("/dev/hidraw1", keep_alive_interval=4.0)

    def test_wmr_bridge_args_env_supports_hand_and_custom_flags(self) -> None:
        args = _parse_cli_args(["--bind", "0.0.0.0"], env_args="--hand left --batch-max 4")
        self.assertEqual(args.bind, "0.0.0.0")
        self.assertEqual(args.hand, "left")
        self.assertEqual(args.batch_max, 4)


class StatusTests(unittest.TestCase):
    def test_status_argument_parses(self) -> None:
        self.assertEqual(_build_parser().parse_args(["status"]).command, "status")
        self.assertIsNone(_build_parser().parse_args([]).command)
        self.assertTrue(_build_parser().parse_args(["--status"]).status)

    def test_pair_command_parses(self) -> None:
        args = _build_parser().parse_args(["pair", "--seconds", "30"])
        self.assertEqual(args.command, "pair")
        self.assertEqual(args.seconds, 30.0)
        self.assertFalse(args.close)
        self.assertTrue(_build_parser().parse_args(["pair", "--close"]).close)

    def test_prefer_5ghz_flags_parse(self) -> None:
        parser = _build_parser()
        defaults = parser.parse_args([])
        self.assertFalse(defaults.prefer_5ghz)
        self.assertFalse(defaults.no_reconnect)
        args = parser.parse_args(["--prefer-5ghz", "--no-reconnect"])
        self.assertTrue(args.prefer_5ghz)
        self.assertTrue(args.no_reconnect)

    def test_format_status_names_both_hands_and_the_log_hint(self) -> None:
        status = BluezStatus(
            adapter=BluezAdapter("11:22:33:44:55:66", "wmr-pi", True, True, False, True),
            devices=(
                BluezDevice(
                    "AA:BB:CC:DD:EE:00", "Motion controller - Left", True, True, True
                ),
            ),
            hidraw=(
                HidrawDevice("/dev/hidraw0", "Motion controller - Left", "AA:BB:CC:DD:EE:00"),
            ),
        )
        lines = _format_status(status)
        self.assertIn("powered=yes", lines[0])
        self.assertIn("discovering=yes", lines[0])
        self.assertIn("LEFT: /dev/hidraw0", lines[1])
        self.assertIn("paired=yes trusted=yes connected=yes", lines[1])
        self.assertIn("RIGHT: no hidraw node", lines[2])
        self.assertIn("journalctl -u wmr-bridge", lines[3])

    def test_print_status_reads_bluetoothctl(self) -> None:
        def runner(args: list[str], timeout: float):
            if args[1] == "show":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        "Controller 11:22:33:44:55:66 (public)\n"
                        "\tName: wmr-pi\n\tPowered: yes\n\tPairable: yes\n"
                    ),
                    stderr="",
                )
            if args[1] == "devices":
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
            raise AssertionError(args)

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(output):
            code = _print_status(
                runner=runner,
                which=lambda _command: "/usr/bin/bluetoothctl",
                sysfs_root=temp,
            )

        self.assertEqual(code, 0)
        self.assertIn("Bluetooth adapter 11:22:33:44:55:66", output.getvalue())
        self.assertIn("journalctl -u wmr-bridge", output.getvalue())

    def test_print_status_without_adapter_explains(self) -> None:
        def runner(args: list[str], timeout: float):
            return subprocess.CompletedProcess(
                args, 1, stdout="No default controller available\n", stderr=""
            )

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(output):
            code = _print_status(
                runner=runner,
                which=lambda _command: "/usr/bin/bluetoothctl",
                sysfs_root=temp,
            )

        self.assertEqual(code, 1)
        self.assertIn("rfkill list bluetooth", output.getvalue())

    def test_print_status_without_bluetoothctl_explains(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = _print_status(which=lambda _command: None)

        self.assertEqual(code, 1)
        self.assertIn("install bluez", output.getvalue())

    def test_format_status_reports_wifi_band_and_warning(self) -> None:
        status = BluezStatus(
            adapter=BluezAdapter("11:22:33:44:55:66", "wmr-pi", True, True, False, True),
            devices=(),
            hidraw=(),
        )
        lines = _format_status(status, WIFI_INFO)
        self.assertIn(
            "Wi-Fi wlan0: band=2.4GHz 2437 MHz supports5Ghz=yes", lines
        )
        self.assertIn(f"Wi-Fi warning: {WIFI_INFO['warning']}", lines)

    def test_format_status_reports_the_active_uplink(self) -> None:
        status = BluezStatus(
            adapter=BluezAdapter("11:22:33:44:55:66", "wmr-pi", True, True, False, True),
            devices=(),
            hidraw=(),
        )

        lines = _format_status(status, WIFI_INFO, UPLINK_INFO)

        self.assertIn("Uplink eth0: kind=ethernet addresses=192.0.2.60", lines)
        self.assertIn("Wi-Fi wlan0: band=2.4GHz", "\n".join(lines))

    def test_format_status_without_a_default_route_says_so(self) -> None:
        status = BluezStatus(
            adapter=BluezAdapter("11:22:33:44:55:66", "wmr-pi", True, True, False, True),
            devices=(),
            hidraw=(),
        )

        lines = _format_status(
            status, uplink={"iface": None, "kind": None, "addresses": []}
        )

        self.assertIn("Uplink: none (no IPv4 default route)", lines)

    def test_print_status_includes_wifi(self) -> None:
        def runner(args: list[str], timeout: float):
            if args[1] == "show":
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="Controller 11:22:33:44:55:66 (public)\n\tName: wmr-pi\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(output):
            code = _print_status(
                runner=runner,
                which=lambda _command: "/usr/bin/bluetoothctl",
                sysfs_root=temp,
                wifi=WIFI_INFO,
                uplink=UPLINK_INFO,
            )

        self.assertEqual(code, 0)
        self.assertIn("Uplink eth0: kind=ethernet", output.getvalue())
        self.assertIn("Wi-Fi wlan0: band=2.4GHz", output.getvalue())
        self.assertIn("Wi-Fi warning:", output.getvalue())


class IdleTimeoutTests(unittest.TestCase):
    def test_defaults_match_the_two_stage_policy(self) -> None:
        args = _build_parser().parse_args([])
        self.assertEqual(args.idle_sleep_timeout, 300.0)
        self.assertEqual(args.idle_poweroff_timeout, 900.0)
        self.assertEqual(args.idle_reconnect_cooldown, 60.0)

    def test_flags_parse_seconds(self) -> None:
        args = _build_parser().parse_args(
            [
                "--idle-sleep-timeout",
                "120",
                "--idle-poweroff-timeout",
                "600",
                "--idle-reconnect-cooldown",
                "30",
            ]
        )
        self.assertEqual(args.idle_sleep_timeout, 120.0)
        self.assertEqual(args.idle_poweroff_timeout, 600.0)
        self.assertEqual(args.idle_reconnect_cooldown, 30.0)

    def test_zero_disables_a_stage(self) -> None:
        args = _build_parser().parse_args(
            ["--idle-sleep-timeout", "0", "--idle-poweroff-timeout", "0"]
        )
        self.assertEqual(args.idle_sleep_timeout, 0.0)
        self.assertEqual(args.idle_poweroff_timeout, 0.0)

    def test_negative_timeout_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["--idle-sleep-timeout", "-1"])
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["--idle-poweroff-timeout", "-1"])
        with self.assertRaises(SystemExit):
            _build_parser().parse_args(["--idle-reconnect-cooldown", "-1"])

    def test_poweroff_must_be_disabled_or_at_least_the_sleep_timeout(self) -> None:
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            _validate_idle_timeouts(
                parser.parse_args(
                    ["--idle-sleep-timeout", "300", "--idle-poweroff-timeout", "60"]
                )
            )
        _validate_idle_timeouts(
            parser.parse_args(
                ["--idle-sleep-timeout", "300", "--idle-poweroff-timeout", "0"]
            )
        )
        _validate_idle_timeouts(
            parser.parse_args(
                ["--idle-sleep-timeout", "300", "--idle-poweroff-timeout", "300"]
            )
        )


class IdleManagerWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_passes_idle_timeouts_to_hidraw_manager(self) -> None:
        captured: dict[str, object] = {}

        class FakeManager:
            def __init__(self, *_args, **kwargs) -> None:
                captured.update(kwargs)

            def start(self) -> None:
                pass

            async def close(self) -> None:
                pass

        args = _build_parser().parse_args(
            [
                "--duration",
                "0.05",
                "--port",
                "0",
                "--no-auto-pair",
                "--no-ble",
                "--idle-sleep-timeout",
                "60",
                "--idle-poweroff-timeout",
                "120",
            ]
        )
        with mock.patch("wmr_bridge.__main__.HidrawManager", FakeManager):
            await asyncio.wait_for(_run(args), 5)

        self.assertEqual(captured["idle_sleep_timeout"], 60.0)
        self.assertEqual(captured["idle_poweroff_timeout"], 120.0)
        self.assertEqual(captured["idle_reconnect_cooldown"], 60.0)
        self.assertIs(captured["reconnect_guard"], set_device_blocked)

    async def test_run_rejects_poweroff_before_sleep(self) -> None:
        args = _build_parser().parse_args(
            [
                "--duration",
                "0.05",
                "--port",
                "0",
                "--no-auto-pair",
                "--no-ble",
                "--idle-sleep-timeout",
                "300",
                "--idle-poweroff-timeout",
                "60",
            ]
        )
        with self.assertRaises(SystemExit):
            await _run(args)


class PairCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.calls: list[tuple[str, float | None]] = []

        async def handler(action: str, seconds: float | None) -> dict:
            self.calls.append((action, seconds))
            return {"open": action == "open", "seconds": seconds}

        self.bridge = Bridge(token="secret")
        self.bridge.pairing_handler = handler
        self.server = BridgeServer(self.bridge, host="127.0.0.1", port=0)
        await self.server.start()

    async def asyncTearDown(self) -> None:
        await self.server.close()

    def _args(self, *extra: str):
        return _build_parser().parse_args(
            [
                "pair",
                "--port",
                str(self.server.port),
                "--token",
                "secret",
                *extra,
            ]
        )

    async def test_pair_opens_and_closes_the_window(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(await _pair_window(self._args("--seconds", "45")), 0)
        self.assertIn("open for 45 s", out.getvalue())
        self.assertEqual(self.calls, [("open", 45.0)])

        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(await _pair_window(self._args("--close")), 0)
        self.assertIn("manual BLE pairing window closed", out.getvalue())
        self.assertEqual(self.calls[-1], ("close", None))

    async def test_pair_without_the_token_explains_how_to_pass_it(self) -> None:
        args = _build_parser().parse_args(
            ["pair", "--port", str(self.server.port)]
        )
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(await _pair_window(args), 1)
        self.assertIn("--token-file", out.getvalue())
        self.assertEqual(self.calls, [])


class WifiStartupTests(unittest.TestCase):
    def test_prefer_5ghz_is_a_one_shot_action(self) -> None:
        calls: list[object] = []

        def fake_prefer(reconnect: bool = True, **_kwargs):
            calls.append(("prefer", reconnect))
            return {
                "profile": "HomeNet-5ghz",
                "changed": True,
                "reconnected": False,
                "warning": None,
            }

        def no_bridge(*_args, **_kwargs):
            raise AssertionError("--prefer-5ghz must not start the bridge")

        with mock.patch("wmr_bridge.__main__.prefer_5ghz", side_effect=fake_prefer), \
             mock.patch("wmr_bridge.__main__.Bridge", side_effect=no_bridge):
            with self.assertLogs("wmr_bridge", level="INFO") as logs:
                code = main(["--prefer-5ghz"])
            self.assertEqual(code, 0)
            self.assertEqual(calls, [("prefer", True)])
            self.assertTrue(any("HomeNet-5ghz" in line for line in logs.output))

            code = main(["--prefer-5ghz", "--no-reconnect"])
            self.assertEqual(code, 0)
            self.assertEqual(calls[-1], ("prefer", False))


if __name__ == "__main__":
    unittest.main()
