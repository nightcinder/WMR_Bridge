"""mDNS advertisement: avahi, systemd-resolved fallback and clean no-op."""

from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wmr_bridge.mdns import SERVICE_FILE, SERVICE_TYPE, MdnsAdvertiser

LOG = logging.getLogger("wmr_bridge.mdns")


class FakeProcess:
    def __init__(self) -> None:
        self.terminated = 0
        self.waited = 0

    def terminate(self) -> None:
        self.terminated += 1

    def wait(self, timeout: float | None = None) -> int:
        self.waited += 1
        return 0


def _which(avahi: str | None = None, resolvectl: str | None = None):
    return lambda name: {"avahi-publish-service": avahi, "resolvectl": resolvectl}.get(name)


class AbsentTests(unittest.TestCase):
    def test_no_publisher_logs_once_and_keeps_running(self) -> None:
        advertiser = MdnsAdvertiser(port=8765, hands=["LEFT", "RIGHT"], which=_which())
        with self.assertLogs(LOG, level="INFO") as caught:
            self.assertFalse(advertiser.start())
        self.assertEqual(len(caught.records), 1)
        self.assertIn("both absent", caught.output[0])
        advertiser.close()


class AvahiTests(unittest.TestCase):
    def test_publishes_service_with_hand_txt(self) -> None:
        process = FakeProcess()
        calls: list[tuple] = []

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return process

        advertiser = MdnsAdvertiser(
            port=8765,
            hands=["LEFT"],
            hostname="wmr-pi",
            which=_which(avahi="/usr/bin/avahi-publish-service"),
            popen=popen,
        )
        with self.assertLogs(LOG, level="INFO") as caught:
            self.assertTrue(advertiser.start())
        command, kwargs = calls[0]
        self.assertEqual(command[0], "/usr/bin/avahi-publish-service")
        self.assertIn("--no-reverse", command)
        self.assertIn("wmr-pi", command)
        self.assertIn(SERVICE_TYPE, command)
        self.assertIn("8765", command)
        self.assertIn("hands=LEFT", command)
        self.assertIn("path=/wmr", command)
        self.assertEqual(kwargs["stdout"], mock.ANY)
        self.assertIn("via avahi-publish-service", caught.output[0])
        self.assertTrue(advertiser.start(), "start() is idempotent")
        self.assertEqual(len(calls), 1)

        advertiser.close()
        self.assertEqual(process.terminated, 1)
        self.assertEqual(process.waited, 1)
        advertiser.close()
        self.assertEqual(process.terminated, 1)

    def test_avahi_start_failure_is_not_fatal(self) -> None:
        advertiser = MdnsAdvertiser(
            port=8765,
            hands=["LEFT", "RIGHT"],
            which=_which(avahi="/usr/bin/avahi-publish-service"),
            popen=mock.Mock(side_effect=OSError("missing binary")),
        )
        with self.assertLogs(LOG, level="INFO") as caught:
            self.assertFalse(advertiser.start())
        self.assertIn("cannot start avahi", caught.output[0])


class ResolvedTests(unittest.TestCase):
    def test_registers_dnssd_file_and_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dnssd_dir = Path(directory)
            advertiser = MdnsAdvertiser(
                port=9000,
                hands=["RIGHT"],
                hostname="wmr-pi",
                dnssd_dir=dnssd_dir,
                which=_which(resolvectl="/usr/bin/resolvectl"),
            )
            with self.assertLogs(LOG, level="INFO") as caught:
                self.assertTrue(advertiser.start())
            service = dnssd_dir / SERVICE_FILE
            text = service.read_text(encoding="utf-8")
            self.assertIn("[Service]", text)
            self.assertIn("Name=wmr-pi", text)
            self.assertIn(f"Type={SERVICE_TYPE}", text)
            self.assertIn("Port=9000", text)
            self.assertIn("hands=RIGHT", text)
            self.assertIn("systemd-resolved", caught.output[0])

            advertiser.close()
            self.assertFalse(service.exists())
            advertiser.close()

    def test_missing_dnssd_directory_logs_and_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            advertiser = MdnsAdvertiser(
                port=9000,
                hands=["LEFT", "RIGHT"],
                dnssd_dir=Path(directory) / "absent",
                which=_which(resolvectl="/usr/bin/resolvectl"),
            )
            with self.assertLogs(LOG, level="INFO") as caught:
                self.assertFalse(advertiser.start())
            self.assertIn("does not exist", caught.output[0])
            self.assertFalse(advertiser.service_file.exists())


if __name__ == "__main__":
    unittest.main()
