"""UDP discovery responder and the stable bridge identity it advertises."""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path

from wmr_bridge import __version__
from wmr_bridge.bridge import Bridge, default_bridge_id
from wmr_bridge.discovery import (
    MAX_NONCE_LENGTH,
    PROBE_PREFIX,
    DiscoveryResponder,
    parse_probe,
)


def _responder(**overrides) -> DiscoveryResponder:
    options = {
        "bridge_id": "wmr-pi-abc123",
        "name": "wmr-pi",
        "port": 8765,
        "hands": ["LEFT", "RIGHT"],
        "path": "/wmr",
        "version": __version__,
        "auth_required": True,
        "listen_host": "127.0.0.1",
        "listen_port": 0,
        "reply_interval_s": 0,
    }
    options.update(overrides)
    return DiscoveryResponder(**options)


class ProbeTests(unittest.TestCase):
    def test_accepts_versioned_probe_and_returns_nonce(self) -> None:
        self.assertEqual(parse_probe(b"WMR_DISCOVER_V1 8f3a2c"), "8f3a2c")
        self.assertEqual(parse_probe(b"WMR_DISCOVER_V1 8f3a2c\r\n"), "8f3a2c")

    def test_rejects_malformed_probes(self) -> None:
        for probe in (
            b"",
            b"WMR_DISCOVER_V2 nonce",
            b"WMR_DISCOVER_V1",
            b"WMR_DISCOVER_V1 ",
            b"WMR_DISCOVER_V1 one two",
            b"WMR_DISCOVER_V1 " + b"x" * (MAX_NONCE_LENGTH + 1),
            b"WMR_DISCOVER_V1 \xff",
        ):
            with self.subTest(probe=probe):
                self.assertIsNone(parse_probe(probe))


class ReplyTests(unittest.TestCase):
    def test_reply_carries_identity_endpoint_and_auth_mode(self) -> None:
        responder = _responder()
        reply = responder.reply_json("8f3a2c")
        self.assertEqual(reply["type"], "wmr_bridge")
        self.assertEqual(reply["protocol"], 1)
        self.assertEqual(reply["bridge_id"], "wmr-pi-abc123")
        self.assertEqual(reply["name"], "wmr-pi")
        self.assertEqual(reply["port"], 8765)
        self.assertEqual(reply["path"], "/wmr")
        self.assertEqual(reply["hands"], ["LEFT", "RIGHT"])
        self.assertEqual(reply["version"], __version__)
        self.assertEqual(reply["authMode"], "token")
        self.assertEqual(reply["nonce"], "8f3a2c")
        self.assertNotIn("token", reply.keys())

    def test_no_auth_mode_is_reported(self) -> None:
        self.assertEqual(_responder(auth_required=False).reply_json("n")["authMode"], "none")

    def test_reply_for_rejects_non_probes(self) -> None:
        responder = _responder()
        self.assertIsNone(responder.reply_for(b"hello", "192.0.2.1"))

    def test_reply_for_is_rate_limited_per_source(self) -> None:
        now = [100.0]
        responder = _responder(reply_interval_s=1.0, clock=lambda: now[0])
        probe = b"WMR_DISCOVER_V1 nonce"
        self.assertIsNotNone(responder.reply_for(probe, "192.0.2.1"))
        self.assertIsNone(responder.reply_for(probe, "192.0.2.1"))
        self.assertIsNotNone(responder.reply_for(probe, "192.0.2.2"))
        now[0] += 1.0
        self.assertIsNotNone(responder.reply_for(probe, "192.0.2.1"))

    def test_reply_does_not_exceed_source_table(self) -> None:
        responder = _responder(max_tracked_sources=2)
        probe = b"WMR_DISCOVER_V1 nonce"
        for address in ("192.0.2.1", "192.0.2.2", "192.0.2.3"):
            self.assertIsNotNone(responder.reply_for(probe, address))
        self.assertLessEqual(len(responder._last_reply), 2)


class IdentityTests(unittest.TestCase):
    def test_hello_exposes_the_same_bridge_id_as_discovery(self) -> None:
        bridge = Bridge(bridge_id="wmr-pi-abc123")
        responder = _responder(bridge_id=bridge.bridge_id)
        self.assertEqual(bridge.hello()["bridge_id"], "wmr-pi-abc123")
        self.assertEqual(responder.reply_json("n")["bridge_id"], bridge.hello()["bridge_id"])

    def test_default_bridge_id_is_stable_and_derived_from_machine_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            machine_id = Path(directory) / "machine-id"
            machine_id.write_text("0123456789abcdef\n", encoding="utf-8")
            first = default_bridge_id("WMR-Pi", machine_id)
            second = default_bridge_id("WMR-Pi", machine_id)
            machine_id.with_name("other").write_text("fedcba9876543210\n", encoding="utf-8")
            other = default_bridge_id("WMR-Pi", machine_id.with_name("other"))
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("wmr-pi-"))
        self.assertEqual(first, first.lower())

    def test_default_bridge_id_falls_back_to_hostname(self) -> None:
        missing = Path("/nonexistent/machine-id-for-test")
        self.assertEqual(
            default_bridge_id("WMR-Pi", missing),
            default_bridge_id("WMR-Pi", missing),
        )


class UdpRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_gets_one_unicast_json_reply(self) -> None:
        responder = _responder()
        await responder.start()
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(b"WMR_DISCOVER_V1 8f3a2c", ("127.0.0.1", responder.bound_port))
            data, _addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), 2)
        finally:
            sock.close()
            await responder.close()
        reply = json.loads(data)
        self.assertEqual(reply["nonce"], "8f3a2c")
        self.assertEqual(reply["bridge_id"], "wmr-pi-abc123")

    async def test_reply_advertises_websocket_port_not_discovery_port(self) -> None:
        responder = _responder(port=9000)
        await responder.start()
        self.assertIsNotNone(responder.bound_port)
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(b"WMR_DISCOVER_V1 8f3a2c", ("127.0.0.1", responder.bound_port))
            data, _addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), 2)
        finally:
            sock.close()
            await responder.close()
        self.assertEqual(json.loads(data)["port"], 9000)

    async def test_malformed_probe_gets_no_reply(self) -> None:
        responder = _responder()
        await responder.start()
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            sock.sendto(b"not a probe", ("127.0.0.1", responder.bound_port))
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), 0.2)
        finally:
            sock.close()
            await responder.close()


if __name__ == "__main__":
    unittest.main()
