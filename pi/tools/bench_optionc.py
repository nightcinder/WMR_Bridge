#!/usr/bin/env python3
"""Option C bench preflight gate and local-on-Pi btmon + journal capture helper.

Run on the Raspberry Pi before and during the single zero-dongle Option C BLE
failover bench:

    sudo python3 /opt/wmr-bridge/pi/tools/bench_optionc.py preflight
    sudo python3 /opt/wmr-bridge/pi/tools/bench_optionc.py capture --out-dir /var/tmp/wmr-bench --duration 60

``btmon.btsnoop`` stays local on the Pi and must never be pulled, committed, or
attached to issues or PRs. ``btmon.txt``, ``pi-journal.txt``, and ``summary.txt``
are redacted before writing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_PI_DIR = Path(__file__).resolve().parent.parent
if str(REPO_PI_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_PI_DIR))

from wmr_bridge.ble_gatt import (  # noqa: E402
    DEFAULT_BOOT_ID_PATH,
    default_state_dir,
    is_recovery_suppressed,
    read_ble_status,
)
from wmr_bridge.bluez import WMR_NAMES  # noqa: E402

DROPIN_DIRS = (
    Path("/etc/systemd/system/wmr-bridge.service.d"),
    Path("/run/systemd/system/wmr-bridge.service.d"),
    Path("/lib/systemd/system/wmr-bridge.service.d"),
    Path("/usr/lib/systemd/system/wmr-bridge.service.d"),
)
DEFAULT_ENV_FILE = Path("/etc/wmr-bridge/wmr-bridge.env")
FORBIDDEN_FLAGS = ("--no-ble", "--ble-allow-serial", "--no-ble-load-cuts")

MAC_COLON_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
MAC_DASH_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}\b")
MAC_DEV_RE = re.compile(r"\bdev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}\b")
KEY_LINE_RE = re.compile(
    r"(?i)(\b(?:Long Term Key|Link Key|Local IRK|Remote IRK|IRK|LTK|CSRK|Key|Rand|EDIV|Signature)\b\s*[:=]\s*)([0-9A-Fa-fxX\s:-]+)"
)
JSON_TOKEN_RE = re.compile(r'("token"\s*:\s*")([^"]*)(")')
ARG_TOKEN_RE = re.compile(r"(--token(?:=|\s+))(\S+)")
KV_TOKEN_RE = re.compile(r"(?i)(\btoken\s*=\s*)([^\s,;]+)")
BEARER_RE = re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9._~+/-]+=*)")

KERNEL_FAULT_PATTERNS = (
    re.compile(r"(?i)hardware\s+error(?:\s+code)?\s*[:=]?\s*0x00\b"),
    re.compile(r"(?i)\bbluetooth\b.*(?:-84\b|\bEILSEQ\b)"),
    re.compile(r"(?i)(?:0x0c03.*-110\b|opcode\s+0x0c03\s+failed:\s*-110\b)"),
)

LE_PDU_RE = re.compile(
    r"(?i)\b(?:(?:LE\s+)?ACL\s+Data\s+(?:RX|TX)|LE\s+(?:Data|Meta|Advertising|Connection)|ATT:|Handle Value Notification|Length:\s*\d+)"
)
LE_INTERVAL_RE = re.compile(
    r"(?i)(?:Connection\s+Interval|Conn(?:ection)?\s+interval)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?\s*ms(?:\s*\([^)]*\))?|[0-9]+)"
)
BTMON_FAULT_00_RE = re.compile(r"(?i)Hardware\s+Error.*0x00\b")
BTMON_FAULT_84_RE = re.compile(r"(?i)(?:-84\b|\bEILSEQ\b)")
BTMON_FAULT_110_RE = re.compile(r"(?i)(?:0x0c03.*-110\b|Command\s+Timeout|\b-110\b)")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


Runner = Callable[[Sequence[str]], CommandResult]


def _default_runner(cmd: Sequence[str]) -> CommandResult:
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(1, "", str(exc))


def redact_text(text: str) -> str:
    """Redact Bluetooth MACs, SMP/link keys, and FF13/auth tokens."""

    out = MAC_DEV_RE.sub("dev_[REDACTED_MAC]", text)
    out = MAC_COLON_RE.sub("[REDACTED_MAC]", out)
    out = MAC_DASH_RE.sub("[REDACTED_MAC]", out)
    out = KEY_LINE_RE.sub(r"\1[REDACTED_KEY]", out)
    out = JSON_TOKEN_RE.sub(r"\1[REDACTED_TOKEN]\3", out)
    out = ARG_TOKEN_RE.sub(r"\1[REDACTED_TOKEN]", out)
    out = KV_TOKEN_RE.sub(r"\1[REDACTED_TOKEN]", out)
    out = BEARER_RE.sub(r"\1[REDACTED_TOKEN]", out)
    return out


def _find_dropins(dropin_dirs: Sequence[Path], runner: Runner) -> list[str]:
    found: list[str] = []
    for directory in dropin_dirs:
        if directory.is_dir():
            for entry in sorted(directory.glob("*.conf")):
                found.append(str(entry))
    res = runner(["systemctl", "show", "-p", "DropInPaths", "--value", "wmr-bridge.service"])
    if res.returncode == 0 and res.stdout.strip():
        for token in res.stdout.split():
            if token not in found:
                found.append(token)
    return found


def _find_forbidden_overrides(env_file: Path, runner: Runner) -> list[str]:
    hits: list[str] = []
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("WMR_BRIDGE_ARGS="):
                    val = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    tokens = shlex.split(val)
                    for flag in FORBIDDEN_FLAGS:
                        if flag in tokens:
                            hits.append(f"{env_file}: {flag}")
        except OSError:
            pass
    res = runner(["systemctl", "show", "-p", "ExecStart", "--value", "wmr-bridge.service"])
    if res.returncode == 0 and res.stdout.strip():
        for flag in FORBIDDEN_FLAGS:
            if flag in res.stdout:
                hits.append(f"wmr-bridge.service ExecStart: {flag}")
    return hits


def _parse_active_instances(bt_show_output: str) -> int | None:
    match = re.search(
        r"ActiveInstances:\s*(?:0x[0-9a-fA-F]+\s*\((\d+)\)|(\d+))",
        bt_show_output,
    )
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    return int(raw)


def _has_active_bonded_le_link(runner: Runner) -> bool:
    devices_res = runner(["bluetoothctl", "devices", "Paired"])
    if devices_res.returncode != 0 or not devices_res.stdout.strip():
        devices_res = runner(["bluetoothctl", "devices"])
    if devices_res.returncode != 0:
        return False
    for line in devices_res.stdout.splitlines():
        if not line.startswith("Device "):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        mac = parts[1]
        name = parts[2].strip() if len(parts) > 2 else ""
        if name.casefold() in WMR_NAMES:
            continue
        info = runner(["bluetoothctl", "info", mac])
        if info.returncode != 0:
            continue
        text = info.stdout
        paired = "Paired: yes" in text
        connected = "Connected: yes" in text
        is_classic_hid = "00001124-0000-1000-8000-00805f9b34fb" in text.casefold()
        if paired and connected and not is_classic_hid:
            return True
    return False


def run_preflight(
    *,
    state_dir: Path | None = None,
    boot_id_path: Path = Path(DEFAULT_BOOT_ID_PATH),
    dropin_dirs: Sequence[Path] = DROPIN_DIRS,
    env_file: Path = DEFAULT_ENV_FILE,
    runner: Runner = _default_runner,
    which: Callable[[str], str | None] = shutil.which,
    pid_alive: Callable[[int], bool] | None = None,
) -> tuple[bool, list[str]]:
    """Run all Option C bench preflight gates. Returns ``(ok, messages)``."""

    errors: list[str] = []
    notes: list[str] = []
    effective_state_dir = state_dir or default_state_dir()

    if which("btmon") is None:
        errors.append("btmon is absent (install bluez)")

    dropins = _find_dropins(dropin_dirs, runner)
    if dropins:
        errors.append(
            f"wmr-bridge.service drop-in(s) present: {', '.join(dropins)}"
        )

    overrides = _find_forbidden_overrides(env_file, runner)
    if overrides:
        errors.append(
            f"forbidden BLE override(s) configured: {', '.join(overrides)}"
        )

    if is_recovery_suppressed(effective_state_dir, boot_id_path=boot_id_path):
        errors.append(
            "current-boot bt-recover.state.json suppression is active (#281 degraded)"
        )

    bt_show = runner(["bluetoothctl", "show"])
    if bt_show.returncode != 0 or "Powered: yes" not in bt_show.stdout:
        errors.append("Bluetooth adapter Powered: yes is missing")

    ble_status = (
        read_ble_status(
            effective_state_dir, boot_id_path=boot_id_path, pid_alive=pid_alive
        )
        if pid_alive is not None
        else read_ble_status(effective_state_dir, boot_id_path=boot_id_path)
    )
    if ble_status is None:
        errors.append(
            "live ble-status.json snapshot is missing or stale (is wmr-bridge running?)"
        )
    else:
        mode = ble_status.get("mode")
        load_cuts = ble_status.get("load_cuts")
        if not load_cuts:
            errors.append("Option C BLE load cuts are disabled")
        if mode != "active":
            errors.append(f"BLE peripheral mode is {mode!r}, expected 'active'")
        else:
            active_instances = _parse_active_instances(bt_show.stdout)
            if active_instances == 0:
                if _has_active_bonded_le_link(runner):
                    notes.append(
                        "ActiveInstances == 0 with active bonded LE link (pass)"
                    )
                else:
                    errors.append(
                        "BLE mode is active but ActiveInstances == 0 and no active bonded LE link is connected"
                    )

    klog = runner(["journalctl", "-k", "-b", "0", "--no-pager"])
    if klog.returncode == 0:
        for line in klog.stdout.splitlines():
            if any(pat.search(line) for pat in KERNEL_FAULT_PATTERNS):
                errors.append(
                    f"prior current-boot Bluetooth fault in kernel log: {redact_text(line.strip())}"
                )
                break

    if errors:
        return False, errors
    return True, notes or ["Option C preflight passed"]


def summarize_capture(btmon_text: str, journal_text: str) -> str:
    """Summarize decoded LE PDUs, connection intervals, and 0x00/-84/-110 faults."""

    redacted_bt = redact_text(btmon_text)
    redacted_journal = redact_text(journal_text)

    le_pdu_lines = [
        line for line in redacted_bt.splitlines() if LE_PDU_RE.search(line)
    ]
    intervals: list[str] = []
    for line in redacted_bt.splitlines():
        m = LE_INTERVAL_RE.search(line)
        if m:
            val = m.group(1).strip()
            if val not in intervals:
                intervals.append(val)

    combined = redacted_bt + "\n" + redacted_journal
    fault_00 = sum(1 for line in combined.splitlines() if BTMON_FAULT_00_RE.search(line))
    fault_84 = sum(1 for line in combined.splitlines() if BTMON_FAULT_84_RE.search(line))
    fault_110 = sum(1 for line in combined.splitlines() if BTMON_FAULT_110_RE.search(line))

    summary_lines = [
        "Option C btmon + journal summary (redacted)",
        f"le_pdu_events={len(le_pdu_lines)}",
        f"le_connection_intervals={', '.join(intervals) if intervals else 'none observed'}",
        f"fault_0x00_count={fault_00}",
        f"fault_minus84_count={fault_84}",
        f"fault_0x0c03_minus110_count={fault_110}",
        "raw_btsnoop_policy=local-only (never pull, commit, or attach)",
    ]
    return "\n".join(summary_lines) + "\n"


def finalize_capture(
    out_dir: Path,
    *,
    btsnoop_path: Path,
    raw_journal_path: Path,
    runner: Runner = _default_runner,
) -> tuple[bool, list[str]]:
    """Verify non-empty captures, decode/redact btmon & journal, and write summary."""

    if not btsnoop_path.is_file() or btsnoop_path.stat().st_size == 0:
        return False, [f"empty or missing btmon capture: {btsnoop_path}"]
    if not raw_journal_path.is_file() or raw_journal_path.stat().st_size == 0:
        return False, [f"empty or missing journal capture: {raw_journal_path}"]

    decoded = runner(["btmon", "-r", str(btsnoop_path)])
    if decoded.returncode != 0 or not decoded.stdout.strip():
        return False, [
            f"btmon -r failed or produced empty output ({decoded.stderr.strip() or decoded.returncode})"
        ]

    raw_journal_text = raw_journal_path.read_text(encoding="utf-8", errors="replace")
    redacted_btmon = redact_text(decoded.stdout)
    redacted_journal = redact_text(raw_journal_text)
    if not redacted_btmon.strip():
        return False, ["redacted btmon.txt is empty"]
    if not redacted_journal.strip():
        return False, ["redacted pi-journal.txt is empty"]

    btmon_txt = out_dir / "btmon.txt"
    journal_txt = out_dir / "pi-journal.txt"
    summary_txt = out_dir / "summary.txt"

    btmon_txt.write_text(redacted_btmon, encoding="utf-8")
    journal_txt.write_text(redacted_journal, encoding="utf-8")
    summary_txt.write_text(
        summarize_capture(redacted_btmon, redacted_journal), encoding="utf-8"
    )
    if raw_journal_path != journal_txt:
        raw_journal_path.unlink(missing_ok=True)

    return True, [
        f"wrote {btmon_txt} ({btmon_txt.stat().st_size} bytes)",
        f"wrote {journal_txt} ({journal_txt.stat().st_size} bytes)",
        f"wrote {summary_txt} ({summary_txt.stat().st_size} bytes)",
        f"kept local-only {btsnoop_path} (do not pull/commit/attach)",
    ]


def run_capture(
    out_dir: Path,
    *,
    duration_s: float = 30.0,
    state_dir: Path | None = None,
    runner: Runner = _default_runner,
    euid: Callable[[], int] = os.geteuid,
) -> int:
    if euid() != 0:
        print("FAIL: capture requires root (run with sudo)", file=sys.stderr)
        return 1
    ok, messages = run_preflight(state_dir=state_dir, runner=runner)
    for msg in messages:
        print(f"{'PASS' if ok else 'FAIL'}: {msg}")
    if not ok:
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    btsnoop_path = out_dir / "btmon.btsnoop"
    raw_journal_path = out_dir / "pi-journal.raw.txt"

    stop_requested = False

    def _handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    old_int = signal.signal(signal.SIGINT, _handle_signal)
    old_term = signal.signal(signal.SIGTERM, _handle_signal)

    journal_fh = raw_journal_path.open("w", encoding="utf-8")
    btmon_proc = subprocess.Popen(
        ["btmon", "-w", str(btsnoop_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    journal_proc = subprocess.Popen(
        [
            "stdbuf",
            "-oL",
            "journalctl",
            "-f",
            "-u",
            "wmr-bridge",
            "-u",
            "wmr-bridge-bt-recover",
            "-k",
        ],
        stdout=journal_fh,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + max(0.5, duration_s)
        while not stop_requested and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        for proc in (btmon_proc, journal_proc):
            if proc.poll() is None:
                proc.terminate()
        for proc in (btmon_proc, journal_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        journal_fh.close()

    fin_ok, fin_msgs = finalize_capture(
        out_dir,
        btsnoop_path=btsnoop_path,
        raw_journal_path=raw_journal_path,
        runner=runner,
    )
    for msg in fin_msgs:
        print(f"{'PASS' if fin_ok else 'FAIL'}: {msg}")
    return 0 if fin_ok else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench_optionc.py",
        description="Option C bench preflight and local-on-Pi btmon+journal capture helper",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight", help="run Option C preflight checks")
    pre.add_argument("--state-dir", type=Path, default=None)

    cap = sub.add_parser("capture", help="run preflight and capture btmon + journal locally")
    cap.add_argument("--out-dir", type=Path, required=True)
    cap.add_argument("--duration", type=float, default=30.0)
    cap.add_argument("--state-dir", type=Path, default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "preflight":
        ok, msgs = run_preflight(state_dir=args.state_dir)
        for msg in msgs:
            print(f"{'PASS' if ok else 'FAIL'}: {msg}")
        return 0 if ok else 1
    if args.command == "capture":
        return run_capture(
            args.out_dir,
            duration_s=args.duration,
            state_dir=args.state_dir,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
