"""Keep BlueZ's phone-facing policy and LE link timing usable by the bridge.

When the adapter still holds a bond for a phone and that phone starts a fresh
Just Works pairing, the kernel raises ``MGMT User Confirmation Request`` with
confirm hint ``0x01``.  bluetoothd's default ``JustWorksRepairing = never``
answers that request with ``User Confirmation Negative Reply`` itself, so the
phone reports ``SMP Pairing Failed: Numeric comparison failed (0x0c)`` and the
bridge's ``NoInputNoOutput`` agent is never asked.

``JustWorksRepairing = confirm`` sends the same request to the default agent,
where :class:`wmr_bridge.ble_gatt.PairingAgentPolicy` accepts it.  This is the
ESP32 lesson: a phone that forgot the bridge may pair again and
replace the stale key instead of being refused.  Keep ``always`` if an admin
selected it, because that also repairs without the agent.

The module also sets ``[LE] MinConnectionInterval = 12`` and
``MaxConnectionInterval = 15`` (BlueZ's 1.25 ms units, so a 15-20 ms link).
BlueZ's long default interval starves BLE HID and FF11/FF12/FF14
notifications while the Pi's shared combo radio serves two Classic controller
links; the ESP32 requests the same 15 ms interval for its HID gamepad.

This module is stdlib-only and safe to import from the daemon or run as a
script from the package maintainer scripts:

    python3 -m wmr_bridge.bluez_conf [/etc/bluetooth/main.conf]
    python3 -m wmr_bridge.bluez_conf --check [/etc/bluetooth/main.conf]

``--check`` exits 0 when the policy and interval already match (or the file is
absent, so there is nothing to apply) and 1 when ``main.conf`` needs rewriting.
The maintainer scripts use it to avoid restarting ``bluetooth.service`` on an
upgrade that does not change the policy or the interval bounds: a bluetoothd
restart while the bridge holds a GATT application and Classic scans can wedge
the Pi's combo controller.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

DEFAULT_MAIN_CONF = Path("/etc/bluetooth/main.conf")
JUST_WORKS_REPAIRING = "confirm"
# 12 and 15 are BlueZ's 1.25 ms units, so a 15-20 ms LE interval: the same
# 15 ms link the ESP32 requests for its HID gamepad. BlueZ defaults to a much
# longer interval, which starves BLE HID and FF11/FF12/FF14 notifications while
# the Pi's shared combo radio serves two Classic controller links.
LE_MIN_CONNECTION_INTERVAL = 12
LE_MAX_CONNECTION_INTERVAL = 15
_ACCEPTED = frozenset(("confirm", "always"))
_KEY_RE = re.compile(
    r"^(\s*)(?:#\s*)?JustWorksRepairing\s*=\s*(.*?)\s*$", re.IGNORECASE
)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def _value_from_text(text: str) -> str:
    """Return the effective uncommented ``JustWorksRepairing`` value."""

    in_general = False
    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            in_general = section.group(1).strip().lower() == "general"
            continue
        if not in_general or line.lstrip().startswith("#"):
            continue
        key = _KEY_RE.match(line)
        if key:
            return (key.group(2) or "never").lower()
    return "never"


def read_just_works_repairing(path: Path | str = DEFAULT_MAIN_CONF) -> str:
    """Read the policy bluetoothd would use, defaulting to ``never``."""

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "never"
    return _value_from_text(text)


def _key_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^(\s*)(?:#\s*)?{re.escape(key)}\s*=\s*(.*?)\s*$", re.IGNORECASE)


def _read_option(text: str, section_name: str, key: str) -> str | None:
    """Return the effective uncommented *key* in *section_name*."""

    in_section = False
    for line in text.splitlines():
        section = _SECTION_RE.match(line)
        if section:
            in_section = section.group(1).strip().lower() == section_name.lower()
            continue
        if not in_section or line.lstrip().startswith("#"):
            continue
        match = _key_re(key).match(line)
        if match:
            return (match.group(2) or "").strip()
    return None


def _set_option(text: str, section_name: str, key: str, value: str) -> str:
    """Return *text* with ``[section_name] key = value`` applied in place.

    A commented stock entry is replaced, a missing key is added at the end of
    the section (so related options keep their order), and a missing section is
    appended. Other lines are left byte-for-byte alone.
    """

    lines = text.splitlines()
    out: list[str] = []
    in_section = False
    seen_section = False
    written = False
    pattern = _key_re(key)

    def finish_section() -> None:
        nonlocal written
        if not written:
            out.append(f"{key} = {value}")
            written = True

    for line in lines:
        section = _SECTION_RE.match(line)
        if section:
            if in_section:
                finish_section()
            in_section = section.group(1).strip().lower() == section_name.lower()
            if in_section:
                seen_section = True
            out.append(line)
            continue
        if in_section:
            match = pattern.match(line)
            if match:
                if not written:
                    out.append(f"{match.group(1)}{key} = {value}")
                    written = True
                continue
        out.append(line)

    if in_section:
        finish_section()
    if not seen_section:
        if out and out[-1].strip():
            out.append("")
        out.append(f"[{section_name}]")
        out.append(f"{key} = {value}")

    result = "\n".join(out)
    if text.endswith("\n") or not text:
        result += "\n"
    return result


def read_le_connection_intervals(
    path: Path | str = DEFAULT_MAIN_CONF,
) -> tuple[int, int] | None:
    """Read BlueZ's LE connection interval bounds, or None when unset/invalid."""

    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    minimum = _read_option(text, "LE", "MinConnectionInterval")
    maximum = _read_option(text, "LE", "MaxConnectionInterval")
    try:
        return int(minimum), int(maximum)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def set_le_connection_intervals(
    text: str,
    minimum: int = LE_MIN_CONNECTION_INTERVAL,
    maximum: int = LE_MAX_CONNECTION_INTERVAL,
) -> str:
    """Return *text* with BlueZ's LE interval bounds set to *minimum*/*maximum*."""

    text = _set_option(text, "LE", "MinConnectionInterval", str(minimum))
    return _set_option(text, "LE", "MaxConnectionInterval", str(maximum))


