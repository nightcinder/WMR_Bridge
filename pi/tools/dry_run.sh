#!/bin/sh
# Desk-only dry run of the Raspberry Pi host-bridge packages.
#
# Proves, without a Pi or a controller, that the .deb installs into a clean
# staged rootfs, creates the token, survives a reinstall without losing the
# token or a locally edited wmr-bridge.env, serves batched FF14 records to the
# packaged probe, and removes cleanly. This is the P2 pre-flight check in
# docs/runs/sheets/pi-bridge.md.
#
#   pi/tools/dry_run.sh                    # build the .deb, then dry-run it
#   pi/tools/dry_run.sh path/to/pkg.deb    # dry-run an existing .deb
#   pi/tools/dry_run.sh path/to/pkg.tar.gz # legacy tarball dry run
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

# Assert the installed /usr/bin/wmr-bridge launcher runs the staged daemon.
assert_wrapper() {
    root="$1"
    staged="$2"
    wrapper="${root}/usr/bin/wmr-bridge"
    [ -x "$wrapper" ] || die "PATH wrapper is missing: $wrapper"
    version="$(
        sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${staged}/pi/wmr_bridge/__init__.py"
    )"
    [ -n "$version" ] || die "could not read the staged daemon version"
    if ! output="$(WMR_BRIDGE_PREFIX="$staged" "$wrapper" --version 2>&1)"; then
        die "PATH wrapper failed: $output"
    fi
    case "$output" in
        *"$version"*) ;;
        *) die "wmr-bridge --version printed '$output', expected $version" ;;
    esac
    echo "PASS wrapper: wmr-bridge --version = $version"
}

# Serve the replay capture from an installed tree and assert zero gaps/drops.
replay_from() {
    staged="$1"
    port="$(
        python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()'
    )"
    (cd "${staged}/pi" && exec python3 -m wmr_bridge \
        --replay ../samples/controller_frames.txt \
        --bind 127.0.0.1 --port "$port" --duration 30 \
        >"$WORK/daemon.log" 2>&1) &
    DAEMON_PID=$!

    python3 - "$port" <<'PY' || die "daemon did not accept a connection within 5 s"
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

    python3 "${staged}/pi/tools/wifi_probe.py" \
        --host 127.0.0.1 --port "$port" --stream batched --duration 3 \
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
    kill "$DAEMON_PID" 2>/dev/null || true
    wait "$DAEMON_PID" 2>/dev/null || true
    DAEMON_PID=""
}

staged_tarball() {
    package="$1"
    "${PI_DIR}/tools/verify_package.sh" "$package" || die "package contents are incomplete"
    mkdir -p "$WORK/unpack"
    tar -xzf "$package" -C "$WORK/unpack"
    pkg_root="$(find "$WORK/unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    [ -n "$pkg_root" ] || die "package has no top-level directory"

    DESTDIR="$WORK/rootfs" WMR_SKIP_PRIVILEGED=1 sh "$pkg_root/pi/install.sh" \
        >"$WORK/install.log" 2>&1 || die "installer failed (see $WORK/install.log)"

    staged="$WORK/rootfs/opt/wmr-bridge"
    [ -f "$staged/pi/wmr_bridge/__main__.py" ] || die "staged daemon is missing"
    [ -f "$staged/pi/tools/wifi_probe.py" ] || die "staged probe is missing"
    [ -f "$staged/samples/controller_frames.txt" ] || die "staged replay capture is missing"
    [ -f "$WORK/rootfs/etc/systemd/system/wmr-bridge.service" ] || die "systemd unit is missing"
    [ -f "$WORK/rootfs/etc/udev/rules.d/99-wmr-bridge.rules" ] || die "udev rule is missing"
    [ -f "$WORK/rootfs/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf" ] || die "power-save config is missing"
    [ -f "$WORK/rootfs/etc/wmr-bridge/token" ] || die "token file is missing"
    [ "$(stat -c '%a' "$WORK/rootfs/etc/wmr-bridge/token")" = "600" ] || die "token mode is not 0600"
    assert_wrapper "$WORK/rootfs" "$staged"
    echo "PASS install: staged tree, unit, udev rule, power-save config and 0600 token"
    replay_from "$staged"
    echo "PASS dry run: Pi tarball and daemon are ready for the hardware sitting"
}

