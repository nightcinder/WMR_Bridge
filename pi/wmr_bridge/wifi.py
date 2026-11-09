"""Wi-Fi radio inspection and a non-lockout NetworkManager 5 GHz preference.

Bluetooth Classic lives in the 2.4 GHz band. On a Pi whose Wi-Fi is also on
2.4 GHz the two share the radio, so the bridge prefers a 5 GHz BSSID when one
is available. The preference is deliberately non-lockout:

* the user's existing connection profile is left untouched and keeps working
  as the 2.4 GHz fallback;
* a separate ``<connection>-5ghz`` profile is cloned from it, restricted to
  ``802-11-wireless.band=a`` and given a higher autoconnect priority;
* the bridge only switches to the new profile when a 5 GHz BSSID is actually
  visible, and a failed switch leaves the current connection alone;
* a 2.4 GHz-only radio (for example the CYW43439 in a Pi Zero 2 W) is a
  warning-only no-op.

The inspection result never contains the SSID. It is logged and returned to
clients often enough that leaking the network name into support output would
be unnecessary.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

LOG = logging.getLogger("wmr_bridge.wifi")

IW = "iw"
NMCLI = "nmcli"

# 5 GHz runs from 4.9 GHz to just below the 6 GHz band (5925 MHz).
FIVE_GHZ_MIN_MHZ = 4900
FIVE_GHZ_MAX_MHZ = 5925
TWO_GHZ_MIN_MHZ = 2400
TWO_GHZ_MAX_MHZ = 2500
PREFERRED_PRIORITY = 50
FIVE_GHZ_PROFILE_SUFFIX = "-5ghz"

_INTERFACE_RE = re.compile(r"^\s*Interface\s+(\S+)\s*$")
_PHY_RE = re.compile(r"^\s*phy#(\d+)\s*$")
_CHANNEL_RE = re.compile(r"\bchannel\s+\d+\s+\((\d+)\s*MHz\)")
_FREQ_RE = re.compile(r"\bfreq(?:uency)?\s*:?\s*(\d+(?:\.\d+)?)\s*MHz")
_WIPHY_RE = re.compile(r"^\s*Wiphy\s+phy(\d+)\s*$")
_FREQ_LINE_RE = re.compile(r"^\s*\*\s*(\d+(?:\.\d+)?)\s*MHz\b")
_MHZ_RE = re.compile(r"(\d{4,5})\s*MHz\b")

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one inspection/NetworkManager command without raising."""

    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", str(exc))


def _band_for(freq_mhz: int | None) -> str | None:
    if freq_mhz is None:
        return None
    if TWO_GHZ_MIN_MHZ <= freq_mhz <= TWO_GHZ_MAX_MHZ:
        return "2.4GHz"
    if FIVE_GHZ_MIN_MHZ <= freq_mhz <= FIVE_GHZ_MAX_MHZ:
        return "5GHz"
    if freq_mhz > FIVE_GHZ_MAX_MHZ:
        return "6GHz"
    return None


def _parse_iw_dev(text: str) -> list[dict[str, Any]]:
    """Return one dict per ``iw dev`` interface block."""

    interfaces: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    phy: str | None = None
    for line in text.splitlines():
        phy_match = _PHY_RE.match(line)
        if phy_match:
            phy = phy_match.group(1)
            continue
        iface_match = _INTERFACE_RE.match(line)
        if iface_match:
            current = {
                "iface": iface_match.group(1),
                "phy": phy,
                "type": None,
                "freqMhz": None,
                "connected": False,
            }
            interfaces.append(current)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("type "):
            current["type"] = stripped.split(None, 1)[1]
        elif stripped.startswith("ssid "):
            # Presence of an SSID means associated; the value is discarded.
            current["connected"] = True
        else:
            channel = _CHANNEL_RE.search(line)
            if channel:
                current["freqMhz"] = int(channel.group(1))
                current["connected"] = True
                continue
            freq = _FREQ_RE.search(line)
            if freq:
                current["freqMhz"] = int(float(freq.group(1)))
                current["connected"] = True
    return interfaces


