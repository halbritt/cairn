"""Codex backend: launches `codex exec` and parses its JSON answer."""
import json
import subprocess


def command(prompt):
    return ["codex", "exec", "--json", prompt]


def run(prompt):
    out = subprocess.run(command(prompt), capture_output=True, text=True, check=True).stdout
    return parse(out)


def parse(text):
    data = json.loads(text)  # malformed responses raise and are rejected
    if "result" not in data:
        raise ValueError("missing result")
    return data
