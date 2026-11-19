#!/bin/bash
# Root-run Bluetooth adapter recovery ladder for wmr-bridge-bt-recover.service.
#
# The daemon (User=wmr, no CAP_NET_ADMIN) triggers the unit when
# Adapter1.Powered stays off and its own power-on fails: on a crashed combo
# firmware that write answers "Authentication Failed", and no amount of
# retrying from the daemon can reload the controller. Only root can.
#
# Ladder, each rung logged so the journal names the path taken. Every step
# that can hang on a wedged controller (btmgmt, the sysfs unbind/bind)
# runs under timeout(1) with a per-step bound well under the unit's
# TimeoutStartSec=120, so a hung step fails forward to the next rung
# instead of hanging the whole unit until systemd kills it at 120 s.
# Every failure is non-fatal and the script always exits 0: a still-dead
# adapter must not fail the unit (the daemon's 300 s cooldown is the rate
# limiter), so the escalation is logged instead. Reboot remains the
# documented last rung.
#   1. rfkill unblock bluetooth -- clear the boot-time soft block that the
#      unprivileged daemon cannot clear.
#   2. restart hciuart.service -- only when `systemctl cat` proves the image
#      ships the unit (the rc.2 image does not: hciuart is not-found there).
#   3. otherwise reset-rebind each enumerated controller: a btmgmt power
#      cycle, then unbind and rebind its driver (hci_uart on the Pi's UART
#      attach, btusb on a USB dongle) so the kernel re-runs the attach,
#      UART firmware download included.
#   4. restart bluetooth.service -- bluetoothd rediscovers the adapter.
#   5. start wmr-bridge.service -- it re-registers GATT and advertising.
set -u

DAEMON_SERVICE=wmr-bridge.service
# Per-step bound for the external calls that hang on a wedged controller:
# a crashed chip never answers the mgmt socket, so btmgmt blocks forever,
# and the sysfs unbind/bind runs the driver's detach/attach inline. Kept
# well under the unit's TimeoutStartSec=120 so even a fully hung rung 3
# still leaves the unit time to reach rungs 4/5.
STEP_TIMEOUT_S=10

log() {
    echo "wmr-bridge-bt-recover: $*"
}

# Stop the daemon before touching bluetoothd: a live GATT application,
# advertisement and Classic scan racing a bluetoothd restart can wedge the
# combo controller until the Pi is rebooted.
log "stopping ${DAEMON_SERVICE} before the ladder"
systemctl stop "$DAEMON_SERVICE" ||
    log "could not stop ${DAEMON_SERVICE} (continuing)"

log "rung 1/5: rfkill unblock bluetooth"
rfkill unblock bluetooth ||
    log "rung 1/5: rfkill unblock failed (continuing)"

if systemctl cat hciuart.service >/dev/null 2>&1; then
    log "rung 2/5: hciuart.service present; restarting the UART attach"
    systemctl restart hciuart.service ||
        log "rung 2/5: hciuart.service restart failed (continuing)"
else
    log "rung 3/5: no hciuart.service on this image; btmgmt/hci_uart reset-rebind"
    found=0
    for hci in /sys/class/bluetooth/hci[0-9]*; do
        [ -d "$hci" ] || continue
        found=1
        index="$(basename "$hci")"
        if command -v btmgmt >/dev/null 2>&1; then
            log "rung 3/5: btmgmt power cycle on ${index}"
            timeout "$STEP_TIMEOUT_S" btmgmt -i "$index" power off >/dev/null 2>&1 ||
                log "rung 3/5: btmgmt power off failed or timed out on ${index} (continuing)"
            timeout "$STEP_TIMEOUT_S" btmgmt -i "$index" power on >/dev/null 2>&1 ||
                log "rung 3/5: btmgmt power on failed or timed out on ${index} (continuing)"
        fi
        # Driver reset-rebind: walk to the closest ancestor bound to a
        # driver and unbind/rebind that device. Best-effort; a driver
        # without bind controls is logged and skipped.
        dev="$(readlink -f "$hci")"
        bound=""
        while [ "$dev" != "/" ]; do
            if [ -L "${dev}/driver" ]; then
                bound="$dev"
                break
            fi
            dev="$(dirname "$dev")"
        done
        if [ -z "$bound" ]; then
            log "rung 3/5: ${index} has no bound driver; skipping the rebind"
            continue
        fi
        driver_dir="$(readlink -f "${bound}/driver")"
        device_name="$(basename "$bound")"
        # timeout(1) cannot wrap a shell redirection, so each sysfs write
        # runs in a bounded sh -c: a wedged driver can block the write in
        # the kernel, and the bound keeps the ladder moving to rung 4/5.
        if timeout "$STEP_TIMEOUT_S" sh -c 'printf "%s\n" "$1" >"$2"' sh "$device_name" "${driver_dir}/unbind" 2>/dev/null; then
            # Let the detach settle before re-attaching the same device.
            sleep 1
            timeout "$STEP_TIMEOUT_S" sh -c 'printf "%s\n" "$1" >"$2"' sh "$device_name" "${driver_dir}/bind" 2>/dev/null ||
                log "rung 3/5: re-binding ${device_name} failed or timed out (continuing)"
            log "rung 3/5: re-bound ${device_name} via ${driver_dir##*/}"
        else
            log "rung 3/5: unbinding ${device_name} failed or timed out (continuing)"
        fi
    done
    if [ "$found" -eq 0 ]; then
        log "rung 3/5: no hci device is enumerated; nothing to reset-rebind"
    fi
fi

log "rung 4/5: restarting bluetooth.service"
if ! systemctl restart bluetooth.service; then
    log "escalation: bluetooth.service restart failed; adapter left down -- reboot the Pi (documented last rung)"
fi

log "rung 5/5: starting ${DAEMON_SERVICE}"
systemctl start "$DAEMON_SERVICE" ||
    log "rung 5/5: ${DAEMON_SERVICE} start failed (continuing)"

exit 0
