#!/bin/sh
# Self-contained installer for the WMR Bridge Raspberry Pi / Linux host bridge.
#
# Usage on Raspberry Pi / Linux host:
#   sudo ./pi/install.sh
#
# One-line install (no checkout): fetch the release .deb, verify the release
# SHA256SUMS against the pinned minisign key, verify the .deb against that list,
# install it with apt-get and enable/start the service. minisign is installed
# with apt-get when the host does not have it; the .deb also depends on it for
# later upgrades.
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
# A pre-release is not returned by releases/latest, so pin its tag and version
# too. dpkg reads its "-rc.2" as a package revision and ranks it above the
# plain release, so the installer compares the tilde form instead and lets
# apt-get replace that revision when semver still ranks the target newer:
#   curl -fsSL \
#     https://github.com/nightcinder/WMR_Bridge/releases/download/v<version>/install.sh \
#     | WMR_BRIDGE_VERSION=<version> WMR_BRIDGE_TAG=v<version> sudo -E bash
#
# The installed copy is also the root upgrade path for the daemon:
#   /opt/wmr-bridge/pi/install.sh --upgrade
# --upgrade forces the signed release fetch even from an installed tree, and
# exits 0 without reinstalling when the installed version is already current.
#
# Test / packaging staging mode (no root required when WMR_SKIP_PRIVILEGED=1):
#   DESTDIR=/tmp/stage WMR_SKIP_PRIVILEGED=1 ./pi/install.sh
set -eu

UPGRADE=0
if [ "$#" -gt 1 ]; then
    echo "usage: $0 [--upgrade]" >&2
    exit 64
fi
case "${1:-}" in
    "") ;;
    --upgrade) UPGRADE=1 ;;
    *)
        echo "usage: $0 [--upgrade]" >&2
        exit 64
        ;;
esac

REPO="${WMR_BRIDGE_REPO:-nightcinder/WMR_Bridge}"

# Release signature trust anchor: bare minisign public key lines. A piped
# install accepts SHA256SUMS only when SHA256SUMS.minisig verifies against one
# of these, and every asset is then checked against that list. Tests may point
# WMR_BRIDGE_MINISIGN_PUBKEYS_OVERRIDE at a throwaway key, but only in the
# unprivileged staging mode: the root-owned upgrade path must never take a
# trust anchor from its environment.
WMR_BRIDGE_MINISIGN_PUBKEYS='RWTL80BhYeTEqPhBvFePoSc/uoC+iJCEa41o1uA3h+t4opQbX0yKCnA6'
MINISIGN_PUBKEYS="$WMR_BRIDGE_MINISIGN_PUBKEYS"
if [ "${WMR_SKIP_PRIVILEGED:-0}" = "1" ] && [ -n "${WMR_BRIDGE_MINISIGN_PUBKEYS_OVERRIDE:-}" ]; then
    MINISIGN_PUBKEYS="$WMR_BRIDGE_MINISIGN_PUBKEYS_OVERRIDE"
fi

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
    # The package path already restarted the bridge in postinst. Starting an
    # active unit is a no-op, so an upgrade restarts the daemon once instead of
    # twice; a checkout install is not run by dpkg and passes "restart".
    restart="${1:-restart}"
    if [ "${WMR_SKIP_PRIVILEGED:-0}" != "1" ] && command -v systemctl >/dev/null 2>&1; then
        systemctl enable bluetooth.service || true
        systemctl start bluetooth.service || true
        systemctl enable wmr-bridge.service || true
        if [ "$restart" = "restart" ]; then
            systemctl restart wmr-bridge.service || true
        else
            systemctl start wmr-bridge.service || true
        fi
        systemctl enable wmr-bridge-upgrade.path || true
        systemctl start wmr-bridge-upgrade.path || true
        systemctl enable wmr-bridge-bt-recover.path || true
        systemctl start wmr-bridge-bt-recover.path || true
    fi
}

