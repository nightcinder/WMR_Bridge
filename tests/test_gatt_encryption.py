#!/usr/bin/env python3
"""Assert the FF10 data characteristics require a 16-byte encryption key.

BTstack's att_validate_security() only rejects an unencrypted access when the
attribute's encryption-key-size nibble (bits 12..15 of the ATT flags) is
non-zero.  A bare READ_ENCRYPTED / WRITE_ENCRYPTED flag therefore compiles to a
permission bit that an unencrypted link passes.  This test compiles
wmr_bridge.gatt with the same tool the firmware build uses and inspects the
generated attribute records, not the source flags: every FF10 value
characteristic except FF16, and every CCC descriptor under FF10, must carry
nibble 0xf (key size 16).  FF16 must stay at 0.

The compiler checkout normally comes from the firmware build's
`.fw-build/btstack`.  CI's host-tests job does not build firmware, so when that
checkout is absent the test fetches the three files the compiler needs at the
pinned BTSTACK_COMMIT instead of cloning the tree.

Run: python3 tests/test_gatt_encryption.py [--gatt PATH] [--btstack-root PATH]
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATT = ROOT / "wmr_bridge.gatt"
BUILD_PY = ROOT / "tools" / "firmware" / "build.py"
LOCAL_BTSTACK = ROOT / ".fw-build" / "btstack"
RAW_URL = "https://raw.githubusercontent.com/bluekitchen/btstack/{commit}/{path}"

# Files compile_gatt.py reads for this .gatt.  Fetching these instead of the
# whole BTstack tree keeps the host-tests job cheap.
COMPILER_FILES = (
    "tool/compile_gatt.py",
    "src/bluetooth_gatt.h",
    "src/ble/gatt-service/hids.gatt",
)

FF10_SERVICE = "0000ff1000001000800000805f9b34fb"
FF16_VALUE = "0000ff1600001000800000805f9b34fb"
# FF10 data characteristics that carry motion, config or the control plane.
# FF16 is deliberately plaintext and checked separately.
FF10_ENCRYPTED_VALUES = {
    "0000ff1100001000800000805f9b34fb",
    "0000ff1200001000800000805f9b34fb",
    "0000ff1300001000800000805f9b34fb",
    "0000ff1400001000800000805f9b34fb",
    "0000ff1500001000800000805f9b34fb",
    "0000ff1700001000800000805f9b34fb",
}

PROFILE_RE = re.compile(r"profile_data\[\]\s*=\s*\{(.*?)\};", re.S)
BYTE_RE = re.compile(r"0x([0-9a-fA-F]{1,2})")
COMMIT_RE = re.compile(r'^BTSTACK_COMMIT\s*=\s*"([0-9a-f]{40})"', re.M)


@dataclass(frozen=True)
class Record:
    handle: int
    flags: int
    uuid: str
    value: bytes

    @property
    def key_size(self) -> int:
        """Key size in bytes, or 0 when the nibble is absent."""
        nibble = (self.flags >> 12) & 0xF
        return nibble + 1 if nibble else 0


def uuid_hex(raw: bytes) -> str:
    """Canonical lowercase UUID from BTstack's little-endian representation."""
    return raw[::-1].hex()


