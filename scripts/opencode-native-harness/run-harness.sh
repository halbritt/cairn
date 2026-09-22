#!/bin/sh
# Default is model-free. Native execution requires an explicit binary argument.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ]; then
  exec "$here/isolation-negative-tests.sh"
fi
if [ "$#" -lt 2 ] || [ "$1" != --native ]; then
  echo 'usage: run-harness.sh [--native /absolute/opencode-binary [iterations]]' >&2
  exit 2
fi
shift
exec "$here/netns-isolate.sh" /usr/bin/python3 "$here/admission_probe.py" "$@"
