"""CLI safety rails: bind default, token requirement and token-file errors."""

from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest import mock

from wmr_bridge.__main__ import (
    _build_parser,
    _is_loopback_bind,
    _read_token,
    _require_auth_for_bind,
    _run,
)

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


if __name__ == "__main__":
    unittest.main()