def read_btstack_commit() -> str:
    match = COMMIT_RE.search(BUILD_PY.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit(f"cannot find BTSTACK_COMMIT in {BUILD_PY}")
    return match.group(1)


def prepare_compiler(tmp: Path, btstack_root: Path | None) -> Path:
    """Return compile_gatt.py, downloading only what it needs if necessary."""
    if btstack_root is None:
        candidate = Path(os.environ.get("WMR_BTSTACK_ROOT", LOCAL_BTSTACK))
        if (candidate / "tool" / "compile_gatt.py").is_file():
            btstack_root = candidate
    if btstack_root is not None:
        compiler = btstack_root / "tool" / "compile_gatt.py"
        if not compiler.is_file():
            raise SystemExit(f"no compile_gatt.py under {btstack_root}")
        return compiler

    commit = read_btstack_commit()
    root = tmp / "btstack"
    for relative in COMPILER_FILES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = RAW_URL.format(commit=commit, path=relative)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                destination.write_bytes(response.read())
        except OSError as error:
            raise SystemExit(f"cannot fetch {url}: {error}") from error
    return root / "tool" / "compile_gatt.py"


def compile_gatt(compiler: Path, gatt: Path, output: Path) -> None:
    # The compiler finds bluetooth_gatt.h and #import files relative to its own
    # parent directory, exactly as the ESP-IDF custom command does.
    result = subprocess.run(
        [sys.executable, str(compiler), str(gatt), str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(
            f"compile_gatt.py failed with exit code {result.returncode}"
        )


def parse_records(header: str) -> dict[int, Record]:
    match = PROFILE_RE.search(header)
    if not match:
        raise SystemExit("generated header has no profile_data array")
    block = re.sub(r"//[^\n]*", "", match.group(1))
    data = bytes(int(token, 16) for token in BYTE_RE.findall(block))

    records: dict[int, Record] = {}
    offset = 0
    while offset + 6 <= len(data):
        size = int.from_bytes(data[offset : offset + 2], "little")
        if size == 0:
            break
        if size < 6 or offset + size > len(data):
            raise SystemExit(f"malformed attribute record at byte {offset}")
        flags = int.from_bytes(data[offset + 2 : offset + 4], "little")
        handle = int.from_bytes(data[offset + 4 : offset + 6], "little")
        uuid_length = 16 if flags & 0x0200 else 2
        uuid_bytes = data[offset + 6 : offset + 6 + uuid_length]
        value = data[offset + 6 + uuid_length : offset + size]
        records[handle] = Record(handle, flags, uuid_hex(uuid_bytes), value)
        offset += size
    if not records:
        raise SystemExit("generated header has no attribute records")
    return records


def check(gatt: Path, records: dict[int, Record]) -> list[str]:
    starts = [
        record.handle
        for record in records.values()
        if record.uuid == "2800" and uuid_hex(record.value) == FF10_SERVICE
    ]
    if len(starts) != 1:
        return [f"{gatt}: expected one FF10 service, found {len(starts)}"]
    start = starts[0]
    later_services = [
        record.handle
        for record in records.values()
        if record.uuid == "2800" and record.handle > start
    ]
    end = min(later_services) - 1 if later_services else max(records)

    failures: list[str] = []
    ff10 = [record for record in records.values() if start <= record.handle <= end]
    values = [record for record in ff10 if record.uuid in FF10_ENCRYPTED_VALUES]
    others = [
        record
        for record in ff10
        if record.uuid in (FF16_VALUE, "2803", "2902")
        or record.uuid == "2800"
    ]
    unexpected = [
        record
        for record in ff10
        if record not in values and record not in others
    ]
    if unexpected:
        failures.append(
            "unexpected FF10 attributes: "
            + ", ".join(
                f"0x{record.handle:04x}/{record.uuid}" for record in unexpected
            )
        )

    found = {record.uuid for record in values}
    missing = FF10_ENCRYPTED_VALUES - found
    if missing:
        failures.append("missing encrypted FF10 values: " + ", ".join(sorted(missing)))
    if len(values) != len(FF10_ENCRYPTED_VALUES):
        failures.append(
            f"expected {len(FF10_ENCRYPTED_VALUES)} FF10 value attributes, "
            f"found {len(values)}"
        )

    for record in values:
        if record.key_size != 16:
            failures.append(
                f"FF10 value 0x{record.handle:04x} ({record.uuid}) has key size "
                f"{record.key_size}, expected 16 (ATT flags 0x{record.flags:04x})"
            )

    cccs = [record for record in ff10 if record.uuid == "2902"]
    if len(cccs) != len(FF10_ENCRYPTED_VALUES):
        failures.append(
            f"expected {len(FF10_ENCRYPTED_VALUES)} FF10 CCCs, found {len(cccs)}"
        )
    for record in cccs:
        if record.key_size != 16:
            failures.append(
                f"FF10 CCC 0x{record.handle:04x} has key size "
                f"{record.key_size}, expected 16 (ATT flags 0x{record.flags:04x})"
            )

    ff16 = [
        record
        for record in ff10
        if record.uuid == FF16_VALUE
    ]
    if len(ff16) != 1:
        failures.append(f"expected one FF16 value attribute, found {len(ff16)}")
    for record in ff16:
        if record.key_size != 0:
            failures.append(
                f"FF16 value 0x{record.handle:04x} must stay plaintext, has key "
                f"size {record.key_size} (ATT flags 0x{record.flags:04x})"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gatt",
        type=Path,
        default=DEFAULT_GATT,
        help="GATT file to compile (default: wmr_bridge.gatt)",
    )
    parser.add_argument(
        "--btstack-root",
        type=Path,
        help="existing BTstack checkout (default: .fw-build/btstack or a download)",
    )
    args = parser.parse_args()

    if not args.gatt.is_file():
        raise SystemExit(f"no such GATT file: {args.gatt}")

    with tempfile.TemporaryDirectory(prefix="gatt-encryption-") as temp:
        tmp = Path(temp)
        compiler = prepare_compiler(tmp, args.btstack_root)
        header_path = tmp / "wmr_bridge.h"
        compile_gatt(compiler, args.gatt, header_path)
        records = parse_records(header_path.read_text(encoding="utf-8"))

    failures = check(args.gatt, records)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1

    print(
        f"{args.gatt}: {len(FF10_ENCRYPTED_VALUES)} FF10 values and "
        f"{len(FF10_ENCRYPTED_VALUES)} CCCs require key size 16; FF16 plaintext"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
