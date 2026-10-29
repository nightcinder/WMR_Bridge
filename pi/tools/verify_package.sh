#!/bin/sh
# Check a wmr-bridge Pi package before it is copied to a host.
#
#   pi/tools/verify_package.sh dist/wmr-bridge-0.2.1.tar.gz
#
# Verifies the layout install.sh expects, the package version against the
# daemon, and the sibling .sha256 file when present. No extraction to disk.
set -eu

[ "$#" -eq 1 ] || { echo "usage: $0 <wmr-bridge-<version>.tar.gz>" >&2; exit 64; }
ARCHIVE="$1"
[ -f "$ARCHIVE" ] || { echo "FAIL: not found: $ARCHIVE" >&2; exit 1; }

BASE="$(basename "$ARCHIVE")"
VERSION="$(printf '%s' "$BASE" | sed -n 's/^wmr-bridge-\(.*\)\.tar\.gz$/\1/p')"
[ -n "$VERSION" ] || { echo "FAIL: expected wmr-bridge-<version>.tar.gz, got $BASE" >&2; exit 1; }
PREFIX="wmr-bridge-${VERSION}/"

if [ -f "${ARCHIVE}.sha256" ]; then
    expected="$(cut -d' ' -f1 "${ARCHIVE}.sha256")"
    actual="$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
    [ "$expected" = "$actual" ] || {
        echo "FAIL: sha256 mismatch (expected $expected, got $actual)" >&2
        exit 1
    }
fi

LIST="$(tar -tzf "$ARCHIVE")"
for path in \
    "${PREFIX}pi/install.sh" \
    "${PREFIX}pi/wmr_bridge/__main__.py" \
    "${PREFIX}pi/tools/wifi_probe.py" \
    "${PREFIX}pi/systemd/wmr-bridge.service" \
    "${PREFIX}pi/systemd/wmr-bridge.env.example" \
    "${PREFIX}pi/udev/99-wmr-bridge.rules" \
    "${PREFIX}docs/PI_BRIDGE.md" \
    "${PREFIX}samples/controller_frames.txt"; do
    printf '%s\n' "$LIST" | grep -qx "$path" || {
        echo "FAIL: package is missing $path" >&2
        exit 1
    }
done
if printf '%s\n' "$LIST" | grep -Eq '(__pycache__|\.pyc$)'; then
    echo "FAIL: package contains bytecode" >&2
    exit 1
fi

EXTRACTED_VERSION="$(
    tar -xzOf "$ARCHIVE" "${PREFIX}pi/wmr_bridge/__init__.py" |
        sed -n 's/^__version__ = "\(.*\)"$/\1/p'
)"
[ "$EXTRACTED_VERSION" = "$VERSION" ] || {
    echo "FAIL: package name is $VERSION but wmr_bridge is $EXTRACTED_VERSION" >&2
    exit 1
}

echo "OK: $BASE ($VERSION, $(wc -c < "$ARCHIVE") bytes)"
