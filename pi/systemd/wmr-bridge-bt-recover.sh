#!/bin/bash
# Root-run Bluetooth adapter recovery ladder for wmr-bridge-bt-recover.service.
#
# The daemon (User=wmr, no CAP_NET_ADMIN) triggers the unit when
# Adapter1.Powered stays off and its own power-on fails: on a crashed combo
# firmware that write answers "Authentication Failed", and no amount of
# retrying from the daemon can reload the controller. Only root can.
#
# Ladder, each rung logged so the journal names the path taken. Every step
# that can hang on a wedged controller (btmgmt, systemctl and the sysfs
# unbind/bind) runs under timeout(1), and the whole script carries its own
# deadline, so a hung step fails forward to the next rung instead of hanging
# the unit until systemd kills it at TimeoutStartSec=120. A systemd timeout
# kill has no exit code for the caller; this script does.
#
# A rung failing is non-fatal and the ladder continues, but the run is a
# success only if the postcondition holds after the last rung: at least one
# controller is enumerated and reports "Powered: yes". Otherwise the script
# exits non-zero with a code naming the failure kind (1 no controller, 2
# enumerated but unpowered, 3 the postcondition could not be sampled inside
# the deadline, 4 a sample call failed for another reason, e.g. bluetoothctl
# missing or bluetoothd unreachable) and logs one line with the observed
# state. systemd, the daemon and a reader of the journal then see a failed
# unit instead of a false success. Reboot remains the human last resort, not
# a rung of a script that cannot fail.
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
# the sysfs unbind/bind runs the driver's detach/attach inline, and
# systemctl can wait forever on a wedged daemon. Kept well under the unit's
# TimeoutStartSec=120 so even a fully hung rung still leaves the unit time
# to reach rungs 4/5. The tests lower it; production keeps these values.
STEP_TIMEOUT_S="${BT_RECOVER_STEP_TIMEOUT_S:-10}"
# Script-level deadline, enforced by the script itself. systemd's
# TimeoutStartSec kill yields no exit code, so the ladder must finish, and
# sample the postcondition, before that. A sampling reserve keeps the final
# `bluetoothctl` calls inside the deadline even when every rung hangs.
DEADLINE_S="${BT_RECOVER_DEADLINE_S:-90}"
case "$STEP_TIMEOUT_S" in ''|*[!0-9]*) STEP_TIMEOUT_S=10 ;; esac
case "$DEADLINE_S" in ''|*[!0-9]*) DEADLINE_S=90 ;; esac
[ "$DEADLINE_S" -ge "$STEP_TIMEOUT_S" ] || DEADLINE_S="$STEP_TIMEOUT_S"
SAMPLE_RESERVE_S=$((DEADLINE_S / 4))
[ "$SAMPLE_RESERVE_S" -gt 10 ] && SAMPLE_RESERVE_S=10
DEADLINE_EPOCH=$(( $(date +%s) + DEADLINE_S ))

EXIT_NO_CONTROLLER=1
EXIT_UNPOWERED=2
EXIT_DEADLINE=3
EXIT_SAMPLE_FAILED=4

log() {
    echo "wmr-bridge-bt-recover: $*"
}

# Run one external step under min(STEP_TIMEOUT_S, deadline - reserve). The
# reserve leaves time to sample the postcondition; once it is gone the step
# is skipped and reports timeout(1)'s 124, so the ladder still reaches the
# postcondition check instead of being killed by systemd.
run_bounded() {
    cap="$1"
    shift
    left=$((DEADLINE_EPOCH - $(date +%s) - SAMPLE_RESERVE_S))
    [ "$left" -gt "$cap" ] && left="$cap"
    if [ "$left" -le 0 ]; then
        log "deadline ${DEADLINE_S}s: skipping $*"
        return 124
    fi
    timeout "$left" "$@"
}

# Sample the postcondition: count the controllers BlueZ enumerates, then ask
# each for Powered. Sets PC_COUNT, PC_MACS, PC_POWERED and PC_POWERED_MAC.
# Returns 0 when every sample call completed (PC_COUNT then holds the
# observed count), 124 when a call could not finish inside the deadline, and
# 1 when a call failed for another reason (bluetoothctl missing, bluetoothd
# unreachable, a controller vanishing between `list` and `show`); PC_ERROR
# then names the cause, so the caller does not report every failure as a
# deadline overrun.
sample_postcondition() {
    PC_COUNT=0
    PC_MACS=""
    PC_POWERED=no
    PC_POWERED_MAC=""
    PC_ERROR=""
    bound=$((DEADLINE_EPOCH - $(date +%s)))
    [ "$bound" -gt "$STEP_TIMEOUT_S" ] && bound="$STEP_TIMEOUT_S"
    if [ "$bound" -le 0 ]; then
        PC_COUNT="unknown"
        return 124
    fi
    list="$(timeout "$bound" bluetoothctl list 2>/dev/null)"
    status=$?
    if [ "$status" -ne 0 ]; then
        if [ "$status" -eq 124 ]; then
            PC_COUNT="unknown"
            return 124
        fi
        PC_ERROR="bluetoothctl list exited ${status}"
        return 1
    fi
    while IFS= read -r line; do
        case "$line" in
            Controller\ *)
                mac="${line#Controller }"
                mac="${mac%% *}"
                PC_COUNT=$((PC_COUNT + 1))
                PC_MACS="${PC_MACS:+${PC_MACS} }${mac}"
                ;;
        esac
    done <<EOF
