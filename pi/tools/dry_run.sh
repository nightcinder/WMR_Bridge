#!/bin/sh
# Desk-only dry run of the Raspberry Pi host-bridge package.
#
# Proves, without a Pi or a controller, that the package installs into a clean
# rootfs, starts the daemon from the installed tree in replay mode, and serves
# batched FF14 records to the packaged probe with no gaps or server drops.
# This is the P2 pre-flight check in docs/runs/sheets/pi-bridge.md.
#
#   pi/tools/dry_run.sh                    # build the package, then dry-run it
#   pi/tools/dry_run.sh path/to/pkg.tar.gz # dry-run an existing package
#
# No root, hardware or third-party package needed; everything happens under a
# temporary directory and is removed on exit.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PI_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/wmr-bridge-dry-run.XXXXXX")"
DAEMON_PID=""

cleanup() {
    if [ -n "$DAEMON_PID" ]; then
        kill "$DAEMON_PID" 2>/dev/null || true
        wait "$DAEMON_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

die() {
    echo "FAIL: $*" >&2
    exit 1
}

PACKAGE="${1:-}"
if [ -z "$PACKAGE" ]; then
    "${PI_DIR}/build_package.sh" --out "$WORK/dist" >"$WORK/build.log" 2>&1 ||
        die "package build failed (see $WORK/build.log)"
    PACKAGE="$(find "$WORK/dist" -maxdepth 1 -name '*.tar.gz' | head -n 1)"
fi
[ -f "$PACKAGE" ] || die "package not found: $PACKAGE"
PACKAGE="$(CDPATH= cd -- "$(dirname -- "$PACKAGE")" && pwd)/$(basename "$PACKAGE")"

echo "package: $PACKAGE"
"${PI_DIR}/tools/verify_package.sh" "$PACKAGE" || die "package contents are incomplete"

mkdir -p "$WORK/unpack"
tar -xzf "$PACKAGE" -C "$WORK/unpack"
PKG_ROOT="$(find "$WORK/unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$PKG_ROOT" ] || die "package has no top-level directory"

DESTDIR="$WORK/rootfs"
DESTDIR="$DESTDIR" WMR_SKIP_PRIVILEGED=1 sh "$PKG_ROOT/pi/install.sh" \
    >"$WORK/install.log" 2>&1 || die "installer failed (see $WORK/install.log)"

STAGED="$DESTDIR/opt/wmr-bridge"
[ -f "$STAGED/pi/wmr_bridge/__main__.py" ] || die "staged daemon is missing"
[ -f "$STAGED/pi/tools/wifi_probe.py" ] || die "staged probe is missing"
[ -f "$STAGED/samples/controller_frames.txt" ] || die "staged replay capture is missing"
[ -f "$DESTDIR/etc/systemd/system/wmr-bridge.service" ] || die "systemd unit is missing"
[ -f "$DESTDIR/etc/udev/rules.d/99-wmr-bridge.rules" ] || die "udev rule is missing"
[ -f "$DESTDIR/etc/wmr-bridge/token" ] || die "token file is missing"
[ "$(stat -c '%a' "$DESTDIR/etc/wmr-bridge/token")" = "600" ] || die "token mode is not 0600"
echo "PASS install: staged tree, unit, udev rule and 0600 token"

PORT="$(
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
)"
cd "$STAGED/pi"
python3 -m wmr_bridge \
    --replay ../samples/controller_frames.txt \
    --bind 127.0.0.1 --port "$PORT" --duration 30 \
    >"$WORK/daemon.log" 2>&1 &
DAEMON_PID=$!

python3 - "$PORT" <<'PY' || die "daemon did not accept a connection within 5 s"
import socket
import sys
import time

port = int(sys.argv[1])
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close()
        raise SystemExit(0)
    except OSError:
        time.sleep(0.1)
raise SystemExit(1)
PY

python3 tools/wifi_probe.py \
    --host 127.0.0.1 --port "$PORT" --stream batched --duration 3 \
    >"$WORK/probe.out" 2>&1 || die "probe failed (see $WORK/probe.out)"

python3 - "$WORK/probe.out" <<'PY' || die "dry-run assertions failed (see the message above)"
import json
import sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
start = text.rfind("\n{\n")
if start < 0:
    print("no JSON summary in probe output")
    raise SystemExit(1)
data = json.loads(text[start + 1 :])

def need(condition, message):
    if not condition:
        print(message)
        raise SystemExit(1)

need(data["records"] > 0, "no records received")
need(data["gaps"] == [0, 0], f"sequence gaps: {data['gaps']}")
need(data["missingFromTicks"] == [0, 0], f"tick-based missing reports: {data['missingFromTicks']}")
need(data["dropped"] == 0, f"probe drops: {data['dropped']}")
hands = {h["hand"]: h for h in data["hands"]}
need(set(hands) == {"LEFT", "RIGHT"}, f"hands seen: {sorted(hands)}")
for hand, row in hands.items():
    need(row["records"] > 0, f"{hand} received no records")
    need(row["sequenceGaps"] == 0, f"{hand} sequence gaps: {row['sequenceGaps']}")

server = data["serverStatus"]
need(server["dropped"] == 0, f"server drops: {server['dropped']}")
need(server["clients"] >= 1 and server["subscribed"] >= 1, "client did not stay subscribed")
for row in server["hands"]:
    need(row["connected"], f"{row['hand']} disconnected during the run")
    need(row["errors"] == 0, f"{row['hand']} decode errors: {row['errors']}")

print(
    "PASS replay:",
    f"{data['records']} records",
    f"({hands['LEFT']['records']} L / {hands['RIGHT']['records']} R),",
    "gaps=0 missing=0 dropped=0 clients=" + str(server["clients"]),
)
PY

if grep -q "Traceback" "$WORK/daemon.log"; then
    die "daemon logged a traceback (see $WORK/daemon.log)"
fi
echo "PASS daemon: replay served batched FF14 from the installed tree with no traceback"
echo "PASS dry run: Pi package and daemon are ready for the hardware sitting"
