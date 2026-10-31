#!/bin/sh
# Build and run every host C test. One list, so the plain job and the
# ASan/UBSan job cannot drift apart:
#
#   ./tests/run_c_tests.sh
#   CFLAGS="-fsanitize=address,undefined -fno-sanitize-recover=all -g" \
#       ./tests/run_c_tests.sh
#
# CC (default cc), CFLAGS (default -O2) and OUT_DIR (default a fresh temp
# directory) can be overridden. Needs no ESP-IDF or BTstack.

set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
CC=${CC:-cc}
CFLAGS=${CFLAGS:--O2}
OUT_DIR=${OUT_DIR:-$(mktemp -d)}
mkdir -p "$OUT_DIR"
cleanup() {
    [ "${KEEP_OUT_DIR:-0}" = 1 ] || rm -rf "$OUT_DIR"
}
trap cleanup EXIT INT TERM

common="-std=c99 -Wall -Wextra -Wconversion -Werror -I$root/src -I$root/tests/host_stubs $CFLAGS"

build() {
    name=$1
    extra=$2
    shift 2
    # shellcheck disable=SC2086
    $CC $common $extra "$@" -o "$OUT_DIR/$name"
}

run() {
    name=$1
    shift
    "$OUT_DIR/$name" "$@"
}

step() {
    printf '\n== %s\n' "$1"
}

step test_protocol
build test_protocol "" tests/test_protocol.c src/wmr_protocol.c src/wmr_queue.c \
    src/wmr_decoded.c src/wmr_decode.c
run test_protocol "$root/samples/controller_frames.txt"

step test_decode
build test_decode "" tests/test_decode.c src/wmr_decode.c -lm
run test_decode "$root/samples/controller_frames.txt"

step test_link
build test_link "" tests/test_link.c src/wmr_link.c
run test_link

step test_stream_mode
build test_stream_mode "" tests/test_stream_mode.c src/wmr_stream_mode.c \
    src/wmr_decoded.c src/wmr_decode.c
run test_stream_mode "$root/samples/controller_frames.txt"

step test_batch
build test_batch "" tests/test_batch.c src/wmr_batch.c src/wmr_levers.c \
    src/wmr_stream_mode.c src/wmr_queue.c src/wmr_decoded.c src/wmr_decode.c
run test_batch

step test_batch_on
build test_batch_on "-DWMR_CFG_TEST_LEVERS=1" tests/test_batch.c src/wmr_batch.c \
    src/wmr_levers.c src/wmr_stream_mode.c src/wmr_queue.c src/wmr_decoded.c src/wmr_decode.c
run test_batch_on

step test_levers
build test_levers "" tests/test_levers.c src/wmr_levers.c
run test_levers

step test_levers_on
build test_levers_on "-DWMR_CFG_TEST_LEVERS=1" tests/test_levers.c src/wmr_levers.c
run test_levers_on

step test_echo
build test_echo "" tests/test_echo.c src/wmr_echo.c
run test_echo

step test_cfg
build test_cfg "" tests/test_cfg.c src/wmr_cfg.c
run test_cfg

step test_discovery
build test_discovery "" tests/test_discovery.c src/wmr_discovery.c
run test_discovery

step test_discovery_h1
build test_discovery_h1 "-DWMR_CFG_BRIDGE_HANDS=1" tests/test_discovery.c src/wmr_discovery.c
run test_discovery_h1

step test_discovery_h2
build test_discovery_h2 "-DWMR_CFG_BRIDGE_HANDS=2" tests/test_discovery.c src/wmr_discovery.c
run test_discovery_h2

step test_log
build test_log "" tests/test_log.c
run test_log

step test_diag
build test_diag "" tests/test_diag.c src/wmr_devloss.c src/wmr_diag.c
run test_diag

step test_linkwait
build test_linkwait "" tests/test_linkwait.c src/wmr_linkwait.c
run test_linkwait

step test_status_led
build test_status_led "" tests/test_status_led.c src/wmr_status_led.c
run test_status_led

step test_hid
build test_hid "" tests/test_hid.c src/wmr_hid.c src/wmr_decoded.c src/wmr_decode.c -lm
run test_hid "$root/tests/vectors/hid_gamepad.txt" "$root/tests/vectors/hid_report_descriptor.txt"

step test_ctrl
build test_ctrl "" tests/test_ctrl.c src/wmr_ctrl.c
run test_ctrl

step test_secure
build test_secure "" tests/test_secure.c src/wmr_secure.c
run test_secure

# BTstack-side modules, driven through tests/host_stubs.
step test_cfgmgr
build test_cfgmgr "" tests/test_cfgmgr.c tests/host_stubs/host_stubs.c \
    src/wmr_cfgmgr.c src/wmr_cfg.c
run test_cfgmgr

step test_cfgmgr_off
build test_cfgmgr_off "-DWMR_CFG_FF13_READ=0" tests/test_cfgmgr_off.c tests/host_stubs/host_stubs.c \
    src/wmr_cfgmgr.c src/wmr_cfg.c
run test_cfgmgr_off

step test_linkdiag
build test_linkdiag "" tests/test_linkdiag.c tests/host_stubs/host_stubs.c \
    src/wmr_linkdiag.c src/wmr_devloss.c src/wmr_diag.c src/wmr_log.c
run test_linkdiag

step test_console
build test_console "" tests/test_console.c tests/host_stubs/host_stubs.c \
    src/wmr_console.c src/wmr_cfgmgr.c src/wmr_cfg.c src/wmr_linkdiag.c \
    src/wmr_devloss.c src/wmr_diag.c src/wmr_log.c src/wmr_link.c \
    src/wmr_stream_mode.c src/wmr_discovery.c
run test_console
