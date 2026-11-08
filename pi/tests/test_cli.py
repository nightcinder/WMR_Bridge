"""CLI safety rails: bind default, token requirement and token-file errors."""

from __future__ import annotations

import asyncio
import io
import os
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
    _parse_cli_args,
    _print_status,
    _read_token,
    _require_auth_for_bind,
    _run,
)
from wmr_bridge.bluez import BluezAdapter, BluezDevice, BluezStatus, HidrawDevice

SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "controller_frames.txt"


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
            reader_cls.assert_called_once_with("/dev/hidraw1")
            self.assertTrue(bridge.hands[0].connected)
            self.assertFalse(bridge.hands[1].connected)

            with self.assertLogs("wmr_bridge.inputs", level="INFO") as logs:
                manager._close("/dev/hidraw1")
            self.assertIn("LEFT disconnected: /dev/hidraw1", logs.output[0])
            self.assertFalse(bridge.hands[0].connected)

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


if __name__ == "__main__":
    unittest.main()
