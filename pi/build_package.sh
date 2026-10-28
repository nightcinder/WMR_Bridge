#!/bin/sh
# Build the installable Raspberry Pi / Linux host-bridge package.
#
# The package is a source tree, not a wheel: it carries pi/ (daemon, probe,
# installer, systemd unit, udev rule), docs/PI_BRIDGE.md and a replay capture,
# so it installs without a git checkout and can run the P2 replay check on the
# Pi before any controller is paired.
#
#   pi/build_package.sh                 # writes dist/wmr-bridge-<version>.tar.gz
#   pi/build_package.sh --out DIR       # writes into DIR instead
#
# Output is gitignored (dist/). No root, hardware or third-party package needed.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/dist"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)
            [ "$#" -ge 2 ] || { echo "usage: $0 [--out DIR]" >&2; exit 64; }
            OUT_DIR="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 64
            ;;
    esac
done

VERSION="$(
    cd "${SCRIPT_DIR}"
    python3 -c 'import wmr_bridge; print(wmr_bridge.__version__)'
)"
NAME="wmr-bridge-${VERSION}"
ARCHIVE="${OUT_DIR}/${NAME}.tar.gz"

mkdir -p "$OUT_DIR"
rm -f "$ARCHIVE" "${ARCHIVE}.sha256"

# Deterministic order and no bytecode from a previous test run. Paths keep the
# pi/ and docs/ layout that install.sh expects.
tar \
    --sort=name \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --transform "s,^,${NAME}/," \
    -C "$REPO_ROOT" \
    -czf "$ARCHIVE" \
    pi docs/PI_BRIDGE.md samples/controller_frames.txt

(
    cd "$OUT_DIR"
    sha256sum "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256"
)

echo "package: ${ARCHIVE}"
echo "sha256:  $(cut -d' ' -f1 "${ARCHIVE}.sha256")"
echo "size:    $(wc -c < "$ARCHIVE") bytes"
