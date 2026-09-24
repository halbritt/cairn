"""Provision distinct hosted inbox identities without changing existing profiles."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import tempfile


def provision(home, repo, names):
    config = home / "identities.json"
    lock_path = home / ".identities.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        identities = json.loads(config.read_text())
        directory = home / "event-profiles"
        directory.mkdir(mode=0o700, exist_ok=True)
        for name in names:
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", name):
                raise ValueError("profile names must be lowercase letters, digits or hyphens")
            principal = "agent/" + name
            path = directory / (name + ".token")
            matching = [identity for identity in identities if identity["principal"] == principal]
            if matching:
                token = path.read_text().strip()
                expected = dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                                principal=principal, repo=repo, role="agent", destination="hosted")
                if matching != [expected]:
                    raise ValueError("existing event identity does not match its profile: " + name)
                continue
            # Reuse an orphan token if a prior invocation stopped before config rename.
            if path.exists():
                token = path.read_text().strip()
                if not token:
                    raise ValueError("empty profile token: " + name)
            else:
                token = secrets.token_urlsafe(32)
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as stream:
                    stream.write(token + "\n")
            identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                                   principal=principal, repo=repo, role="agent", destination="hosted"))
        if len(identities) > 32:
            raise ValueError("Cairn supports at most 32 configured identities")
        with tempfile.NamedTemporaryFile(mode="w", dir=home, prefix=".identities-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(identities, stream, indent=2)
            stream.write("\n")
        try:
            temporary.replace(config)
        finally:
            temporary.unlink(missing_ok=True)
    return [str(directory / (name + ".token")) for name in names]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home() / ".local/share/cairn")
    parser.add_argument("--repo", required=True)
    parser.add_argument("names", nargs="+")
    args = parser.parse_args()
    for path in provision(args.home, args.repo, args.names):
        print(path)
    print("Restart cairn-api.service to load these profiles.")
