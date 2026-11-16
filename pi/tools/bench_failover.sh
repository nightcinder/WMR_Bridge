#!/bin/bash
# Headless wlan0 failover for bench sheet items 4 (TCP-to-BLE) and 5 (BLE-to-TCP).
#
#   sudo /opt/wmr-bridge/pi/tools/bench_failover.sh
#   sudo pi/tools/bench_failover.sh --down-seconds 20 --log /tmp/failover.log
#   sudo pi/tools/bench_failover.sh --detach    # keep running if SSH drops
#
# Brings wlan0 down for --down-seconds, then up again, and records the IPv4
# recovery in the log: T0 when `ip link set wlan0 up` is issued, T1 when wlan0
# first reports an IPv4 address, and T1 - T0 in milliseconds.
#
# Every exit path -- success, error, INT, TERM or HUP from a dropped SSH
# session -- runs `ip link set wlan0 up`, so a failed bench cannot leave the Pi
# offline. Needs root for CAP_NET_ADMIN. `--detach` re-runs the script under
# nohup only after the root check, so a detach cannot hide a permission error.
set -u

INTERFACE=wlan0
DOWN_SECONDS=10
ADDR_TIMEOUT=30
LOG=/tmp/failover.log
DETACH=0
DETACHED=0

usage() {
    cat <<'EOF'
usage: bench_failover.sh [--down-seconds N] [--timeout S] [--log FILE] [--detach]

  --down-seconds N  seconds to keep wlan0 down (default 10)
  --timeout S       seconds to wait for the first IPv4 after T0 (default 30)
  --log FILE        log file (default /tmp/failover.log)
  --detach          re-run under nohup so a dropped SSH session is harmless
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --down-seconds)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            DOWN_SECONDS="$2"
            shift 2
            ;;
        --timeout)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            ADDR_TIMEOUT="$2"
            shift 2
            ;;
        --log)
            [ "$#" -ge 2 ] || { usage >&2; exit 64; }
            LOG="$2"
            shift 2
            ;;
        --detach)
            DETACH=1
            shift
            ;;
        # Internal flag for the child of --detach; not part of the interface.
        --detached)
            DETACHED=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 64
            ;;
    esac
done

stamp() { date -u +%Y-%m-%dT%H:%M:%S.%3NZ; }
now_ms() { date +%s%3N; }

log() {
    line="$(stamp) $*"
    printf '%s\n' "$line" >>"$LOG"
    # The detached child's stdout already is the log file.
    if [ "$DETACHED" = 0 ]; then
        printf '%s\n' "$line"
    fi
}

# Root is checked before the nohup detach: a detached run that cannot touch the
# interface would report the failure only into the log, after the session that
# could fix it is gone.
if [ "$(id -u)" -ne 0 ]; then
    echo "FAIL: must run as root: sudo $0" >&2
    exit 77
fi

if [ "$DETACH" = 1 ] && [ "$DETACHED" = 0 ]; then
    nohup "$0" --detached --down-seconds "$DOWN_SECONDS" \
        --timeout "$ADDR_TIMEOUT" --log "$LOG" >>"$LOG" 2>&1 &
    echo "detached pid $!: wlan0 down for ${DOWN_SECONDS}s, log $LOG"
    exit 0
fi

on_exit() {
    status=$?
    trap - EXIT INT TERM HUP
    if ip link set "$INTERFACE" up >>"$LOG" 2>&1; then
        log "restore: ip link set $INTERFACE up"
    else
        log "FAIL: could not restore $INTERFACE"
        [ "$status" -ne 0 ] || status=1
    fi
    if [ "$status" -eq 0 ]; then
        log "PASS: $INTERFACE restored"
    else
        log "FAIL: failover bench exit $status"
    fi
    exit "$status"
}
trap on_exit EXIT
trap 'log "signal: INT"; exit 130' INT
trap 'log "signal: TERM"; exit 143' TERM
trap 'log "signal: HUP"; exit 129' HUP

log "run: $INTERFACE down for ${DOWN_SECONDS}s, address timeout ${ADDR_TIMEOUT}s"

if ! ip link set "$INTERFACE" down >>"$LOG" 2>&1; then
    log "FAIL: ip link set $INTERFACE down"
    exit 1
fi
log "down: ip link set $INTERFACE down"
sleep "$DOWN_SECONDS"

T0="$(now_ms)"
log "T0 $INTERFACE up issued"
if ! ip link set "$INTERFACE" up >>"$LOG" 2>&1; then
    log "FAIL: ip link set $INTERFACE up"
    exit 1
fi

deadline=$((T0 + ADDR_TIMEOUT * 1000))
while :; do
    ADDR="$(
        ip -o -4 addr show dev "$INTERFACE" 2>/dev/null |
            awk '$3 == "inet" { print $4; exit }'
    )"
    if [ -n "$ADDR" ]; then
        T1="$(now_ms)"
        log "T1 $INTERFACE IPv4 $ADDR after $((T1 - T0)) ms"
        log "PASS: T1 - T0 = $((T1 - T0)) ms"
        exit 0
    fi
    [ "$(now_ms)" -lt "$deadline" ] || break
    sleep 0.25
done

log "FAIL: no IPv4 on $INTERFACE within ${ADDR_TIMEOUT}s of T0"
exit 1
