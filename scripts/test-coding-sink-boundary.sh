#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
fixture_unit="cairn-fixture-coding-sink-$(cat /proc/sys/kernel/random/uuid)"
exec systemd-run --user --wait --pipe --collect --unit="$fixture_unit" \
  --property=Delegate=yes --property=DelegateSubgroup=controller \
  --property=RuntimeMaxSec=90 --property=KillMode=control-group \
  --property=MemoryMax=1G --property=MemorySwapMax=0 --property=TasksMax=64 \
  /usr/bin/python3 -B -m unittest discover -s "$script_dir" -p test_trial_coding_sink_boundary.py