# Copy the .deb payload. With preserve=1, existing conffiles win, matching what
# dpkg does for locally modified conffiles on reinstall/upgrade.
copy_deb_payload() {
    root="$1"
    preserve="$2"
    rm -rf "$WORK/extract"
    mkdir -p "$WORK/extract"
    dpkg-deb -x "$DEB" "$WORK/extract"
    (
        cd "$WORK/extract"
        find . -type f -print
    ) | while IFS= read -r rel; do
        rel="${rel#./}"
        if [ "$preserve" = "1" ] && [ -e "$root/$rel" ] &&
            grep -qx "/$rel" "$CONTROL/conffiles"; then
            continue
        fi
        mkdir -p "$root/$(dirname "$rel")"
        cp -p "$WORK/extract/$rel" "$root/$rel"
    done
}

run_maintainer() {
    script="$1"
    shift
    DESTDIR="$ROOT" WMR_SKIP_PRIVILEGED=1 sh "$CONTROL/$script" "$@"
}

assert_deb_install() {
    [ -f "$ROOT/opt/wmr-bridge/pi/wmr_bridge/__main__.py" ] || die "staged daemon is missing"
    [ -f "$ROOT/opt/wmr-bridge/pi/tools/wifi_probe.py" ] || die "staged probe is missing"
    [ -f "$ROOT/opt/wmr-bridge/samples/controller_frames.txt" ] || die "staged replay capture is missing"
    [ -f "$ROOT/etc/systemd/system/wmr-bridge.service" ] || die "systemd unit is missing"
    [ -f "$ROOT/etc/udev/rules.d/99-wmr-bridge.rules" ] || die "udev rule is missing"
    [ -f "$ROOT/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf" ] || die "power-save config is missing"
    grep -q 'wifi\.powersave = 2' "$ROOT/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf" ||
        die "power-save config does not disable powersave"
    [ -f "$ROOT/etc/wmr-bridge/wmr-bridge.env" ] || die "wmr-bridge.env is missing"
    [ -f "$ROOT/etc/wmr-bridge/token" ] || die "token file is missing"
    [ "$(stat -c '%a' "$ROOT/etc/wmr-bridge/token")" = "600" ] || die "token mode is not 0600"
    assert_wrapper "$ROOT" "$ROOT/opt/wmr-bridge"
}

