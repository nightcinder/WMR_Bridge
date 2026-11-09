#!/bin/sh
# Self-contained installer for the WMR Bridge Raspberry Pi / Linux host bridge.
#
# Usage on Raspberry Pi / Linux host:
#   sudo ./pi/install.sh
#
# One-line install (no checkout): fetch the release .deb, verify it against the
# release SHA256SUMS, install it with apt-get and enable/start the service.
# A host without apt-get installs from a checkout or from a locally built
# release tarball instead (see docs/PI_BRIDGE.md).
#   curl -fsSL https://github.com/nightcinder/WMR_Bridge/releases/latest/download/install.sh | sudo bash
#
# Pin a release or a mirror (also used by the host tests). The Pi package
# version follows the repository VERSION and the build scripts reject a
# mismatch, so a release cut from this tree carries a .deb with the tag's
# version:
#   WMR_BRIDGE_VERSION=<version> WMR_BRIDGE_TAG=v<version> sh install.sh
#   WMR_BRIDGE_VERSION=<version> WMR_BRIDGE_RELEASE_BASE_URL=https://example/pkg sh install.sh
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

# When piped (`curl ... | sudo bash`) $0 is the shell, not this file. Detect a
# checkout or extracted package tree here; without one, install the release
# .deb further down.
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

enable_service() {
    if [ "${WMR_SKIP_PRIVILEGED:-0}" != "1" ] && command -v systemctl >/dev/null 2>&1; then
        systemctl enable bluetooth.service || true
        systemctl start bluetooth.service || true
        systemctl enable wmr-bridge.service || true
        systemctl restart wmr-bridge.service || true
    fi
}

if [ -z "$SCRIPT_DIR" ]; then
    PINNED_VERSION="${WMR_BRIDGE_VERSION:-}"
    VERSION="$PINNED_VERSION"

    if [ -z "$VERSION" ]; then
        release_json="$(fetch_stdout "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null || true)"
        if [ -n "$release_json" ]; then
            VERSION="$(
                printf '%s\n' "$release_json" |
                    grep -o '"name"[[:space:]]*:[[:space:]]*"wmr-bridge_[^"]*_all\.deb"' |
                    head -n 1 |
                    sed -n 's/.*"wmr-bridge_\([^"]*\)_all\.deb".*/\1/p'
            )"
        fi
        [ -n "$VERSION" ] || die "no wmr-bridge .deb on the latest release; set WMR_BRIDGE_VERSION=<version>"
    fi

    BASE_URL="${WMR_BRIDGE_RELEASE_BASE_URL:-}"
    if [ -z "$BASE_URL" ]; then
        if [ -n "$PINNED_VERSION" ]; then
            BASE_URL="https://github.com/${REPO}/releases/download/${WMR_BRIDGE_TAG:-v${VERSION}}"
        else
            BASE_URL="https://github.com/${REPO}/releases/latest/download"
        fi
    fi

    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wmr-bridge-install.XXXXXX")"
    trap 'rm -rf "$TMP_DIR"' EXIT INT TERM HUP

    fetch_url "${BASE_URL}/SHA256SUMS" "${TMP_DIR}/SHA256SUMS" ||
        die "could not fetch ${BASE_URL}/SHA256SUMS"

    # Verify one asset against the shared digest list. The list covers every
    # release file, so select the single line for this asset first.
    verify_from_sums() {
        name="$1"
        awk -v n="$name" '$2 == n { print }' "${TMP_DIR}/SHA256SUMS" > "${TMP_DIR}/${name}.sum"
        [ -s "${TMP_DIR}/${name}.sum" ] || die "SHA256SUMS has no entry for ${name}"
        (cd "$TMP_DIR" && sha256sum -c "${name}.sum") >/dev/null ||
            die "sha256 verification failed for ${name}"
    }

    # Releases attach no tarball, so the no-checkout path installs the .deb.
    # Without apt-get, point at the two paths that still work instead of
    # failing on a missing asset.
    command -v apt-get >/dev/null 2>&1 ||
        die "the piped install needs apt-get; install from a checkout (git clone https://github.com/${REPO} && sudo WMR_Bridge/pi/install.sh) or extract a tarball built with pi/build_package.sh and run its pi/install.sh"

    DEB="wmr-bridge_${VERSION}_all.deb"
    fetch_url "${BASE_URL}/${DEB}" "${TMP_DIR}/${DEB}" ||
        die "could not fetch ${BASE_URL}/${DEB}"
    verify_from_sums "$DEB"
    echo "install.sh: installing ${DEB} from ${BASE_URL}"
    # apt-get drops to the unprivileged _apt user to read a local .deb. mktemp
    # creates a 0700 root-owned directory, so let _apt traverse the directory
    # and read the file; otherwise apt reports a Permission denied warning and
    # performs the download unsandboxed.
    chmod 0755 "$TMP_DIR"
    chmod 0644 "${TMP_DIR}/${DEB}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${TMP_DIR}/${DEB}"
    enable_service
    exit 0
