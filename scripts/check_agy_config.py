#!/usr/bin/env python3
"""Check Agy's native MCP configuration in a new isolated home.

Tested with Agy 1.2.0 and 1.2.11; other versions are refused.

No model call, owner configuration, API token or database is used. This verifies
registration bytes and lifecycle, not native discovery or execution of tools.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

TESTED_VERSIONS = ("1.2.0", "1.2.11")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy", required=True, type=Path)
    parser.add_argument("--cairn", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    agy, cairn = args.agy.resolve(strict=True), args.cairn.resolve(strict=True)
    root = args.output.resolve()
    root.mkdir(mode=0o700)
    env = {key: os.environ[key] for key in ("PATH", "LANG", "TERM") if key in os.environ}
    for key in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        directory = root / key.lower()
        directory.mkdir(mode=0o700)
        env[key] = str(directory)

    def invoke(label, *command):
        result = subprocess.run([str(agy), *command], cwd=root, env=env,
                                capture_output=True, text=True, timeout=20)
        (root / (label + ".stdout")).write_text(result.stdout)
        (root / (label + ".stderr")).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(f"Agy {label} failed; inspect retained output")
        return result.stdout.strip()

    version = invoke("version", "--version")
    if version not in TESTED_VERSIONS:
        raise ValueError("this configuration check covers Agy " + " and ".join(TESTED_VERSIONS) + " only")
    command = ["mcp", "--socket", str(root / "socket with spaces"), "--token-file",
               str(root / "synthetic-token-not-created"), "--repo", "fixture:agy",
               "--task", "explicit-task", "--run", "explicit-run", "--tokens", "32000",
               "--task-phase", "validation"]
    plan = dict(schema="cairn.agy-configuration-plan/1", version=version,
                agy_sha256=digest(agy), cairn_sha256=digest(cairn),
                script_sha256=digest(Path(__file__)), command=[str(cairn), *command],
                model_calls=0, owner_configuration_used=False,
                scope="Native configuration only; no tool-runtime or task-value claim")
    (root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    assert invoke("empty-list", "mcp", "list") == "No MCP servers configured."
    invoke("unrelated", "mcp", "add", "unrelated", "--", "/bin/false", "literal $(value)", "line\nbreak")
    config = Path(env["HOME"]) / ".gemini/config/mcp_config.json"

    def snapshot(label):
        value = json.loads(config.read_text())
        (root / (label + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        return value["mcpServers"]

    unrelated = snapshot("before")["unrelated"]
    invoke("add", "mcp", "add", "cairn-fixture", "--", str(cairn), *command)
    registered = snapshot("registered")
    expected = dict(command=str(cairn), args=command, disabled=False)
    assert registered == {"unrelated": unrelated, "cairn-fixture": expected}
    invoke("list", "mcp", "list")
    invoke("disable", "mcp", "disable", "cairn-fixture")
    assert snapshot("disabled") == {"unrelated": unrelated, "cairn-fixture": dict(expected, disabled=True)}
    invoke("enable", "mcp", "enable", "cairn-fixture")
    # Native enable removes the disabled key; native add writes false explicitly.
    assert snapshot("enabled") == {"unrelated": unrelated,
                                   "cairn-fixture": dict(command=str(cairn), args=command)}
    changed = ["next-run" if item == "explicit-run" else item for item in command]
    invoke("update", "mcp", "add", "cairn-fixture", "--", str(cairn), *changed)
    assert snapshot("updated") == {"unrelated": unrelated, "cairn-fixture": dict(expected, args=changed)}
    invoke("remove", "mcp", "remove", "cairn-fixture")
    assert snapshot("removed") == {"unrelated": unrelated}
    report = dict(schema="cairn.agy-configuration-report/1", plan_sha256=digest(root / "plan.json"),
                  exact_argv=True, unrelated_entry_preserved=True, explicit_scope_updated=True,
                  enable_disable_remove=True, native_tool_execution=False, model_calls=0)
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
