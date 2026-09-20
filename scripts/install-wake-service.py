#!/usr/bin/env python3
"""Install or update one owner-configured Cairn wakeup user service."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import tempfile


def quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def install(binary, source, home, unit_directory):
    directory = home / "wake"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    frozen_bytes = source.read_bytes()
    stream = tempfile.NamedTemporaryFile(mode="wb", dir=directory, prefix=".install-", suffix=".tmp", delete=False)
    temp_path = Path(stream.name)
    try:
        with stream:
            stream.write(frozen_bytes)
        subprocess.run([str(binary), "wake", "check", "--config", str(temp_path)], check=True)
        chosen = None

        def choose(pairs):
            nonlocal chosen
            chosen = None
            for key, value in pairs:
                if key.lower() == "name" and value is not None:
                    chosen = value
            return dict(pairs)

        json.loads(frozen_bytes.decode(), object_pairs_hook=choose)
        name = chosen
        unit_directory.mkdir(parents=True, exist_ok=True)
        with (unit_directory / (".cairn-wake-" + name + ".lock")).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            activate(binary, temp_path, directory / (name + ".json"), unit_directory, name)
    finally:
        temp_path.unlink(missing_ok=True)


def activate(binary, temp_path, destination, unit_directory, name):
    unit = "cairn-wake-" + name + ".service"
    unit_path = unit_directory / unit
    if unit_path.exists():
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)
    temp_path.replace(destination)
    unit_path.write_text(f'''[Unit]
Description=Cairn request wakeups for {name}
After=cairn-api.service
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart={quoted(binary)} wake serve --config {quoted(destination)}
Restart=on-failure
RestartSec=5s
TimeoutStopSec=45s
KillMode=control-group
UMask=0077

[Install]
WantedBy=default.target
''')
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", unit], check=True)
    subprocess.run(["systemctl", "--user", "is-active", unit], check=True)
    print(destination)
    print(unit_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path.home() / ".local/bin/cairn")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/cairn")
    parser.add_argument("--unit-directory", type=Path, default=Path.home() / ".config/systemd/user")
    args = parser.parse_args()
    install(args.binary.resolve(strict=True), args.config.resolve(strict=True), args.home.resolve(), args.unit_directory.resolve())
