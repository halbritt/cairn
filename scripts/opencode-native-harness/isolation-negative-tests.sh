#!/bin/sh
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Deliberate ambient canaries prove the clean environment and lack of a bypass.
exec /usr/bin/env HARNESS_IN_NETNS=1 HARNESS_FIXTURE_ROOT=/must-not-be-used \
  ARBITRARY_PROVIDER_CREDENTIAL=fixture-only HTTPS_PROXY=http://127.0.0.1:9 \
  "$here/netns-isolate.sh" /usr/bin/python3 "$here/isolation_checks.py"