${list}
EOF
    for mac in $PC_MACS; do
        bound=$((DEADLINE_EPOCH - $(date +%s)))
        [ "$bound" -gt "$STEP_TIMEOUT_S" ] && bound="$STEP_TIMEOUT_S"
        if [ "$bound" -le 0 ]; then
            PC_COUNT="unknown"
            return 124
        fi
        info="$(timeout "$bound" bluetoothctl show "$mac" 2>/dev/null)"
        status=$?
        if [ "$status" -ne 0 ]; then
            if [ "$status" -eq 124 ]; then
                PC_COUNT="unknown"
                return 124
            fi
            PC_ERROR="bluetoothctl show ${mac} exited ${status}"
            return 1
        fi
        case "$info" in
            *"Powered: yes"*)
                PC_POWERED=yes
                PC_POWERED_MAC="$mac"
                break
                ;;
        esac
    done
    return 0
}

# Stop the daemon before touching bluetoothd: a live GATT application,
# advertisement and Classic scan racing a bluetoothd restart can wedge the
# combo controller until the Pi is rebooted.
log "stopping ${DAEMON_SERVICE} before the ladder"
run_bounded "$STEP_TIMEOUT_S" systemctl stop "$DAEMON_SERVICE" ||
    log "could not stop ${DAEMON_SERVICE} (continuing)"

log "rung 1/5: rfkill unblock bluetooth"
run_bounded "$STEP_TIMEOUT_S" rfkill unblock bluetooth ||
    log "rung 1/5: rfkill unblock failed or timed out (continuing)"

if run_bounded "$STEP_TIMEOUT_S" systemctl cat hciuart.service >/dev/null 2>&1; then
    log "rung 2/5: hciuart.service present; restarting the UART attach"
    run_bounded "$STEP_TIMEOUT_S" systemctl restart hciuart.service ||
        log "rung 2/5: hciuart.service restart failed or timed out (continuing)"
else
    log "rung 3/5: no hciuart.service on this image; btmgmt/hci_uart reset-rebind"
    found=0
    for hci in /sys/class/bluetooth/hci[0-9]*; do
        [ -d "$hci" ] || continue
        found=1
        index="$(basename "$hci")"
        if command -v btmgmt >/dev/null 2>&1; then
            log "rung 3/5: btmgmt power cycle on ${index}"
            run_bounded "$STEP_TIMEOUT_S" btmgmt -i "$index" power off >/dev/null 2>&1 ||
                log "rung 3/5: btmgmt power off failed or timed out on ${index} (continuing)"
            run_bounded "$STEP_TIMEOUT_S" btmgmt -i "$index" power on >/dev/null 2>&1 ||
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
        if run_bounded "$STEP_TIMEOUT_S" sh -c 'printf "%s\n" "$1" >"$2"' sh "$device_name" "${driver_dir}/unbind" 2>/dev/null; then
            # Let the detach settle before re-attaching the same device.
            sleep 1
            run_bounded "$STEP_TIMEOUT_S" sh -c 'printf "%s\n" "$1" >"$2"' sh "$device_name" "${driver_dir}/bind" 2>/dev/null ||
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
run_bounded "$STEP_TIMEOUT_S" systemctl restart bluetooth.service ||
    log "rung 4/5: bluetooth.service restart failed or timed out (continuing)"

log "rung 5/5: starting ${DAEMON_SERVICE}"
run_bounded "$STEP_TIMEOUT_S" systemctl start "$DAEMON_SERVICE" ||
    log "rung 5/5: ${DAEMON_SERVICE} start failed or timed out (continuing)"

# Success is the observed postcondition, not the fact that the rungs ran.
sample_postcondition
sample_status=$?
if [ "$sample_status" -eq 124 ]; then
    log "postcondition: controllers=${PC_COUNT} powered=unknown -- not sampled inside the ${DEADLINE_S}s deadline"
    exit "$EXIT_DEADLINE"
fi
if [ "$sample_status" -ne 0 ]; then
    if [ -n "$PC_MACS" ]; then
        log "postcondition: controllers=${PC_COUNT} powered=unknown (${PC_MACS}) -- sample failed: ${PC_ERROR}"
    else
        log "postcondition: controllers=${PC_COUNT} powered=unknown -- sample failed: ${PC_ERROR}"
    fi
    exit "$EXIT_SAMPLE_FAILED"
fi
if [ "$PC_COUNT" -eq 0 ]; then
    log "postcondition: controllers=0 powered=no -- no controller enumerated after the ladder"
    exit "$EXIT_NO_CONTROLLER"
fi
if [ "$PC_POWERED" != yes ]; then
    log "postcondition: controllers=${PC_COUNT} powered=no (${PC_MACS}) -- controller enumerated but not powered"
    exit "$EXIT_UNPOWERED"
fi
log "postcondition: controllers=${PC_COUNT} powered=yes (${PC_POWERED_MAC}) -- recovery succeeded"
exit 0
