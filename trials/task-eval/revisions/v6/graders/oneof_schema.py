#!/usr/bin/env python3
"""v6 grader for oneof-schema: does the backend hand a oneOf schema to Codex's native output-schema option?

Run from the case workspace. Imports surveyor.codex_backend with subprocess.run
and subprocess.Popen intercepted, calls run("probe") (or command("probe") when
run is absent), and inspects every captured argv.

Exit 0: no native schema option is passed, or the schema passed contains no oneOf.
Exit 1: a native schema option is passed with a schema file containing oneOf.
Exit 3: no Codex invocation could be observed.
"""
import json
from pathlib import Path
import subprocess
import sys

captured = []


class Fake:
    returncode = 0
    stdout = json.dumps({"result": {"finding": "probe"}})
    stderr = ""

    def __init__(self, *a, **k):
        pass

    def communicate(self, *a, **k):
        return self.stdout, self.stderr

    def wait(self, *a, **k):
        return 0


def fake_run(args, *a, **k):
    captured.append([str(x) for x in (args if isinstance(args, (list, tuple)) else [args])])
    return Fake()


def fake_popen(args, *a, **k):
    captured.append([str(x) for x in (args if isinstance(args, (list, tuple)) else [args])])
    return Fake()


def schema_paths(argv):
    paths = []
    for i, arg in enumerate(argv):
        if arg.startswith("--output-schema="):
            paths.append(arg.split("=", 1)[1])
        elif arg in ("--output-schema", "--output_schema") and i + 1 < len(argv):
            paths.append(argv[i + 1])
    return paths


def main():
    sys.path.insert(0, ".")
    subprocess.run, subprocess.Popen = fake_run, fake_popen
    try:
        import surveyor.codex_backend as backend
    except Exception as error:
        print("import failed:", error)
        return 3
    try:
        if hasattr(backend, "run"):
            backend.run("probe")
        elif hasattr(backend, "command"):
            captured.append([str(x) for x in backend.command("probe")])
    except Exception as error:
        print("invocation raised:", type(error).__name__, error)
    if not captured:
        return 3
    for argv in captured:
        for path in schema_paths(argv):
            text = Path(path).read_text() if Path(path).is_file() else ""
            if "oneOf" in text:
                print("native schema option with oneOf schema:", path)
                return 1
    print(json.dumps(dict(invocations=len(captured), schema_options=[schema_paths(a) for a in captured])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
