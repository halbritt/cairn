#!/usr/bin/env python3
"""Install or update one owner-configured Cairn wakeup user service."""
import argparse
import json
from pathlib import Path
import subprocess


def quoted(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def install(binary, source, home, unit_directory):
    subprocess.run([str(binary), "wake", "check", "--config", str(source)], check=True)
    config = json.loads(source.read_text())
    name = config["name"]  # validated by the executable
    unit = "cairn-wake-" + name + ".service"
    directory = home / "wake"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / (name + ".json")
    unit_directory.mkdir(parents=True, exist_ok=True)
    unit_path = unit_directory / unit
    if unit_path.exists():
        # Finish/stop the old attempt before changing its binding file.
        subprocess.run(["systemctl", "--user", "stop", unit], check=True)
    encoded = json.dumps(config, indent=2) + "\n"
    temporary = destination.with_suffix(".pending")
    temporary.write_text(encoded)
    temporary.chmod(0o600)
    temporary.replace(destination)
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
