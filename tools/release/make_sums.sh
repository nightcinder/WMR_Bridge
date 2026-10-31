#!/bin/sh
# Write SHA256SUMS covering every file in a directory of release assets.
#
#   tools/release/make_sums.sh [DIR]     # default: dist
#
# Writes DIR/SHA256SUMS with one line per asset, sorted by file name and
# relative to DIR, so a tester can download the release assets, run
# `sha256sum -c SHA256SUMS` beside them, and check every file at once.
# SHA256SUMS is not listed in itself: it cannot contain its own digest. The
# per-file `.sha256` files stay as they are and are covered like any asset.
set -eu

DIR="${1:-dist}"
[ -d "$DIR" ] || { echo "not a directory: $DIR" >&2; exit 66; }

SUMS="${DIR}/SHA256SUMS"

(
    cd "$DIR"
    rm -f SHA256SUMS
    find . -maxdepth 1 -type f ! -name SHA256SUMS -print |
        sed 's|^\./||' |
        LC_ALL=C sort |
        while IFS= read -r name; do
            sha256sum -- "$name"
        done > SHA256SUMS

    [ -s SHA256SUMS ] || {
        echo "no release assets in $DIR" >&2
        exit 1
    }

    sha256sum --check --quiet SHA256SUMS
)

echo "SHA256SUMS: $(wc -l < "$SUMS") assets in $DIR"
