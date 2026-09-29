"""Install the Cairn launcher shim for Codex into HOME/.local/bin."""
import os
import sys
from pathlib import Path

SHIM = """#!/bin/sh
# cairn launcher shim
exec "$CAIRN_REAL_CODEX" "$@"
"""


def main(home):
    bindir = Path(home) / ".local" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    # TODO: install SHIM as bindir/"codex" and remember the real codex path in CAIRN_REAL_CODEX
    raise SystemExit("not implemented")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.environ["HOME"])
