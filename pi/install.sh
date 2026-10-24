#!/bin/sh
# Self-contained installer for the WMR Bridge Raspberry Pi / Linux host bridge.
#
# Usage on Raspberry Pi / Linux host:
#   sudo ./pi/install.sh
#
# Test / packaging staging mode (no root required when WMR_SKIP_PRIVILEGED=1):
#   DESTDIR=/tmp/stage WMR_SKIP_PRIVILEGED=1 ./pi/install.sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
DESTDIR="${DESTDIR:-}"
PREFIX="${PREFIX:-/opt/wmr-bridge}"
ETC_DIR="${ETC_DIR:-/etc/wmr-bridge}"
UDEV_DIR="${UDEV_DIR:-/etc/udev/rules.d}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
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
    "${DEST_ETC}" \
    "${DEST_UDEV}" \
    "${DEST_SYSTEMD}"

for pyfile in "${SCRIPT_DIR}"/wmr_bridge/*.py; do
    install -m 0644 "$pyfile" "${DEST_PI}/wmr_bridge/$(basename "$pyfile")"
done

install -m 0755 "${SCRIPT_DIR}/tools/wifi_probe.py" "${DEST_PI}/tools/wifi_probe.py"
install -m 0755 "${SCRIPT_DIR}/run_tests.sh" "${DEST_PI}/run_tests.sh"
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

install -m 0644 "${SCRIPT_DIR}/udev/99-wmr-bridge.rules" "${DEST_UDEV}/99-wmr-bridge.rules"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.service" "${DEST_SYSTEMD}/wmr-bridge.service"
install -m 0644 "${SCRIPT_DIR}/systemd/wmr-bridge.env.example" "${DEST_ETC}/wmr-bridge.env.example"

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
