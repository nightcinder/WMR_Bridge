"""Keep BlueZ from rejecting a phone's Just Works re-pair before our agent runs.

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

This module is stdlib-only and safe to import from the daemon or run as a
script from the package maintainer scripts:

    python3 -m wmr_bridge.bluez_conf [/etc/bluetooth/main.conf]
    python3 -m wmr_bridge.bluez_conf --check [/etc/bluetooth/main.conf]

``--check`` exits 0 when the policy already permits re-pairing (or the file is
absent, so there is nothing to apply) and 1 when ``main.conf`` needs rewriting.
The maintainer scripts use it to avoid restarting ``bluetooth.service`` on an
upgrade that does not change the policy: a bluetoothd restart while the bridge
holds a GATT application and Classic scans can wedge the Pi's combo controller.
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

    updated = set_just_works_repairing(text, value)
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
    return True


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--check":
        path = Path(args[1]) if len(args) > 1 else DEFAULT_MAIN_CONF
        if not path.exists():
            # No BlueZ config on this host: there is nothing to rewrite and no
            # bluetoothd to restart.
            return 0
        return 0 if read_just_works_repairing(path) in _ACCEPTED else 1
    path = Path(args[0]) if args else DEFAULT_MAIN_CONF
    if not path.exists():
        print(f"wmr-bridge: {path} not found; leaving BlueZ's default policy alone")
        return 0
    try:
        changed = ensure_just_works_repairing(path)
    except OSError as exc:
        print(f"wmr-bridge: could not update {path}: {exc}", file=sys.stderr)
        return 1
    state = "set" if changed else "already permits re-pairing"
    print(f"wmr-bridge: BlueZ JustWorksRepairing {state} in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
