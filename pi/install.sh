#!/bin/sh
# Self-contained installer for the WMR Bridge Raspberry Pi / Linux host bridge.
#
# Usage on Raspberry Pi / Linux host:
#   sudo ./pi/install.sh
#
# One-line install (no checkout): fetch the release tarball, verify its sha256
# and run the installer from the extracted tree.
#   curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
#
# Pin a release or a mirror (also used by the host tests):
#   WMR_BRIDGE_VERSION=0.2.1 WMR_BRIDGE_RELEASE_BASE_URL=https://example/pkg sh install.sh
#
# Test / packaging staging mode (no root required when WMR_SKIP_PRIVILEGED=1):
#   DESTDIR=/tmp/stage WMR_SKIP_PRIVILEGED=1 ./pi/install.sh
set -eu

REPO="${WMR_BRIDGE_REPO:-nightcinder/WMR_Bridge}"

die() {
    echo "install.sh: $*" >&2
    exit 1
}

fetch_url() {
    url="$1"
    dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$url" -o "$dest"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$dest" "$url"
    else
        die "curl or wget is required to fetch the release package"
    fi
}

fetch_stdout() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- "$1"
    else
        die "curl or wget is required to fetch the release package"
    fi
}

# When piped (`curl ... | sudo bash`) $0 is the shell, not this file. Fetch the
# matching release tarball and run the installer from the extracted tree.
SELF="$0"
SCRIPT_DIR=""
case "$SELF" in
    */*|install.sh)
        if [ -r "$SELF" ] && [ -f "$SELF" ]; then
            candidate="$(CDPATH= cd -- "$(dirname -- "$SELF")" && pwd)"
            if [ -d "${candidate}/wmr_bridge" ]; then
                SCRIPT_DIR="$candidate"
            fi
        fi
        ;;
esac

if [ -z "$SCRIPT_DIR" ]; then
    PINNED_VERSION="${WMR_BRIDGE_VERSION:-}"
    VERSION="$PINNED_VERSION"

    if [ -z "$VERSION" ]; then
        release_json="$(fetch_stdout "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null || true)"
        if [ -n "$release_json" ]; then
            VERSION="$(
                printf '%s\n' "$release_json" |
                    grep -o '"name"[[:space:]]*:[[:space:]]*"wmr-bridge-[^"]*\.tar\.gz"' |
                    head -n 1 |
                    sed -n 's/.*"wmr-bridge-\([^"]*\)\.tar\.gz".*/\1/p'
            )"
        fi
        [ -n "$VERSION" ] || die "no wmr-bridge tarball on the latest release; set WMR_BRIDGE_VERSION=<version>, or install the .deb"
    fi

    BASE_URL="${WMR_BRIDGE_RELEASE_BASE_URL:-}"
    if [ -z "$BASE_URL" ]; then
        if [ -n "$PINNED_VERSION" ]; then
            BASE_URL="https://github.com/${REPO}/releases/download/${WMR_BRIDGE_TAG:-v${VERSION}}"
        else
            BASE_URL="https://github.com/${REPO}/releases/latest/download"
        fi
    fi

    TARBALL="wmr-bridge-${VERSION}.tar.gz"
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wmr-bridge-install.XXXXXX")"
    trap 'rm -rf "$TMP_DIR"' EXIT INT TERM HUP

    fetch_url "${BASE_URL}/${TARBALL}" "${TMP_DIR}/${TARBALL}" ||
        die "could not fetch ${BASE_URL}/${TARBALL}"
    fetch_url "${BASE_URL}/${TARBALL}.sha256" "${TMP_DIR}/${TARBALL}.sha256" ||
        die "could not fetch ${BASE_URL}/${TARBALL}.sha256"
    (cd "$TMP_DIR" && sha256sum -c "${TARBALL}.sha256") ||
        die "sha256 verification failed for ${TARBALL}"

    tar -xzf "${TMP_DIR}/${TARBALL}" -C "$TMP_DIR"
    EXTRACTED="${TMP_DIR}/wmr-bridge-${VERSION}"
    INNER="${EXTRACTED}/pi/install.sh"
    [ -f "$INNER" ] || die "release tarball has no pi/install.sh"
    EXTRACTED_VERSION="$(
        sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${EXTRACTED}/pi/wmr_bridge/__init__.py"
    )"
    [ "$EXTRACTED_VERSION" = "$VERSION" ] ||
        die "tarball is $EXTRACTED_VERSION but requested $VERSION"
    echo "install.sh: using release ${VERSION} from ${BASE_URL}"
    status=0
    WMR_BRIDGE_PIPED=1 sh "$INNER" "$@" || status=$?
    exit "$status"
fi

