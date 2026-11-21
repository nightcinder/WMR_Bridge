#!/bin/bash
# wlan-cycle: take one network interface down for N seconds, restore it, and
# record the Bluetooth adapter invariants before and after.
#
# The 2026-11-21 bench ran an out-of-tree `wmr-agent wlan-cycle 60` and lost
# both controllers, so the trigger could not be reproduced from this tree.
# This is the in-tree form. It is a bench tool: install.sh and build_deb.sh
# deliberately do not package it.
#
# The EXIT trap restores the interface on every path after the down,
# including INT, TERM and HUP, and records the after sample.
#
# Machine-readable stdout lines:
#   bt-before: controllers=<n|unknown> powered=<yes|no|unknown>
#   bt-after:  controllers=<n|unknown> powered=<yes|no|unknown>
#
# usage: wlan_cycle.sh [--interface IFACE] N
# exit: 0 cycle complete, 1 ip failed, 64 usage, 75 bt-recover running,
#       77 not root, 129/130/143 signal.
set -u

INTERFACE=wlan0
SAMPLE_TIMEOUT_S="${WLAN_CYCLE_SAMPLE_TIMEOUT_S:-10}"
case "$SAMPLE_TIMEOUT_S" in ''|*[!0-9]*|0) SAMPLE_TIMEOUT_S=10 ;; esac

usage() { echo "usage: wlan_cycle.sh [--interface IFACE] N" >&2; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --interface)
            [ "$#" -ge 2 ] || { usage; exit 64; }
            INTERFACE="$2"
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        -*) usage; exit 64 ;;
        *) break ;;
    esac
done
[ "$#" -eq 1 ] || { usage; exit 64; }
DOWN_SECONDS="$1"
case "$DOWN_SECONDS" in ''|*[!0-9]*) usage; exit 64 ;; esac
[ -n "$INTERFACE" ] || { usage; exit 64; }
if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: must run as root: sudo $0" >&2
    exit 77
fi

# A recovery ladder run owns the adapter; cycling Wi-Fi under it would make
# the run's postcondition and this record describe different states. The unit
# is Type=oneshot without RemainAfterExit, so a running ladder reports
# `activating`, not `active`; query ActiveState instead of `is-active`.
recovery_state="$(systemctl show -p ActiveState --value wmr-bridge-bt-recover.service 2>/dev/null)"
case "$recovery_state" in
    active|activating|reloading|refreshing)
        echo "wlan-cycle: refusing to start: wmr-bridge-bt-recover.service is $recovery_state" >&2
        exit 75
        ;;
esac

# Sets CB_COUNT and CB_POWERED from one bounded `bluetoothctl list`/`show`.
sample_bt() {
    local label="$1" list rc mac macs info
    CB_COUNT=unknown
    CB_POWERED=unknown
    list="$(timeout "$SAMPLE_TIMEOUT_S" bluetoothctl list 2>/dev/null)"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        CB_COUNT=0 CB_POWERED=no
        macs=""
        while IFS= read -r line; do
            case "$line" in
                Controller\ *)
                    mac="${line#Controller }"; mac="${mac%% *}"
                    CB_COUNT=$((CB_COUNT + 1))
                    macs="${macs:+$macs }$mac"
                    ;;
            esac
        done <<EOF
$list
EOF
        for mac in $macs; do
            info="$(timeout "$SAMPLE_TIMEOUT_S" bluetoothctl show "$mac" 2>/dev/null)" ||
                continue
            case "$info" in
                *"Powered: yes"*)
                    CB_POWERED=yes
                    break
                    ;;
            esac
        done
    fi
    echo "bt-${label}: controllers=${CB_COUNT} powered=${CB_POWERED}"
}
sample_bt before

sleep_pid=""
on_exit() {
    status=$?
    trap - EXIT INT TERM HUP
    if [ -n "$sleep_pid" ]; then
        kill "$sleep_pid" 2>/dev/null
        wait "$sleep_pid" 2>/dev/null
        sleep_pid=""
    fi
    if ip link set "$INTERFACE" up; then
        echo "wlan-cycle: restored: $INTERFACE"
    else
        echo "wlan-cycle: FAIL: could not restore $INTERFACE" >&2
        [ "$status" -ne 0 ] || status=1
    fi
    sample_bt after
    exit "$status"
}
trap on_exit EXIT
trap 'echo "wlan-cycle: signal: INT" >&2; exit 130' INT
trap 'echo "wlan-cycle: signal: TERM" >&2; exit 143' TERM
trap 'echo "wlan-cycle: signal: HUP" >&2; exit 129' HUP

echo "wlan-cycle: down: $INTERFACE for ${DOWN_SECONDS}s"
if ! ip link set "$INTERFACE" down; then
    echo "wlan-cycle: FAIL: ip link set $INTERFACE down" >&2
    exit 1
fi

# A background sleep plus wait lets a trapped signal run the restore trap
# immediately instead of after the full down window.
sleep "$DOWN_SECONDS" &
sleep_pid=$!
wait "$sleep_pid"
status=$?
sleep_pid=""
[ "$status" -eq 0 ] || exit "$status"
