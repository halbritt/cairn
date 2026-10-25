"""Private, sanitized persistence for native notice-batch advance responses."""

import json
import os
from pathlib import Path
import stat

_RESPONSE_SCHEMA = "cairn.compact-inbox-batch/1"
_SAFE_KEYS = {
    "schema", "ok", "next", "remainder", "batch_index", "batch_limit", "status",
}
_SAFE_STATUS = {
    "API_CONNECTION_FAILED", "API_UNAVAILABLE", "CONFIRMATION_MISMATCH",
    "HANDLE_EXPIRED", "INVALID_HOST", "INVALID_REQUEST", "NOT_FOUND",
    "NOT_THIS_SESSION", "PAYLOAD_UNAVAILABLE", "SCHEMA_MISMATCH",
    "SESSION_BUSY", "SOURCE_UNAVAILABLE", "STALE_LEASE", "STALE_SESSION",
    "UNSUPPORTED_CONTROL",
}
_SAFE_REMAINDERS = {"batch_limit", "priority_waiting"}


def _bounded_code(value, allowed):
    return value if isinstance(value, str) and value in allowed else "other"


def _bounded_integer(value, minimum, maximum):
    if type(value) is int and minimum <= value <= maximum:
        return value
    return None


class AdvanceDiagnosticWriter:
    def __init__(self, path, disposable_root):
        self.path = Path(path)
        root = Path(disposable_root).resolve(strict=True)
        if not self.path.is_absolute():
            raise ValueError("diagnostic output must be an absolute path")
        parent = self.path.parent
        parent_info = parent.lstat()
        if (stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode) or
                parent_info.st_uid != os.getuid() or stat.S_IMODE(parent_info.st_mode) & 0o077):
            raise ValueError("diagnostic output parent must be an owner-only directory")
        resolved = parent.resolve(strict=True) / self.path.name
        try:
            resolved.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("diagnostic output must be outside the disposable root")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        os.fchmod(fd, 0o600)
        self.stream = os.fdopen(fd, "w", encoding="utf-8")

    def record_advance(self, response, returncode):
        response = response if isinstance(response, dict) else {}
        has_next = "next" in response
        next_value = response.get("next")
        row = {
            "operation": "advance",
            "keys": sorted(_SAFE_KEYS.intersection(response)),
            "schema": response.get("schema") if response.get("schema") == _RESPONSE_SCHEMA else "other",
            "ok": response.get("ok") if type(response.get("ok")) is bool else None,
            "status": _bounded_code(response.get("status"), _SAFE_STATUS),
            "next_type": ("null" if next_value is None else
                          "string" if isinstance(next_value, str) else
                          type(next_value).__name__) if has_next else "absent",
            "remainder": _bounded_code(response.get("remainder"), _SAFE_REMAINDERS),
            "batch_index": _bounded_integer(response.get("batch_index"), 1, 3),
            "batch_limit": _bounded_integer(response.get("batch_limit"), 1, 3),
            "returncode": _bounded_integer(returncode, -128, 255),
        }
        self.stream.write(json.dumps(row, sort_keys=True) + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self):
        self.stream.close()