# BlueZ defaults JustWorksRepairing to "never", which makes bluetoothd answer a
# phone's fresh Just Works pairing against a stored bond with a negative reply
# before our NoInputNoOutput agent is asked ("Numeric comparison failed"). Point
# the repair at the agent so a phone that forgot the bridge can pair again, as
# the ESP32 does after PIN_OR_KEY_MISSING. The same tool sets the [LE] interval
# bounds to 12/15 (15-20 ms) so BLE HID and FF1x notifications are not starved
# by BlueZ's long default.
configure_bluez() {
    if [ "${WMR_SKIP_PRIVILEGED:-0}" = "1" ] || [ -n "${DESTDIR:-}" ]; then
        return 0
    fi
    conf="${WMR_BLUETOOTH_MAIN_CONF:-/etc/bluetooth/main.conf}"
    script="${DEST_PI:-}/wmr_bridge/bluez_conf.py"
    [ -f "$script" ] || script="${SCRIPT_DIR}/wmr_bridge/bluez_conf.py"
    if ! command -v python3 >/dev/null 2>&1 || [ ! -f "$script" ]; then
        return 0
    fi
    if python3 "$script" --check "$conf"; then
        echo "install.sh: BlueZ re-pairing and LE intervals already configured; leaving bluetooth.service running"
        return 0
    fi
    if ! python3 "$script" "$conf"; then
        echo "install.sh: could not configure BlueZ re-pairing and LE intervals (non-fatal)" >&2
        return 0
    fi
    # bluetoothd reads main.conf once at startup, so a policy change needs a
    # restart. Stop the bridge first: a live GATT application, advertisement
    # and Classic scan racing that restart can wedge the Pi's combo controller
    # (btmon shows HCI Reset answered by Hardware Error 0x00) until the Pi is
    # rebooted.
    if command -v systemctl >/dev/null 2>&1; then
        systemctl stop wmr-bridge.service || true
        systemctl restart bluetooth.service || true
    fi
}

if [ -z "$SCRIPT_DIR" ] || [ "$UPGRADE" = "1" ]; then
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

    # dpkg reads the "-rc.2" of a semver pre-release as a package revision and
    # ranks it *above* the plain release (dpkg: 0.3.27-rc.2 > 0.3.27), the
    # reverse of semver. Compare the tilde form (0.3.27~rc.2 < 0.3.27) so the
    # install decisions agree with semver.
    debian_version() {
        printf '%s\n' "$1" | sed 's/-\([A-Za-z][0-9A-Za-z.+]*\)$/~\1/'
    }

    # Read the installed version once, for both the upgrade no-op check and the
    # apt-get downgrade decision below. Prefer the installed files (a checkout
    # install may be newer than the last .deb dpkg knows), then the package
    # database.
    INSTALLED_VERSION=""
    if [ -n "$SCRIPT_DIR" ] && [ -f "${SCRIPT_DIR}/wmr_bridge/__init__.py" ] &&
        command -v python3 >/dev/null 2>&1; then
        INSTALLED_VERSION="$(
            PYTHONPATH="$SCRIPT_DIR" python3 -c \
                'import wmr_bridge; print(wmr_bridge.__version__)' 2>/dev/null || true
        )"
    fi
    if [ -z "$INSTALLED_VERSION" ] && command -v dpkg-query >/dev/null 2>&1; then
        INSTALLED_VERSION="$(
            dpkg-query -W -f='${Version}' wmr-bridge 2>/dev/null || true
        )"
    fi

    # The daemon-triggered upgrade must not reinstall or downgrade.
    if [ "$UPGRADE" = "1" ]; then
        command -v apt-get >/dev/null 2>&1 ||
            die "the upgrade path needs apt-get"
        command -v dpkg >/dev/null 2>&1 ||
            die "the upgrade path needs dpkg"
        if [ -n "$INSTALLED_VERSION" ] &&
            dpkg --compare-versions \
                "$(debian_version "$VERSION")" le "$(debian_version "$INSTALLED_VERSION")"; then
            echo "install.sh: wmr-bridge ${INSTALLED_VERSION} is current (latest ${VERSION}); nothing to do"
            exit 0
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

    # The digest list is only as trustworthy as its signature: accept it only
    # when SHA256SUMS.minisig verifies against one of the pinned keys. Try each
    # key so a rotation can ship both the old and the new public key.
    verify_release_signature() {
        if ! command -v minisign >/dev/null 2>&1; then
            echo "install.sh: installing minisign to verify the release signature" >&2
            DEBIAN_FRONTEND=noninteractive apt-get install -y minisign >/dev/null ||
                die "could not install minisign; install it and retry: apt-get install minisign"
        fi
        [ -s "${TMP_DIR}/SHA256SUMS.minisig" ] ||
            die "the release has no SHA256SUMS.minisig"
        for key in $MINISIGN_PUBKEYS; do
            if minisign -V -q -m "${TMP_DIR}/SHA256SUMS" \
                -x "${TMP_DIR}/SHA256SUMS.minisig" -P "$key" 2>/dev/null; then
                echo "install.sh: release signature verified"
                return 0
            fi
        done
        die "SHA256SUMS does not verify against the pinned minisign key"
    }

    # Releases attach no tarball, so the no-checkout path installs the .deb.
    # Without apt-get, point at the two paths that still work instead of
    # failing on a missing asset.
    command -v apt-get >/dev/null 2>&1 ||
        die "the piped install needs apt-get; install from a checkout (git clone https://github.com/${REPO} && sudo WMR_Bridge/pi/install.sh) or extract a tarball built with pi/build_package.sh and run its pi/install.sh"

    fetch_url "${BASE_URL}/SHA256SUMS.minisig" "${TMP_DIR}/SHA256SUMS.minisig" ||
        die "could not fetch ${BASE_URL}/SHA256SUMS.minisig"
    verify_release_signature

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
    # apt-get refuses to replace a version dpkg ranks higher even when semver
    # says the fetched build is newer (0.3.27 over an installed 0.3.27-rc.2).
    # Accept exactly that case; a real semver downgrade still stops here.
    ALLOW_DOWNGRADES=""
    if [ -n "$INSTALLED_VERSION" ] && command -v dpkg >/dev/null 2>&1 &&
        dpkg --compare-versions \
            "$(debian_version "$VERSION")" gt "$(debian_version "$INSTALLED_VERSION")" &&
        dpkg --compare-versions "$VERSION" lt "$INSTALLED_VERSION"; then
        ALLOW_DOWNGRADES="--allow-downgrades"
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y $ALLOW_DOWNGRADES "${TMP_DIR}/${DEB}"
    # postinst already restarted wmr-bridge once with the new files.
    enable_service start
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