DESTDIR="${DESTDIR:-}"
PREFIX="${PREFIX:-/opt/wmr-bridge}"
ETC_DIR="${ETC_DIR:-/etc/wmr-bridge}"
UDEV_DIR="${UDEV_DIR:-/etc/udev/rules.d}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
NETWORKMANAGER_DIR="${NETWORKMANAGER_DIR:-/etc/NetworkManager/conf.d}"
SERVICE_USER="${SERVICE_USER:-wmr}"
SERVICE_GROUP="${SERVICE_GROUP:-wmr}"
SKIP_PRIVILEGED="${WMR_SKIP_PRIVILEGED:-0}"

if [ -n "$DESTDIR" ]; then
    DESTDIR="${DESTDIR%/}"
fi

DEST_PREFIX="${DESTDIR}${PREFIX}"
DEST_PI="${DEST_PREFIX}/pi"
DEST_ETC="${DESTDIR}${ETC_DIR}"
DEST_UDEV="${DESTDIR}${UDEV_DIR}"
DEST_SYSTEMD="${DESTDIR}${SYSTEMD_DIR}"
DEST_NETWORKMANAGER="${DESTDIR}${NETWORKMANAGER_DIR}"

if [ "$SKIP_PRIVILEGED" != "1" ]; then
    if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --create-home --groups input "$SERVICE_USER"
    else
        usermod -aG input "$SERVICE_USER" 2>/dev/null || true
    fi
fi

mkdir -p \
    "${DEST_PI}/wmr_bridge" \
    "${DEST_PI}/tools" \
    "${DEST_PI}/systemd" \
    "${DEST_PI}/udev" \
    "${DEST_PI}/networkmanager" \
    "${DEST_ETC}" \
    "${DEST_UDEV}" \
    "${DEST_SYSTEMD}" \
    "${DEST_NETWORKMANAGER}"

for pyfile in "${SCRIPT_DIR}"/wmr_bridge/*.py; do
    install -m 0644 "$pyfile" "${DEST_PI}/wmr_bridge/$(basename "$pyfile")"
done

install -m 0755 "${SCRIPT_DIR}/tools/wifi_probe.py" "${DEST_PI}/tools/wifi_probe.py"
# Keep the installed pi/ tree self-sufficient: the shipped install.sh reads the
# NetworkManager drop-in from its own directory. run_tests.sh stays out of the
# installed tree because the suite needs the repo build scripts.
install -m 0644 "${SCRIPT_DIR}/networkmanager/99-wmr-bridge-powersave.conf" \
    "${DEST_PI}/networkmanager/99-wmr-bridge-powersave.conf"
install -m 0755 "${SCRIPT_DIR}/install.sh" "${DEST_PI}/install.sh"
install -m 0644 "${SCRIPT_DIR}/pyproject.toml" "${DEST_PI}/pyproject.toml"
install -m 0644 "${SCRIPT_DIR}/README.md" "${DEST_PI}/README.md"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${DEST_PI}/systemd/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${DEST_PI}/systemd/wmr-bridge.env.example"
install -m 0644 "${SCRIPT_DIR}/udev/99-wmr-bridge.rules" "${DEST_PI}/udev/99-wmr-bridge.rules"

if [ -f "${SCRIPT_DIR}/../docs/PI_BRIDGE.md" ]; then
    mkdir -p "${DEST_PREFIX}/docs"
    install -m 0644 "${SCRIPT_DIR}/../docs/PI_BRIDGE.md" "${DEST_PREFIX}/docs/PI_BRIDGE.md"
fi

# A replay capture makes the P2 no-hardware check runnable on the installed
# tree (`cd /opt/wmr-bridge/pi && python3 -m wmr_bridge --replay ../samples/...`).
if [ -f "${SCRIPT_DIR}/../samples/controller_frames.txt" ]; then
    mkdir -p "${DEST_PREFIX}/samples"
    install -m 0644 "${SCRIPT_DIR}/../samples/controller_frames.txt" \
        "${DEST_PREFIX}/samples/controller_frames.txt"
fi

install -m 0644 "${SCRIPT_DIR}/udev/99-wmr-bridge.rules" "${DEST_UDEV}/99-wmr-bridge.rules"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${DEST_SYSTEMD}/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${DEST_ETC}/wmr-bridge.env.example"
install -m 0644 "${SCRIPT_DIR}/networkmanager/99-wmr-bridge-powersave.conf" \
    "${DEST_NETWORKMANAGER}/99-wmr-bridge-powersave.conf"

if [ ! -f "${DEST_ETC}/wmr-bridge.env" ]; then
    install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${DEST_ETC}/wmr-bridge.env"
fi

TOKEN_FILE="${DEST_ETC}/token"
if [ ! -s "$TOKEN_FILE" ]; then
    umask_old="$(umask)"
    umask 0077
    head -c 24 /dev/urandom | base64 > "$TOKEN_FILE"
    umask "$umask_old"
fi
chmod 0600 "$TOKEN_FILE"

if [ "$SKIP_PRIVILEGED" != "1" ]; then
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "$TOKEN_FILE"
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger || true
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
    fi
fi