def set_just_works_repairing(
    text: str, value: str = JUST_WORKS_REPAIRING
) -> str:
    """Return *text* with ``[General] JustWorksRepairing`` set to *value*.

    A commented stock entry is replaced in place, a missing key is inserted
    under ``[General]``, and a missing section is appended.  Other lines are
    left byte-for-byte alone so a distribution's ``main.conf`` keeps its
    settings.
    """

    lines = text.splitlines()
    out: list[str] = []
    in_general = False
    seen_general = False
    key_written = False

    for line in lines:
        section = _SECTION_RE.match(line)
        if section:
            in_general = section.group(1).strip().lower() == "general"
            out.append(line)
            if in_general and not seen_general:
                seen_general = True
                out.append(f"JustWorksRepairing = {value}")
                key_written = True
            continue

        if in_general:
            key = _KEY_RE.match(line)
            if key:
                if not key_written:
                    out.append(f"{key.group(1)}JustWorksRepairing = {value}")
                    key_written = True
                # Drop a duplicate or now-obsolete commented entry.
                continue

        out.append(line)

    if not seen_general:
        if out and out[-1].strip():
            out.append("")
        out.append("[General]")
        out.append(f"JustWorksRepairing = {value}")

    result = "\n".join(out)
    if text.endswith("\n") or not text:
        result += "\n"
    return result


def _write_config(path: Path, updated: str) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(updated)
        if path.exists():
            # Keep the distribution's permission bits (normally 0644).
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_just_works_repairing(
    path: Path | str = DEFAULT_MAIN_CONF,
    value: str = JUST_WORKS_REPAIRING,
) -> bool:
    """Make sure *path* lets a phone re-pair over a stored bond.

    Returns True when the file was rewritten.  An explicit ``confirm`` or
    ``always`` is left alone; every other value (including the stock
    commented ``never``) is replaced with *value*.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not path.parent.is_dir():
            return False
        text = ""

    if _value_from_text(text) in _ACCEPTED:
        return False

    _write_config(path, set_just_works_repairing(text, value))
    return True


def ensure_bluez_config(
    path: Path | str = DEFAULT_MAIN_CONF,
    *,
    minimum: int = LE_MIN_CONNECTION_INTERVAL,
    maximum: int = LE_MAX_CONNECTION_INTERVAL,
) -> bool:
    """Apply the re-pair policy and the short LE connection interval bounds.

    Returns True when the file was rewritten. The LE bounds are 12/15 in
    BlueZ's 1.25 ms units, so the phone links at 15-20 ms instead of the much
    longer default that starved BLE HID and FF1x notifications.
    """

    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not path.parent.is_dir():
            return False
        text = ""

    current = _value_from_text(text)
    policy_ok = current in _ACCEPTED
    intervals_ok = read_le_connection_intervals(path) == (minimum, maximum)
    if policy_ok and intervals_ok:
        return False

    # Keep an accepted value (an admin's ``always``) when only the LE bounds
    # are missing; rewriting it to ``confirm`` would silently regress the
    # upgrade path from a release that already carries the policy.
    policy = current if policy_ok else JUST_WORKS_REPAIRING
    updated = set_le_connection_intervals(
        set_just_works_repairing(text, policy), minimum, maximum
    )
    _write_config(path, updated)
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--check":
        path = Path(args[1]) if len(args) > 1 else DEFAULT_MAIN_CONF
        if not path.exists():
            # No BlueZ config on this host: there is nothing to rewrite and no
            # bluetoothd to restart.
            return 0
        configured = (
            read_just_works_repairing(path) in _ACCEPTED
            and read_le_connection_intervals(path)
            == (LE_MIN_CONNECTION_INTERVAL, LE_MAX_CONNECTION_INTERVAL)
        )
        return 0 if configured else 1
    path = Path(args[0]) if args else DEFAULT_MAIN_CONF
    if not path.exists():
        print(f"wmr-bridge: {path} not found; leaving BlueZ's default policy alone")
        return 0
    try:
        changed = ensure_bluez_config(path)
    except OSError as exc:
        print(f"wmr-bridge: could not update {path}: {exc}", file=sys.stderr)
        return 1
    state = "set" if changed else "already configured"
    print(
        f"wmr-bridge: BlueZ JustWorksRepairing {state} and LE interval "
        f"{LE_MIN_CONNECTION_INTERVAL}-{LE_MAX_CONNECTION_INTERVAL}*1.25ms "
        f"in {path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
