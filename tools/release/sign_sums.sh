#!/bin/sh
# Sign a release digest list with minisign.
#
#   MINISIGN_SECRET_KEY="$(cat wmr-bridge.key)" tools/release/sign_sums.sh [DIR]
#
# Writes DIR/SHA256SUMS.minisig over DIR/SHA256SUMS and verifies the result
# against the pinned public key (pi/minisign.pub, or WMR_MINISIGN_PUBKEY when it
# names another file). The secret key arrives in the environment so the release
# workflow can pass the repository secret without writing it into the
# checkout; it is staged in a 0600 file for minisign and removed on every exit
# path. The signature covers SHA256SUMS only, which in turn covers every asset
# in DIR, so one signature protects the whole release.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "${SCRIPT_DIR}/../.." && pwd)"
DIR="${1:-dist}"
SUMS="${DIR}/SHA256SUMS"
SIG="${SUMS}.minisig"
PUBKEY="${WMR_MINISIGN_PUBKEY:-${REPO_ROOT}/pi/minisign.pub}"

die() {
    echo "sign_sums.sh: $*" >&2
    exit 1
}

[ -d "$DIR" ] || die "not a directory: $DIR"
[ -s "$SUMS" ] || die "missing or empty $SUMS"
[ -n "${MINISIGN_SECRET_KEY:-}" ] || die "MINISIGN_SECRET_KEY is not set"
command -v minisign >/dev/null 2>&1 || die "minisign is not installed"
[ -f "$PUBKEY" ] || die "no public key to verify against: $PUBKEY"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/wmr-minisign.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM HUP
umask 077
KEY="${TMP_DIR}/minisign.key"
printf '%s\n' "$MINISIGN_SECRET_KEY" > "$KEY"

minisign -S -s "$KEY" -m "$SUMS" -x "$SIG" ||
    die "minisign could not sign $SUMS"
minisign -V -q -m "$SUMS" -x "$SIG" -p "$PUBKEY" ||
    die "$SIG does not verify against $PUBKEY"

echo "SHA256SUMS.minisig: signed $SUMS"
