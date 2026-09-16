# Agent wakeup verification — 2026-09-15

## Implemented slice

The [contract](../agent-wakeups.md) is implemented as PostgreSQL wake attempts,
request-only claims, delayed pre-launch retries, authenticated coordination APIs,
a host supervisor, and fresh systemd-managed workers using the existing runner.
The owner requested OpenCode and Hermes deployment and restart of Hermes and its
gateway. The [plan](../plans/agent-wakeups.md) tracks installation separately.

## Tested claims

| Boundary | Evidence |
| --- | --- |
| Only requests launch; one unfinished attempt per inbox; claim retries retain identity; expired deliveries remain held | `core/agent_wakeups_test.go`, disposable PostgreSQL and race-enabled integration suite |
| Duplicate worker entry is refused; zero exit without handling fails; completion survives reconciliation | Store tests and `scripts/check_wakeups.py` |
| Safe pre-launch retries wait, stop after three claims; restore fencing preserves holds; another inbox cannot finish an attempt | Store tests |
| Real API, process wrapper and systemd cgroup; explicit atomic result linked to runner receipt | `scripts/check_wakeups.py` |
| Supervisor killed while detached child lives; restart kills the cgroup and never relaunches uncertain work | Process probe with actual SIGKILL and detached `/usr/bin/sleep` |
| API outage stops a worker at failed renewal; recovery reconciles its hold | Process probe with stopped/restarted API |
| Installed OpenCode and Hermes launch a fresh run, invoke their native shell tool and complete the delivery | Optional native mode of `scripts/check_wakeups.py`, isolated homes and synthetic loopback provider |
| Operational event and wake tables survive a real backup and restore | `make test-lifecycle`, exact JSON comparison before/after restore |

`make test-integration` runs race-enabled Go packages and real CLI/API probes;
its systemd wake probe runs where a user manager is available and reports a skip
elsewhere. `make check` and all 101 Python unit tests passed. The final native
probe passed six checks, including both actual harness executables. Local logs
are `/tmp/cairn-wake-integration-final.log`, `/tmp/cairn-wake-check.log`,
`/tmp/cairn-wake-lifecycle.log`, `/tmp/cairn-wake-python.log`, and
`/tmp/cairn-wake-final-native.log`; these scratch logs are not repository artifacts.

## Limits

The native provider fixture establishes executable launch, tool execution and
completion wiring. It makes no model-quality, real-work usefulness, live-provider
reliability or exactly-once external-effect claim. Explicit handler completion
is still a report; independent task acceptance is separate.

The host is the recovery owner. Unit inspection includes cgroup population;
unconfirmed cleanup retains the hold. A database moved to another host cannot
verify the old host's processes. Stop old supervisors and workers before restore
or host migration. An operator can decide whether a new request is appropriate
after uncertain execution; the system does not make that decision automatically.
