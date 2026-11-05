#!/usr/bin/env python3
"""Synthetic Plane subprocess boundary; never contacts a server.

Envelope and exit contracts follow plane-cli/internal/out/out.go; list metadata
follows internal/cli/helpers.go. Inputs are a private fixture-owned JSON file.
Unknown commands/flags refuse, and every attempted invocation is recorded.
"""
import json
import os
from pathlib import Path
import sys

TRACE = {}
LOG = None


def emit(envelope, exit_code=0):
    if LOG is not None:
        with LOG.open("a") as stream:
            stream.write(json.dumps(dict(TRACE, exit_code=exit_code,
                                        error_code=envelope.get("error", {}).get("code"))) + "\n")
    print(json.dumps(envelope))


def fail(code, message, exit_code, http_status=None):
    error = dict(code=code, message=message)
    if http_status is not None:
        error["http_status"] = http_status
    emit(dict(ok=False, error=error), exit_code)
    raise SystemExit(exit_code)


def main():
    global LOG
    path = Path(os.environ["CAIRN_WORK_UI_PLANE_STATE"])
    state = json.loads(path.read_text())
    argv = sys.argv[1:]
    LOG = path.with_suffix(".calls.jsonl")
    TRACE.update(argv=argv, project_id=None, mode=None)
    flags, positional = {}, []
    values = {"--instance", "-i", "--config", "--base-url", "--project", "-p", "--workspace", "-w",
              "--fields", "--expand", "--limit", "--cursor", "--order-by", "--timeout"}
    switches = {"--no-resolve", "--all"}
    i = 0
    while i < len(argv):
        value = argv[i]
        if value in values:
            if i + 1 == len(argv):
                fail("usage", "missing flag value", 2)
            flags[value] = argv[i + 1]
            i += 2
        elif value in switches:
            flags[value] = True
            i += 1
        elif value.startswith("-"):
            fail("usage", "unsupported fixture flag: " + value, 2)
        else:
            positional.append(value)
            i += 1
    command = tuple(positional[:2])
    allowed = {("project", "get"), ("issue", "list"), ("workspace", "get")}
    if command not in allowed:
        fail("usage", "read-only fixture refuses this command", 2)
    if flags.get("--instance", flags.get("-i")) != "work-ui":
        fail("config", "fixture instance required", 2)
    if flags.get("--base-url") != state["origin"]:
        fail("config", "child origin differs from fixture binding", 2)
    if flags.get("--workspace", flags.get("-w")) != "work-ui":
        fail("config", "child workspace differs from fixture binding", 2)
    if "--config" in flags and Path(flags["--config"]).resolve() != Path(os.environ["PLANE_CONFIG"]).resolve():
        fail("config", "unexpected child configuration file", 2)
    if command == ("workspace", "get"):
        emit(dict(ok=True, data=dict(id=state["workspace"], slug="work-ui"), meta={}))
        return
    project_id = (positional[2] if command == ("project", "get") and len(positional) == 3
                  else flags.get("--project", flags.get("-p")))
    project = state["projects"].get(project_id)
    if project is None:
        fail("not_found", "unknown fixture project", 4, 404)
    if flags.get("--project", flags.get("-p")) != project_id:
        fail("config", "child project flag differs from requested fixture project", 2)
    TRACE.update(project_id=project_id, mode=project["mode"])
    if project["mode"] == "auth":
        fail("auth", "synthetic subscription-independent Plane auth rejection", 3, 401)
    if project["mode"] == "network":
        fail("network", "synthetic Plane connection failure", 6)
    if command == ("project", "get"):
        data = dict(id=project_id, workspace=state["workspace"], identifier=project["identifier"],
                    name=project["name"])
        meta = {}
    else:
        expanded = str(flags.get("--expand", "")).split(",")
        data = [dict(item) for item in project["items"]]
        if "state" not in expanded:
            for item in data:
                item["state"] = item["state"]["id"]
        if "assignees" not in expanded:
            for item in data:
                item["assignees"] = [person["id"] for person in item["assignees"]]
        total = len(data)
        limit = int(flags.get("--limit", "0"))
        if limit < 0:
            fail("usage", "negative fixture limit", 2)
        if limit:
            data = data[:limit]
        meta = dict(pagination=dict(pages=1, fetched=len(data), total_count=total, drained=len(data) == total))
    if "--fields" in flags:
        fields = flags["--fields"].split(",")
        def selected(item):
            return {key: item[key] for key in fields if key in item}
        data = [selected(item) for item in data] if isinstance(data, list) else selected(data)
    emit(dict(ok=True, data=data, meta=meta))


if __name__ == "__main__":
    main()
