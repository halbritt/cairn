"""Read-only update check: report installed vs available versions."""
from updatebot.targets import TARGETS


def plan():
    return [dict(target=t["name"], action="report") for t in TARGETS]