# Do not repeat the fresh-install Wi-Fi staging on a re-run over an existing
# bridge: an upgrade must leave the working radio and its links alone.
FRESH_INSTALL=1
if [ -f "${DEST_PI}/wmr_bridge/__init__.py" ]; then
    FRESH_INSTALL=0
fi

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
install -m 0644 "${SCRIPT_DIR}/minisign.pub" "${DEST_PI}/minisign.pub"
install -m 0644 "${SCRIPT_DIR}/wmr-bridge.in" "${DEST_PI}/wmr-bridge.in"
install -m 0644 "${SCRIPT_DIR}/pyproject.toml" "${DEST_PI}/pyproject.toml"
install -m 0644 "${SCRIPT_DIR}/README.md" "${DEST_PI}/README.md"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${DEST_PI}/systemd/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-upgrade.path" "${DEST_PI}/systemd/wmr-bridge-upgrade.path"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-upgrade.service" "${DEST_PI}/systemd/wmr-bridge-upgrade.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-bt-recover.path" "${DEST_PI}/systemd/wmr-bridge-bt-recover.path"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-bt-recover.service" "${DEST_PI}/systemd/wmr-bridge-bt-recover.service"
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
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-upgrade.path" "${DEST_SYSTEMD}/wmr-bridge-upgrade.path"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-upgrade.service" "${DEST_SYSTEMD}/wmr-bridge-upgrade.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-bt-recover.path" "${DEST_SYSTEMD}/wmr-bridge-bt-recover.path"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge-bt-recover.service" "${DEST_SYSTEMD}/wmr-bridge-bt-recover.service"
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
    # Stage the 5 GHz-preferred NetworkManager profile on a fresh install only.
    # --no-reconnect keeps an SSH session on the current link. A missing nmcli
    # (no NetworkManager) or a 2.4 GHz-only radio is non-fatal.
    if [ "$FRESH_INSTALL" = "1" ] && command -v nmcli >/dev/null 2>&1 &&
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
    configure_bluez
fi

enable_service