fi

DESTDIR="${DESTDIR:-}"
PREFIX="${PREFIX:-/opt/wmr-bridge}"
BINDIR="${BINDIR:-/usr/bin}"
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
DEST_BINDIR="${DESTDIR}${BINDIR}"
DEST_ETC="${DESTDIR}${ETC_DIR}"
DEST_UDEV="${DESTDIR}${UDEV_DIR}"
DEST_SYSTEMD="${DESTDIR}${SYSTEMD_DIR}"
DEST_NETWORKMANAGER="${DESTDIR}${NETWORKMANAGER_DIR}"

if [ "$SKIP_PRIVILEGED" != "1" ]; then
    # bluez is a .deb dependency, but a checkout/tarball install can run on a
    # host without it. Join the bluetooth group only when it exists.
    SERVICE_GROUPS="input"
    if getent group bluetooth >/dev/null 2>&1; then
        SERVICE_GROUPS="input,bluetooth"
    fi
    if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --create-home --groups "$SERVICE_GROUPS" "$SERVICE_USER"
    else
        usermod -aG "$SERVICE_GROUPS" "$SERVICE_USER" 2>/dev/null || true
    fi
fi

mkdir -p \
    "${DEST_PI}/wmr_bridge" \
    "${DEST_PI}/tools" \
    "${DEST_PI}/systemd" \
    "${DEST_PI}/udev" \
    "${DEST_PI}/networkmanager" \
    "${DEST_BINDIR}" \
    "${DEST_ETC}" \
    "${DEST_UDEV}" \
    "${DEST_SYSTEMD}" \
    "${DEST_NETWORKMANAGER}"

# A hand-copied or interrupted install can leave bytecode from another
# version; never let it shadow the sources installed below.
rm -rf "${DEST_PI}/wmr_bridge/__pycache__"
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
install -m 0644 "${SCRIPT_DIR}/wmr-bridge.in" "${DEST_PI}/wmr-bridge.in"
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

# Put the daemon on PATH: wmr-bridge --version/--list work without cd.
sed "s|@PREFIX@|${PREFIX}|g" "${SCRIPT_DIR}/wmr-bridge.in" \
    > "${DEST_BINDIR}/wmr-bridge"
chmod 0755 "${DEST_BINDIR}/wmr-bridge"

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
chmod 0640 "$TOKEN_FILE"

if [ "$SKIP_PRIVILEGED" != "1" ]; then
    chown "${SERVICE_USER}:${SERVICE_GROUP}" "$TOKEN_FILE"
    # The token is group-readable so the invoking admin can run the host probe
    # without sudo. Group membership applies on the next login.
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "$SERVICE_USER" ] &&
        [ "$SUDO_USER" != "root" ]; then
        usermod -aG "$SERVICE_USER" "$SUDO_USER" 2>/dev/null || true
    fi
    # Stage the 5 GHz-preferred NetworkManager profile from the installed tree.
    # --no-reconnect keeps an SSH session on the current link; the unit's own
    # ExecStartPre applies the preference when the service starts. A missing
    # nmcli (no NetworkManager) or a 2.4 GHz-only radio is non-fatal.
    if command -v nmcli >/dev/null 2>&1 &&
        [ -f "${DEST_PI}/wmr_bridge/__main__.py" ]; then
        (cd "${DEST_PI}" && python3 -m wmr_bridge --prefer-5ghz --no-reconnect) ||
            echo "install.sh: could not stage the 5 GHz Wi-Fi preference (non-fatal)" >&2
    fi
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger || true
    fi
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || true
    fi
fi

enable_service
