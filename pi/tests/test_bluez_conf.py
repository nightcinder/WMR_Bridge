"""Tests for the BlueZ Just Works re-pairing policy."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PI_DIR = Path(__file__).resolve().parents[1]
CONF_MODULE = PI_DIR / "wmr_bridge" / "bluez_conf.py"

from wmr_bridge.bluez_conf import (  # noqa: E402
    JUST_WORKS_REPAIRING,
    LE_MAX_CONNECTION_INTERVAL,
    LE_MIN_CONNECTION_INTERVAL,
    ensure_bluez_config,
    ensure_just_works_repairing,
    read_just_works_repairing,
    read_le_connection_intervals,
    set_just_works_repairing,
    set_le_connection_intervals,
)

STOCK = """[General]
#Name = BlueZ
# JustWorksRepairing = never
#TemporaryTimeout = 30

[LE]
#MinConnectionInterval = 40
"""


class RepairPolicyTextTests(unittest.TestCase):
    def test_the_package_asks_bluez_to_confirm_repairs_with_the_agent(self) -> None:
        # "confirm" makes bluetoothd call the default NoInputNoOutput agent for
        # the kernel's hint-0x01 Just Works re-pair instead of replying no.
        self.assertEqual(JUST_WORKS_REPAIRING, "confirm")

    def test_stock_commented_never_is_replaced_in_the_general_section(self) -> None:
        updated = set_just_works_repairing(STOCK)
        self.assertIn("[General]\nJustWorksRepairing = confirm\n", updated)
        self.assertNotIn("# JustWorksRepairing", updated)
        self.assertIn("[LE]\n#MinConnectionInterval = 40\n", updated)

    def test_an_existing_never_is_replaced(self) -> None:
        updated = set_just_works_repairing(
            "[General]\nJustWorksRepairing = never\n"
        )
        self.assertEqual(
            updated, "[General]\nJustWorksRepairing = confirm\n"
        )

    def test_a_missing_section_is_appended(self) -> None:
        updated = set_just_works_repairing("[LE]\n#MinConnectionInterval = 40\n")
        self.assertIn("[General]\nJustWorksRepairing = confirm\n", updated)
        self.assertTrue(updated.startswith("[LE]\n"))

    def test_the_rewrite_is_idempotent(self) -> None:
        once = set_just_works_repairing(STOCK)
        self.assertEqual(set_just_works_repairing(once), once)

    def test_reading_reports_the_effective_value_not_the_comment(self) -> None:
        self.assertEqual(read_just_works_repairing_text(STOCK), "never")
        self.assertEqual(
            read_just_works_repairing_text("[General]\nJustWorksRepairing = always\n"),
            "always",
        )


class LeIntervalTextTests(unittest.TestCase):
    def test_the_bridge_asks_for_the_esp32_15_ms_link(self) -> None:
        self.assertEqual(LE_MIN_CONNECTION_INTERVAL, 12)
        self.assertEqual(LE_MAX_CONNECTION_INTERVAL, 15)

    def test_stock_le_comments_are_replaced_in_place(self) -> None:
        updated = set_le_connection_intervals(STOCK)
        self.assertIn("MinConnectionInterval = 12\n", updated)
        self.assertIn("MaxConnectionInterval = 15\n", updated)
        self.assertNotIn("#MinConnectionInterval", updated)

    def test_a_missing_le_section_is_appended(self) -> None:
        updated = set_le_connection_intervals("[General]\nJustWorksRepairing = confirm\n")
        self.assertIn("[LE]\nMinConnectionInterval = 12\n", updated)
        self.assertIn("MaxConnectionInterval = 15\n", updated)

    def test_the_le_rewrite_is_idempotent(self) -> None:
        once = set_le_connection_intervals(STOCK)
        self.assertEqual(set_le_connection_intervals(once), once)

    def test_reading_intervals_ignores_the_stock_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"
            path.write_text(STOCK, encoding="utf-8")
            self.assertIsNone(read_le_connection_intervals(path))
            path.write_text(set_le_connection_intervals(STOCK), encoding="utf-8")
            self.assertEqual(read_le_connection_intervals(path), (12, 15))

    def test_ensure_applies_both_settings_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"
            path.write_text(STOCK, encoding="utf-8")

            self.assertTrue(ensure_bluez_config(path))
            text = path.read_text(encoding="utf-8")
            self.assertIn("JustWorksRepairing = confirm", text)
            self.assertIn("MinConnectionInterval = 12", text)
            self.assertIn("MaxConnectionInterval = 15", text)
            self.assertFalse(ensure_bluez_config(path))


def read_just_works_repairing_text(text: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "main.conf"
        path.write_text(text, encoding="utf-8")
        return read_just_works_repairing(path)


class EnsureFileTests(unittest.TestCase):
    def test_rewrites_a_file_and_preserves_its_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"
            path.write_text(STOCK, encoding="utf-8")
            path.chmod(0o640)

            self.assertTrue(ensure_just_works_repairing(path))
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertIn("JustWorksRepairing = confirm", path.read_text(encoding="utf-8"))

            # Already good: the second call must not rewrite the file.
            self.assertFalse(ensure_just_works_repairing(path))

    def test_an_explicit_always_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"
            text = "[General]\nJustWorksRepairing = always\n"
            path.write_text(text, encoding="utf-8")
            self.assertFalse(ensure_just_works_repairing(path))
            self.assertEqual(path.read_text(encoding="utf-8"), text)

    def test_a_missing_bluetooth_directory_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing" / "main.conf"
            self.assertFalse(ensure_just_works_repairing(path))
            self.assertFalse(path.parent.exists())

    def test_the_cli_sets_the_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"
            path.write_text(STOCK, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(CONF_MODULE), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn("JustWorksRepairing", proc.stdout)
            text = path.read_text(encoding="utf-8")
            self.assertIn("JustWorksRepairing = confirm", text)
            self.assertIn("MinConnectionInterval = 12", text)
            self.assertIn("MaxConnectionInterval = 15", text)

    def test_the_cli_check_says_when_a_bluetooth_restart_is_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.conf"

            # An absent main.conf has no policy to apply and needs no restart.
            missing = subprocess.run(
                [sys.executable, str(CONF_MODULE), "--check", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(missing.returncode, 0, msg=missing.stderr)

            path.write_text(STOCK, encoding="utf-8")
            needs_rewrite = subprocess.run(
                [sys.executable, str(CONF_MODULE), "--check", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(needs_rewrite.returncode, 1)

            ensure_bluez_config(path)
            configured = subprocess.run(
                [sys.executable, str(CONF_MODULE), "--check", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(configured.returncode, 0, msg=configured.stderr)


if __name__ == "__main__":
    unittest.main()