def _parse_iw_phy(text: str) -> dict[str, list[int]]:
    """Map ``phy`` name/number to the frequencies its wiphy advertises."""

    frequencies: dict[str, list[int]] = {}
    current: str | None = None
    for line in text.splitlines():
        wiphy = _WIPHY_RE.match(line)
        if wiphy:
            current = wiphy.group(1)
            frequencies.setdefault(current, [])
            continue
        match = _FREQ_LINE_RE.match(line)
        if match:
            freq = int(float(match.group(1)))
            if current is None:
                current = ""
                frequencies.setdefault(current, [])
            frequencies[current].append(freq)
    return frequencies


def _supports_5ghz(frequencies: Iterable[int]) -> bool:
    return any(FIVE_GHZ_MIN_MHZ <= freq <= FIVE_GHZ_MAX_MHZ for freq in frequencies)


def _choose_interface(
    interfaces: list[dict[str, Any]], requested: str | None
) -> dict[str, Any] | None:
    if requested is not None:
        for interface in interfaces:
            if interface["iface"] == requested:
                return interface
        return None
    for interface in interfaces:
        if interface.get("type") == "managed":
            return interface
    return interfaces[0] if interfaces else None


def inspect_wifi(
    iface: str | None = None,
    *,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Describe the active Wi-Fi interface and its band without exposing the SSID.

    The result always has the keys ``iface``, ``connected``, ``freqMhz``,
    ``band``, ``supports5Ghz``, ``contentionWarning`` and ``warning``.
    ``supports5Ghz`` is ``None`` when the radio capabilities could not be
    read, so callers never mistake "unknown" for "2.4 GHz only". ``warning``
    is ``None`` when there is nothing to report.
    """

    run = runner or _default_runner
    dev_result = run([IW, "dev"])
    interfaces = (
        _parse_iw_dev(dev_result.stdout) if dev_result.returncode == 0 else []
    )
    selected = _choose_interface(interfaces, iface)

    if selected is None:
        if iface is not None:
            warning = f"Wi-Fi interface {iface} not found."
        elif dev_result.returncode != 0:
            warning = "iw is not available, so the Wi-Fi radio could not be inspected."
        else:
            warning = "No Wi-Fi interface found."
        return {
            "iface": iface,
            "connected": False,
            "freqMhz": None,
            "band": None,
            "supports5Ghz": None,
            "contentionWarning": False,
            "warning": warning,
        }

    phy_result = run([IW, "phy"])
    phy_frequencies: list[int] = []
    if phy_result.returncode == 0:
        by_phy = _parse_iw_phy(phy_result.stdout)
        phy = str(selected.get("phy") or "")
        if phy in by_phy:
            phy_frequencies = by_phy[phy]
        else:
            for freqs in by_phy.values():
                phy_frequencies.extend(freqs)

    freq_mhz = selected["freqMhz"]
    band = _band_for(freq_mhz)
    supports_5ghz: bool | None
    if phy_result.returncode == 0 and phy_frequencies:
        supports_5ghz = _supports_5ghz(phy_frequencies) or band == "5GHz"
    elif band == "5GHz":
        # Already associated on 5 GHz, so support is proven without `iw phy`.
        supports_5ghz = True
    else:
        supports_5ghz = None
    connected = bool(selected["connected"])

    warning: str | None = None
    contention_warning = connected and band == "2.4GHz"
    if not connected:
        warning = "Wi-Fi is not connected."
    elif contention_warning:
        if supports_5ghz:
            warning = (
                "Wi-Fi is connected on 2.4 GHz, which shares the radio with "
                "Bluetooth; prefer a 5 GHz network."
            )
        elif supports_5ghz is False:
            warning = (
                "Wi-Fi is connected on 2.4 GHz and this adapter has no 5 GHz "
                "radio; Bluetooth shares that band."
            )
        else:
            warning = (
                "Wi-Fi is connected on 2.4 GHz, which shares the radio with "
                "Bluetooth, but 5 GHz support could not be determined."
            )

    return {
        "iface": selected["iface"],
        "connected": connected,
        "freqMhz": freq_mhz,
        "band": band,
        "supports5Ghz": supports_5ghz,
        "contentionWarning": contention_warning,
        "warning": warning,
    }


def _unescape_nmcli(value: str) -> str:
    return value.replace("\\:", ":").replace("\\\\", "\\")


def _active_connection(text: str, iface: str) -> str | None:
    """Return the connection profile active on ``iface``, if any."""

    for line in text.splitlines():
        name, separator, device = line.rpartition(":")
        if separator and device == iface:
            return _unescape_nmcli(name)
    return None


def _connection_names(text: str) -> set[str]:
    return {
        _unescape_nmcli(line.strip())
        for line in text.splitlines()
        if line.strip()
    }


def _has_5ghz_bssid(text: str) -> bool:
    for line in text.splitlines():
        for match in _MHZ_RE.finditer(line):
            if FIVE_GHZ_MIN_MHZ <= int(match.group(1)) <= FIVE_GHZ_MAX_MHZ:
                return True
    return False


def prefer_5ghz(
    reconnect: bool = True,
    *,
    iface: str | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Create or update a ``<connection>-5ghz`` NetworkManager profile.

    The base profile is left in place as the 2.4 GHz fallback. With
    ``reconnect`` true the new profile is activated only when a 5 GHz BSSID is
    visible; otherwise the current connection is untouched. A radio with no
    5 GHz support is a warning-only no-op.
    """

    run = runner or _default_runner
    info = inspect_wifi(iface, runner=run)
    result: dict[str, Any] = {
        "iface": info["iface"],
        "band": info["band"],
        "supports5Ghz": info["supports5Ghz"],
        "profile": None,
        "baseProfile": None,
        "changed": False,
        "reconnected": False,
        "warning": info["warning"],
    }

    if info["iface"] is None:
        return result
    if not info["supports5Ghz"]:
        if info["supports5Ghz"] is False and info["band"] == "2.4GHz":
            result["warning"] = (
                "Wi-Fi adapter is 2.4 GHz only; Bluetooth shares that band and "
                "5 GHz cannot be preferred."
            )
        else:
            # Support is unknown (missing interface, unreadable `iw phy`):
            # keep the inspection warning instead of claiming 2.4 GHz only.
            result["warning"] = info["warning"] or (
                "Wi-Fi 5 GHz support could not be determined; the current "
                "connection was left in place."
            )
        return result
    if not info["connected"]:
        result["warning"] = "Wi-Fi is not connected; no NetworkManager profile to clone."
        return result

    active = run(
        [NMCLI, "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"]
    )
    base_profile = (
        _active_connection(active.stdout, info["iface"])
        if active.returncode == 0
        else None
    )
    if base_profile is None:
        result["warning"] = "Could not determine the active NetworkManager connection."
        return result
    result["baseProfile"] = base_profile

    profile = f"{base_profile}{FIVE_GHZ_PROFILE_SUFFIX}"
    result["profile"] = profile
    names_result = run([NMCLI, "-t", "-f", "NAME", "connection", "show"])
    names = (
        _connection_names(names_result.stdout)
        if names_result.returncode == 0
        else set()
    )

    if profile in names:
        changed = run(
            [
                NMCLI,
                "connection",
                "modify",
                profile,
                "802-11-wireless.band",
                "a",
                "connection.autoconnect-priority",
                str(PREFERRED_PRIORITY),
            ]
        )
    else:
        created = run([NMCLI, "connection", "clone", base_profile, profile])
        if created.returncode != 0:
            result["warning"] = (
                f"Could not create the 5 GHz profile {profile}: "
                f"{created.stderr.strip() or 'nmcli failed'}"
            )
            return result
        changed = run(
            [
                NMCLI,
                "connection",
                "modify",
                profile,
                "802-11-wireless.band",
                "a",
                "connection.autoconnect-priority",
                str(PREFERRED_PRIORITY),
            ]
        )

    if changed.returncode != 0:
        result["warning"] = (
            f"Could not update the 5 GHz profile {profile}: "
            f"{changed.stderr.strip() or 'nmcli failed'}"
        )
        return result
    result["changed"] = True

    if not reconnect or info["band"] == "5GHz":
        return result

    visible = run(
        [NMCLI, "-t", "-f", "BSSID,FREQ", "device", "wifi", "list", "ifname", info["iface"]]
    )
    if visible.returncode == 0 and _has_5ghz_bssid(visible.stdout):
        activated = run(
            [NMCLI, "connection", "up", profile, "ifname", info["iface"]]
        )
        if activated.returncode == 0:
            result["reconnected"] = True
            result["warning"] = None
        else:
            result["warning"] = (
                f"Could not activate {profile}; the current connection was left "
                f"in place: {activated.stderr.strip() or 'nmcli failed'}"
            )
    else:
        result["warning"] = (
            "No 5 GHz BSSID is visible; the profile was saved and the current "
            "connection was left in place."
        )
    return result
