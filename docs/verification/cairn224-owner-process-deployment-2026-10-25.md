# CAIRN-224 native process owner attribution deployment — 2026-10-25

The provider-free exact-executable attribution fix from source commit
`4edf764b6f4ce50db42fe03f6d9613c5577a53e0` was deployed to the installed
native coordination hook. It updates `owner_process` to recognize an exact
configured `/proc/<pid>/exe` basename in addition to the existing exact `comm`
and executable-directory checks. It also refuses attribution when an observed
ancestor exits. It adds no prefix matching or caller-selected PID authority.

## Installed bytes and scope

All standard hook bindings use the shared path
`/home/halbritt/.local/share/cairn/coordination/coordination.py`. The file was
replaced atomically in place; settings, binding files, launcher commands and
other installed engines were not changed. The source candidate was staged at
`/home/halbritt/.local/share/cairn/rollouts/cairn224-owner-process-4edf764/source/coordination.py`.

| State | SHA-256 | Mode | Owner |
| --- | --- | --- | --- |
| Installed before deployment | `c50cc32315a7dc802f817e0641c567ba8a0d30ff131f6b273ff271c41fa012e3` | `0700` | UID 1000 |
| Installed after deployment | `9ae77a501c4d16a419c2395b5383825c9878c85d3972e9f86b4c32c4fc3b9e9d` | `0700` | UID 1000 |
| Staged rollback copy | `c50cc32315a7dc802f817e0641c567ba8a0d30ff131f6b273ff271c41fa012e3` | `0600` | owner-only |

The rollback copy is
`/home/halbritt/.local/share/cairn/rollouts/cairn224-owner-process-4edf764/source/coordination.before.py`.
Rollback consists of atomically restoring those exact bytes to the installed
path with mode `0700`. The candidate and rollback copy are retained in the
private rollout directory.

## Preflight and effective coverage

Immediately before replacement, the read-only effective deployment inventory
returned `ok=true`, `installed_verified=true`, `read_only_verified=true`, and
no issues. All four ordinary coordination entries pointed to the expected
shared script path. The three enabled inbox bindings passed their pin and
contract checks. `live_observed` remains false: the inventory checks installed
configuration and fixture behavior, not observed hook execution.

A read-only production database transaction immediately before replacement
reported zero unfinished native session attempts, zero unfinished wake
attempts, and zero unexpired delivery leases. The same counts remained zero in
the post-deployment read-only check. Nine registered live sessions were listed
by the authenticated read-only directory operation. No attempt, delivery,
grant, or session record was changed, and no session reload was required.

The source candidate passed `scripts/test_owner_process_attribution.py` (4
tests), `scripts/test_agent_coordination.py` (13 tests),
`scripts/test_claude_daemon_sessions.py` (13 tests), and `make check`. The
installed script's read-only `--help` invocation and authenticated
`agent-directory` read both succeeded after replacement. The post-deployment
effective inventory again returned `ok=true`, four ordinary hook entries,
three enabled inbox bindings with valid pins/contracts, and no issues. It still
reports `live_observed=false`.

The primary Cairn CLI/API remained at revision `ac705706bf654cd51a0d4531e48f046789abb8da`
with schema 072. The separate `cairn267-distinct-api-4884f6a.service` and all
other services were left untouched. No database migration, API restart,
provider call, or production test was performed.

This deploys the narrow owner attribution correction only. It does not establish
automatic compaction continuity, accepted task outcomes, token savings, or
broader CAIRN-224 acceptance; the Plane work item remains In Progress.
