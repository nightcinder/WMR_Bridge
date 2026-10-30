#!/bin/sh
# Build the Debian package for the Raspberry Pi / Linux host bridge.
#
#   pi/build_deb.sh                  # dist/wmr-bridge_<version>_all.deb
#   pi/build_deb.sh --out DIR        # write into DIR instead
#
# The package is architecture-independent: the daemon is Python. It places the
# app in /opt/wmr-bridge, the systemd unit and udev rule in /etc, the env files
# under /etc/wmr-bridge as conffiles, and creates /etc/wmr-bridge/token in
# postinst when it is absent. No root, fakeroot or third-party tool is needed;
# dpkg-deb --root-owner-group records root ownership.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
OUT_DIR="${REPO_ROOT}/dist"
DEB_VERSION="${WMR_DEB_VERSION:-}"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --out)
            [ "$#" -ge 2 ] || { echo "usage: $0 [--out DIR]" >&2; exit 64; }
            OUT_DIR="$2"
            shift 2
            ;;
        --version)
            [ "$#" -ge 2 ] || { echo "usage: $0 [--version VERSION]" >&2; exit 64; }
            DEB_VERSION="$2"
            shift 2
            ;;
        -h|--help)
            sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            exit 64
            ;;
    esac
done

if [ -z "$DEB_VERSION" ]; then
    DEB_VERSION="$(
        cd "${SCRIPT_DIR}"
        python3 -c 'import wmr_bridge; print(wmr_bridge.__version__)'
    )"
fi
case "$DEB_VERSION" in
    *[!0-9A-Za-z.+~-]*|"")
        echo "invalid Debian version: $DEB_VERSION" >&2
        exit 64
        ;;
esac

command -v dpkg-deb >/dev/null 2>&1 || {
    echo "dpkg-deb is required (install dpkg)" >&2
    exit 69
}
if ! dpkg-deb --help 2>&1 | grep -q -- '--root-owner-group'; then
    echo "dpkg-deb is too old: --root-owner-group is required (dpkg 1.19+)" >&2
    exit 69
fi

DEB_NAME="wmr-bridge_${DEB_VERSION}_all.deb"
DEB="${OUT_DIR}/${DEB_NAME}"
BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wmr-bridge-deb.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT INT TERM HUP

ROOT="${BUILD_DIR}/root"
OPT="${ROOT}/opt/wmr-bridge"
ETC="${ROOT}/etc/wmr-bridge"

install -d -m 0755 \
    "${OPT}/pi/wmr_bridge" \
    "${OPT}/pi/tools" \
    "${OPT}/pi/systemd" \
    "${OPT}/pi/udev" \
    "${OPT}/pi/networkmanager" \
    "${OPT}/docs" \
    "${OPT}/samples" \
    "${ETC}" \
    "${ROOT}/etc/systemd/system" \
    "${ROOT}/etc/udev/rules.d" \
    "${ROOT}/etc/NetworkManager/conf.d" \
    "${ROOT}/DEBIAN"

for pyfile in "${SCRIPT_DIR}"/wmr_bridge/*.py; do
    install -m 0644 "$pyfile" "${OPT}/pi/wmr_bridge/$(basename "$pyfile")"
done
install -m 0755 "${SCRIPT_DIR}/tools/wifi_probe.py" "${OPT}/pi/tools/wifi_probe.py"
# The shipped installer reads the drop-in from its own tree, so the package must
# carry it: without it install.sh aborts under set -eu. run_tests.sh is not
# shipped because the suite needs the repo build scripts, not just this tree.
install -m 0644 "${SCRIPT_DIR}/networkmanager/99-wmr-bridge-powersave.conf" \
    "${OPT}/pi/networkmanager/99-wmr-bridge-powersave.conf"
install -m 0755 "${SCRIPT_DIR}/install.sh" "${OPT}/pi/install.sh"
install -m 0644 "${SCRIPT_DIR}/pyproject.toml" "${OPT}/pi/pyproject.toml"
install -m 0644 "${SCRIPT_DIR}/README.md" "${OPT}/pi/README.md"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${OPT}/pi/systemd/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${OPT}/pi/systemd/wmr-bridge.env.example"
install -m 0644 "${SCRIPT_DIR}/udev/99-wmr-bridge.rules" "${OPT}/pi/udev/99-wmr-bridge.rules"
install -m 0644 "${REPO_ROOT}/docs/PI_BRIDGE.md" "${OPT}/docs/PI_BRIDGE.md"
install -m 0644 "${REPO_ROOT}/samples/controller_frames.txt" "${OPT}/samples/controller_frames.txt"

install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${ROOT}/etc/systemd/system/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/udev/99-wmr-bridge.rules" "${ROOT}/etc/udev/rules.d/99-wmr-bridge.rules"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${ETC}/wmr-bridge.env.example"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${ETC}/wmr-bridge.env"
install -m 0644 "${SCRIPT_DIR}/networkmanager/99-wmr-bridge-powersave.conf" \
    "${ROOT}/etc/NetworkManager/conf.d/99-wmr-bridge-powersave.conf"

INSTALLED_SIZE="$(du -sk "$ROOT" | cut -f1)"
sed "s/@VERSION@/${DEB_VERSION}/; s/@INSTALLED_SIZE@/${INSTALLED_SIZE}/" \
    "${SCRIPT_DIR}/debian/control.in" > "${ROOT}/DEBIAN/control"
install -m 0644 "${SCRIPT_DIR}/debian/conffiles" "${ROOT}/DEBIAN/conffiles"
for script in postinst prerm postrm; do
    install -m 0755 "${SCRIPT_DIR}/debian/${script}" "${ROOT}/DEBIAN/${script}"
done

mkdir -p "$OUT_DIR"
rm -f "$DEB"
dpkg-deb --root-owner-group --build "$ROOT" "$DEB" >/dev/null

echo "package: ${DEB}"
echo "version: ${DEB_VERSION}"
echo "sha256:  $(sha256sum "$DEB" | cut -d' ' -f1)"
echo "size:    $(wc -c < "$DEB") bytes"
