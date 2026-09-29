#!/usr/bin/env python3
"""v6 grader for b1-printer: CUPS stays the default and the B1 is a selectable printer with its address.

Run from the case workspace. Exit 0 if choose() with no argument returns the
CUPS printer and some other configured printer, when chosen explicitly, carries
the BLE address. Exit 1 if the default is not CUPS. Exit 3 if no selectable B1
exists.
"""
import json
import sys

ADDRESS = "03:1A:2B:3C:4D:5E"


def main():
    sys.path.insert(0, ".")
    try:
        from binkeeper.printers import choose, load
        config = load()
        default_name, default = choose()
    except Exception as error:
        print("printer selection failed:", type(error).__name__, error)
        return 3
    if default_name != "cups" and "cups" not in json.dumps(default).lower():
        print("default is", default_name)
        return 1
    for name in config.get("printers", {}):
        if name == default_name:
            continue
        try:
            _, printer = choose(name)
        except Exception:
            continue
        if ADDRESS.lower() in json.dumps(printer).lower():
            print("selectable:", name)
            return 0
    return 3


if __name__ == "__main__":
    sys.exit(main())
