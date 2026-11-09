"""Wi-Fi radio inspection and the non-lockout NetworkManager 5 GHz preference."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from wmr_bridge.wifi import inspect_uplink, inspect_wifi, prefer_5ghz

IW_DEV_5GHZ = """phy#0
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr b8:27:eb:00:00:01
\t\tssid HomeNet
\t\ttype managed
\t\tchannel 36 (5180 MHz), width: 80 MHz, center1: 5210 MHz
\t\ttxpower 22.00 dBm
"""

IW_DEV_24GHZ = """phy#0
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr b8:27:eb:00:00:01
\t\tssid HomeNet
\t\ttype managed
\t\tchannel 6 (2437 MHz), width: 20 MHz
\t\ttxpower 22.00 dBm
"""

IW_DEV_DISCONNECTED = """phy#0
\tInterface wlan0
\t\tifindex 3
\t\twdev 0x1
\t\taddr b8:27:eb:00:00:01
\t\ttype managed
\t\ttxpower 22.00 dBm
"""

IW_PHY_DUAL_BAND = """Wiphy phy0
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412 MHz [1] (20.0 dBm)
\t\t\t* 2437 MHz [6] (20.0 dBm)
\tBand 2:
\t\tFrequencies:
\t\t\t* 5180 MHz [36] (23.0 dBm)
\t\t\t* 5745 MHz [149] (23.0 dBm)
"""

IW_PHY_24GHZ_ONLY = """Wiphy phy0
\tBand 1:
\t\tFrequencies:
\t\t\t* 2412 MHz [1] (20.0 dBm)
\t\t\t* 2437 MHz [6] (20.0 dBm)
"""

NM_ACTIVE = "HomeNet:wlan0\n"
NM_NAMES = "HomeNet\nWired connection 1\n"
NM_TYPES_ETHERNET = "HomeNet:802-11-wireless\nWired connection 1:802-3-ethernet\n"
NM_WIFI_5GHZ = "AA\\:BB\\:CC\\:DD\\:EE\\:FF:5180 MHz\n11\\:22\\:33\\:44\\:55\\:66:2437 MHz\n"
NM_WIFI_24GHZ = "11\\:22\\:33\\:44\\:55\\:66:2437 MHz\n"


class FakeRunner:
    """Return canned output for exact command strings and record every call."""

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        failures: set[str] | None = None,
        missing: set[str] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.failures = failures or set()
        self.missing = missing or set()
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        key = " ".join(argv)
        if key in self.missing:
            return subprocess.CompletedProcess(argv, 127, "", f"{argv[0]}: not found")
        if key in self.failures:
            return subprocess.CompletedProcess(argv, 1, "", "nmcli failed")
        return subprocess.CompletedProcess(argv, 0, self.responses.get(key, ""), "")

    def commands(self) -> list[str]:
        return [" ".join(call) for call in self.calls]


def _inspect_runner(dev: str, phy: str) -> FakeRunner:
    return FakeRunner({"iw dev": dev, "iw phy": phy})


def _prefer_runner(dev: str, phy: str, **responses: str) -> FakeRunner:
    defaults = {
        "nmcli -t -f NAME,TYPE connection show": "",
        "nmcli -t -f NAME,DEVICE connection show --active": NM_ACTIVE,
        "nmcli -t -f NAME connection show": NM_NAMES,
        "nmcli -t -f BSSID,FREQ device wifi list ifname wlan0": NM_WIFI_5GHZ,
        "nmcli connection up HomeNet-5ghz ifname wlan0": "",
    }
    defaults.update(responses)
    return FakeRunner({"iw dev": dev, "iw phy": phy, **defaults})


class InspectTests(unittest.TestCase):
    def test_5ghz_connection_reports_band_and_no_contention(self) -> None:
        result = inspect_wifi(runner=_inspect_runner(IW_DEV_5GHZ, IW_PHY_DUAL_BAND))

        self.assertEqual(result["iface"], "wlan0")
        self.assertTrue(result["connected"])
        self.assertEqual(result["freqMhz"], 5180)
        self.assertEqual(result["band"], "5GHz")
        self.assertTrue(result["supports5Ghz"])
        self.assertFalse(result["contentionWarning"])
        self.assertIsNone(result["warning"])

    def test_2_4ghz_connection_warns_about_bluetooth_contention(self) -> None:
        result = inspect_wifi(runner=_inspect_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND))

        self.assertEqual(result["band"], "2.4GHz")
        self.assertEqual(result["freqMhz"], 2437)
        self.assertTrue(result["supports5Ghz"])
        self.assertTrue(result["contentionWarning"])
        self.assertIn("2.4 GHz", result["warning"])
        self.assertIn("Bluetooth", result["warning"])

    def test_disconnected_interface_is_reported_without_a_band(self) -> None:
        result = inspect_wifi(
            runner=_inspect_runner(IW_DEV_DISCONNECTED, IW_PHY_DUAL_BAND)
        )

        self.assertFalse(result["connected"])
        self.assertIsNone(result["freqMhz"])
        self.assertIsNone(result["band"])
        self.assertFalse(result["contentionWarning"])
        self.assertEqual(result["warning"], "Wi-Fi is not connected.")

    def test_result_never_contains_the_ssid(self) -> None:
        result = inspect_wifi(runner=_inspect_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND))

        self.assertNotIn("HomeNet", json.dumps(result))
        self.assertNotIn("ssid", {key.casefold() for key in result})

    def test_requested_missing_interface_is_a_warning(self) -> None:
        result = inspect_wifi("wlan9", runner=_inspect_runner(IW_DEV_5GHZ, IW_PHY_DUAL_BAND))

        self.assertEqual(result["iface"], "wlan9")
        self.assertFalse(result["connected"])
        self.assertIsNone(result["supports5Ghz"])
        self.assertIn("not found", result["warning"])

    def test_unreadable_phy_leaves_5ghz_support_unknown(self) -> None:
        runner = FakeRunner({"iw dev": IW_DEV_24GHZ}, failures={"iw phy"})

        result = inspect_wifi(runner=runner)

        self.assertIsNone(result["supports5Ghz"])
        self.assertIn("could not be determined", result["warning"])


class Prefer5GhzTests(unittest.TestCase):
    def test_clones_the_active_profile_and_reconnects_to_a_visible_5ghz_bssid(self) -> None:
        runner = _prefer_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND)

        result = prefer_5ghz(runner=runner)

        self.assertEqual(result["baseProfile"], "HomeNet")
        self.assertEqual(result["profile"], "HomeNet-5ghz")
        self.assertTrue(result["changed"])
        self.assertTrue(result["reconnected"])
        self.assertIsNone(result["warning"])
        commands = runner.commands()
        self.assertIn("nmcli connection clone HomeNet HomeNet-5ghz", commands)
        self.assertIn(
            "nmcli connection modify HomeNet-5ghz 802-11-wireless.band a "
            "connection.autoconnect-priority 50 ipv4.route-metric 200 "
            "ipv6.route-metric 200",
            commands,
        )
        self.assertIn("nmcli connection up HomeNet-5ghz ifname wlan0", commands)
        # The base profile keeps its band and security; only its fallback
        # route metric changes (300, below the 5 GHz clone at 200).
        self.assertIn(
            "nmcli connection modify HomeNet connection.autoconnect-priority 0 "
            "ipv4.route-metric 300 ipv6.route-metric 300",
            commands,
        )
        self.assertFalse(any("modify HomeNet 802-11-wireless" in command for command in commands))

    def test_reconnect_false_only_writes_the_profile(self) -> None:
        runner = _prefer_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND)

        result = prefer_5ghz(reconnect=False, runner=runner)

        self.assertTrue(result["changed"])
        self.assertFalse(result["reconnected"])
        commands = runner.commands()
        self.assertFalse(any("device wifi list" in command for command in commands))
        self.assertFalse(any("connection up" in command for command in commands))

    def test_existing_profile_is_updated_instead_of_cloned(self) -> None:
        runner = _prefer_runner(
            IW_DEV_24GHZ,
            IW_PHY_DUAL_BAND,
            **{"nmcli -t -f NAME connection show": "HomeNet\nHomeNet-5ghz\n"},
        )

        result = prefer_5ghz(runner=runner)

        self.assertTrue(result["changed"])
        commands = runner.commands()
        self.assertFalse(any("connection clone" in command for command in commands))
        self.assertIn(
            "nmcli connection modify HomeNet-5ghz 802-11-wireless.band a "
            "connection.autoconnect-priority 50 ipv4.route-metric 200 "
            "ipv6.route-metric 200",
            commands,
        )

    def test_active_5ghz_clone_is_mapped_back_to_the_base_profile(self) -> None:
        runner = _prefer_runner(
            IW_DEV_5GHZ,
            IW_PHY_DUAL_BAND,
            **{
                "nmcli -t -f NAME,DEVICE connection show --active": "HomeNet-5ghz:wlan0\n",
                "nmcli -t -f NAME connection show": "HomeNet\nHomeNet-5ghz\n",
            },
        )

        result = prefer_5ghz(runner=runner)

        self.assertEqual(result["baseProfile"], "HomeNet")
        self.assertEqual(result["profile"], "HomeNet-5ghz")
        commands = runner.commands()
        self.assertFalse(any("connection clone" in command for command in commands))
        self.assertIn(
            "nmcli connection modify HomeNet-5ghz 802-11-wireless.band a "
            "connection.autoconnect-priority 50 ipv4.route-metric 200 "
            "ipv6.route-metric 200",
            commands,
        )
        self.assertIn(
            "nmcli connection modify HomeNet connection.autoconnect-priority 0 "
            "ipv4.route-metric 300 ipv6.route-metric 300",
            commands,
        )
        self.assertFalse(
            any(
                "modify HomeNet-5ghz" in command and "300" in command
                for command in commands
            )
        )

    def test_no_visible_5ghz_bssid_leaves_the_current_connection_alone(self) -> None:
        runner = _prefer_runner(
            IW_DEV_24GHZ,
            IW_PHY_DUAL_BAND,
            **{
                "nmcli -t -f BSSID,FREQ device wifi list ifname wlan0": NM_WIFI_24GHZ,
            },
        )

        result = prefer_5ghz(runner=runner)

        self.assertTrue(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertIn("left in place", result["warning"])
        self.assertFalse(any("connection up" in command for command in runner.commands()))

    def test_single_band_radio_is_a_warning_only_no_op(self) -> None:
        runner = _prefer_runner(IW_DEV_24GHZ, IW_PHY_24GHZ_ONLY)

        result = prefer_5ghz(runner=runner)

        self.assertFalse(result["supports5Ghz"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertIn("2.4 GHz only", result["warning"])
        self.assertEqual(
            runner.commands(),
            ["nmcli -t -f NAME,TYPE connection show", "iw dev", "iw phy"],
        )

    def test_already_on_5ghz_does_not_reconnect(self) -> None:
        runner = _prefer_runner(IW_DEV_5GHZ, IW_PHY_DUAL_BAND)

        result = prefer_5ghz(runner=runner)

        self.assertTrue(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertFalse(any("connection up" in command for command in runner.commands()))

    def test_missing_interface_keeps_the_inspection_warning(self) -> None:
        runner = _prefer_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND)

        result = prefer_5ghz(iface="wlan9", runner=runner)

        self.assertFalse(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertIn("wlan9", result["warning"])
        self.assertIn("not found", result["warning"])
        self.assertNotIn("2.4 GHz only", result["warning"])
        self.assertEqual(
            runner.commands(),
            ["nmcli -t -f NAME,TYPE connection show", "iw dev"],
        )

    def test_unreadable_phy_keeps_the_inspection_warning(self) -> None:
        runner = FakeRunner({"iw dev": IW_DEV_24GHZ}, failures={"iw phy"})

        result = prefer_5ghz(runner=runner)

        self.assertIsNone(result["supports5Ghz"])
        self.assertFalse(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertIn("could not be determined", result["warning"])
        self.assertNotIn("2.4 GHz only", result["warning"])
        self.assertEqual(
            runner.commands(),
            ["nmcli -t -f NAME,TYPE connection show", "iw dev", "iw phy"],
        )

    def test_failed_activation_keeps_the_current_connection(self) -> None:
        runner = _prefer_runner(IW_DEV_24GHZ, IW_PHY_DUAL_BAND)
        runner.failures.add("nmcli connection up HomeNet-5ghz ifname wlan0")

        result = prefer_5ghz(runner=runner)

        self.assertTrue(result["changed"])
        self.assertFalse(result["reconnected"])
        self.assertIn("left in place", result["warning"])
        commands = runner.commands()
        self.assertIn("nmcli connection up HomeNet-5ghz ifname wlan0", commands)
        self.assertFalse(any("connection down" in command for command in commands))
        self.assertFalse(any("modify HomeNet 802-11-wireless" in command for command in commands))


class IWFallbackTests(unittest.TestCase):
    def test_missing_iw_retries_the_absolute_path(self) -> None:
        runner = FakeRunner(
            {"/usr/sbin/iw dev": IW_DEV_5GHZ, "/usr/sbin/iw phy": IW_PHY_DUAL_BAND},
            missing={"iw dev", "iw phy"},
        )

        result = inspect_wifi(runner=runner)

        self.assertEqual(result["iface"], "wlan0")
        self.assertEqual(result["band"], "5GHz")
        self.assertEqual(runner.commands()[:2], ["iw dev", "/usr/sbin/iw dev"])


class UplinkTests(unittest.TestCase):
    def _route_file(self, text: str) -> Path:
        handle = tempfile.NamedTemporaryFile("w", delete=False, suffix=".route")
        handle.write(text)
        handle.close()
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_eth0_wins_over_a_higher_metric_wifi_default_route(self) -> None:
        path = self._route_file(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "wlan0\t00000000\t0102A8C0\t0003\t0\t0\t600\t00000000\n"
            "eth0\t00000000\t0102A8C0\t0003\t0\t0\t100\t00000000\n"
        )

        result = inspect_uplink(path, address_lookup=lambda iface: "192.0.2.60")

        self.assertEqual(
            result,
            {"iface": "eth0", "kind": "ethernet", "addresses": ["192.0.2.60"]},
        )

    def test_no_default_route_is_reported_as_none(self) -> None:
        path = self._route_file("Iface\tDestination\tGateway\tFlags\n")

        self.assertEqual(
            inspect_uplink(path),
            {"iface": None, "kind": None, "addresses": []},
        )


class RouteMetricTests(unittest.TestCase):
    def test_eth0_5ghz_and_2_4ghz_metrics_are_ordered(self) -> None:
        runner = _prefer_runner(
            IW_DEV_24GHZ,
            IW_PHY_DUAL_BAND,
            **{"nmcli -t -f NAME,TYPE connection show": NM_TYPES_ETHERNET},
        )

        result = prefer_5ghz(runner=runner)

        commands = runner.commands()
        self.assertIn(
            "nmcli connection modify Wired connection 1 ipv4.route-metric 100 "
            "ipv6.route-metric 100 connection.autoconnect-priority 100",
            commands,
        )
        self.assertIn(
            "nmcli connection modify HomeNet-5ghz 802-11-wireless.band a "
            "connection.autoconnect-priority 50 ipv4.route-metric 200 "
            "ipv6.route-metric 200",
            commands,
        )
        self.assertIn(
            "nmcli connection modify HomeNet connection.autoconnect-priority 0 "
            "ipv4.route-metric 300 ipv6.route-metric 300",
            commands,
        )
        self.assertEqual(result["wired"]["changed"], ["Wired connection 1"])
        self.assertEqual(result["wired"]["metric"], 100)
        self.assertTrue(result["baseMetricChanged"])


if __name__ == "__main__":
    unittest.main()