staged_deb() {
    DEB="$1"
    CONTROL="$WORK/control"
    ROOT="$WORK/rootfs"
    mkdir -p "$ROOT"
    dpkg-deb -e "$DEB" "$CONTROL" || die "could not read the .deb control area"

    dpkg-deb -I "$DEB" >"$WORK/deb-info.txt" 2>&1 || die "dpkg-deb -I failed"
    dpkg-deb -c "$DEB" >"$WORK/deb-contents.txt" 2>&1 || die "dpkg-deb -c failed"
    grep -q '^ Architecture: all$' "$WORK/deb-info.txt" || die "package is not Architecture: all"
    grep -qx '/etc/wmr-bridge/wmr-bridge.env' "$CONTROL/conffiles" ||
        die "wmr-bridge.env is not a conffile"
    DEB_VERSION="$(sed -n 's/^ Version: //p' "$WORK/deb-info.txt")"
    echo "PASS deb: $(sed -n 's/^ Package: /package=/p; s/^ Version: /version=/p' "$WORK/deb-info.txt" | tr '\n' ' ')arch=all"

    copy_deb_payload "$ROOT" 0
    run_maintainer postinst configure >"$WORK/postinst.log" 2>&1 ||
        die "postinst failed (see $WORK/postinst.log)"
    assert_deb_install
    echo "PASS install: staged .deb tree, unit, udev rule, power-save config, env and 0600 token"

    TOKEN_BEFORE="$(sha256sum "$ROOT/etc/wmr-bridge/token" | cut -d' ' -f1)"
    printf '\n# local admin edit kept across upgrades\n' >>"$ROOT/etc/wmr-bridge/wmr-bridge.env"
    ENV_BEFORE="$(sha256sum "$ROOT/etc/wmr-bridge/wmr-bridge.env" | cut -d' ' -f1)"

    run_maintainer prerm upgrade "$DEB_VERSION" >>"$WORK/postinst.log" 2>&1 ||
        die "prerm upgrade failed (see $WORK/postinst.log)"
    copy_deb_payload "$ROOT" 1
    run_maintainer postinst configure "$DEB_VERSION" >>"$WORK/postinst.log" 2>&1 ||
        die "postinst failed on upgrade (see $WORK/postinst.log)"
    assert_deb_install
    TOKEN_AFTER="$(sha256sum "$ROOT/etc/wmr-bridge/token" | cut -d' ' -f1)"
    ENV_AFTER="$(sha256sum "$ROOT/etc/wmr-bridge/wmr-bridge.env" | cut -d' ' -f1)"
    [ "$TOKEN_BEFORE" = "$TOKEN_AFTER" ] || die "reinstall replaced the token"
    [ "$ENV_BEFORE" = "$ENV_AFTER" ] || die "reinstall replaced the edited wmr-bridge.env"
    TOKEN_SHORT="$(printf '%s' "$TOKEN_BEFORE" | cut -c1-16)"
    echo "PASS upgrade/reinstall: token sha256 ${TOKEN_SHORT}... unchanged; edited wmr-bridge.env preserved"

    replay_from "$ROOT/opt/wmr-bridge"

    run_maintainer prerm remove >"$WORK/prerm.log" 2>&1 ||
        die "prerm remove failed (see $WORK/prerm.log)"
    run_maintainer postrm remove >>"$WORK/prerm.log" 2>&1 ||
        die "postrm remove failed (see $WORK/prerm.log)"
    [ -f "$ROOT/etc/wmr-bridge/token" ] || die "remove deleted the token"
    echo "PASS remove: prerm/postrm exit 0 and keep the token"

    run_maintainer postrm purge >>"$WORK/prerm.log" 2>&1 ||
        die "postrm purge failed (see $WORK/prerm.log)"
    [ ! -e "$ROOT/etc/wmr-bridge/token" ] || die "purge kept the token"
    echo "PASS purge: token removed"
    echo "PASS dry run: Pi .deb installs, upgrades, replays and removes"
}

PACKAGE="${1:-}"
case "$PACKAGE" in
    *.tar.gz)
        [ -f "$PACKAGE" ] || die "package not found: $PACKAGE"
        PACKAGE="$(CDPATH= cd -- "$(dirname -- "$PACKAGE")" && pwd)/$(basename "$PACKAGE")"
        echo "package: $PACKAGE"
        staged_tarball "$PACKAGE"
        ;;
    *)
        if [ -z "$PACKAGE" ]; then
            "${PI_DIR}/build_deb.sh" --out "$WORK/dist" >"$WORK/build.log" 2>&1 ||
                die "package build failed (see $WORK/build.log)"
            PACKAGE="$(find "$WORK/dist" -maxdepth 1 -name '*.deb' | head -n 1)"
            [ -n "$PACKAGE" ] || die "build produced no .deb"
        fi
        [ -f "$PACKAGE" ] || die "package not found: $PACKAGE"
        PACKAGE="$(CDPATH= cd -- "$(dirname -- "$PACKAGE")" && pwd)/$(basename "$PACKAGE")"
        echo "package: $PACKAGE"
        staged_deb "$PACKAGE"
        ;;
esac
