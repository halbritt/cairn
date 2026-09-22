#!/bin/sh
set -eu
exec /usr/bin/python3 -I "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/isolate.py" "$@"
