"""Label printer backends."""
import json
from pathlib import Path

CONFIG = Path(__file__).with_name("printers.json")


def load():
    return json.loads(CONFIG.read_text())


def choose(requested=None):
    config = load()
    name = requested or config["default"]
    if name not in config["printers"]:
        raise KeyError(name)
    return name, config["printers"][name]
