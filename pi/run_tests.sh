#!/bin/sh
# Run the Pi host-bridge tests. No hardware, third-party packages or root needed.
set -eu
cd "$(dirname "$0")"
exec python3 -m unittest discover -s tests -v
