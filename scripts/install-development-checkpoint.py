#!/usr/bin/env python3
"""Install the standalone checkpoint command from this Cairn checkout."""

import argparse
import hashlib
import os
from pathlib import Path
import sys
import tempfile


SOURCE = Path(__file__).resolve().with_name("development_checkpoint.py")
TARGET = Path.home() / ".local/bin/cairn-development-checkpoint"


def install(target, check=False):
    content = SOURCE.read_bytes()
    expected = hashlib.sha256(content).hexdigest()
    matches = (not target.is_symlink() and target.is_file()
               and target.read_bytes() == content and os.access(target, os.X_OK))
    if check:
        if not matches:
            print(f"Installation differs: {target} is not executable {SOURCE} (sha256 {expected})")
            return 1
        print(f"{target} matches {SOURCE} (sha256 {expected})")
        return 0
    if not matches:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".checkpoint-install-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.chmod(0o755)
            temporary.replace(target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    print(f"{target} installed from {SOURCE} (sha256 {expected})")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify installed bytes and execution permission without writing")
    parser.add_argument("--target", type=Path, default=TARGET, help=f"installed executable (default: {TARGET})")
    args = parser.parse_args(argv)
    try:
        return install(args.target, args.check)
    except OSError as exc:
        print(f"checkpoint installation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
