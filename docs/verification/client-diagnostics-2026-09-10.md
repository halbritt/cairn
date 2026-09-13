# Actionable client diagnostics

A diagnostic launch with a deliberately absent token file returned “operation
failed; inspect the local store and task runtime.” That message pointed away
from the actual failure. The client now identifies token-file I/O failure as
`CLIENT_SETUP_FAILED` and Unix dial failure as `API_CONNECTION_FAILED`. Messages
name the relevant configuration check without exposing paths or credentials.
The CLI prints these messages with exit 7; MCP startup and tool errors carry the
same diagnoses through their existing error channels.

This is a client behavior repair. The existing `core.Error` retains the original
cause for trusted Go callers, and the CLI formatter admits the two new codes.
There is no new error framework. Token-source validation, explicit connection
selection, API authentication, request bounds and execution ordering remain.
Unknown CLI errors retain the existing privacy mask.

## Verification

The pending test initially had a missing brace. Correcting that test syntax
produced the intended failing missing-token assertion. A second failing test
reproduced the raw Unix socket error. Both pass after classification and verify
that the underlying OS error remains inspectable. A pre-cancelled call retains
`context.Canceled`; unsafe token sources still refuse.

The executable CLI test then caught a separate masking problem: the new code
appeared in JSON, but the message still blamed the store. After the formatter
repair, `scripts/check_client_diagnostics.py` passes through actual CLI and MCP
processes. It covers absent-token startup, missing-socket CLI and MCP tool calls,
private path/token non-disclosure, invalid file permissions and continued masking
of unknown operator database errors. It runs within the ordinary authenticated
API integration check, which separately exercises successful requests.

Focused Go race checks, `make check` and full `make test-integration` passed.
The [manifest](client-diagnostics-2026-09-10.json) records source and log hashes.
Stat/read failure branches were reviewed in source, without forced runtime faults.
Installation remains separate from these checkout checks.

## Boundaries and decision

The affected consumers are the ordinary agent CLI, MCP facade and authenticated
runner. Their shared client still makes one request without application retries.
Existing authenticated runner tests lose responses after actual claim/outcome
commits and check that the request is not retried, run state remains inspectable,
and a second launch is refused. A failure after connecting remains outside the
new dial classification.

An API dial failure after earlier run steps now retains `API_CONNECTION_FAILED`
and exit 7 instead of the generic `RUN_FAILED`/exit 1 fallback. Retained result
fields, receipts and pending artifacts remain available; the new code does not
assert that earlier steps had no effects. The [client contract](../local-api.md)
describes this distinction. Token setup errors happen before client construction.
File closure, HTTP response closure, caller cancellation, the 35-second client
timeout and idle connection cleanup keep their existing owners.

Leaving the old message preserves a demonstrated diagnostic obstacle. A blanket
transport classification could misdescribe failures after mutation, and retries
would change execution safety. The selected repair changes only known failure
sites and their presentation under the owner's delegated implementation scope.
It needs an updated CLI/MCP executable, with no API restart or database migration.
No model call or incremental memory-task benefit is claimed. The separate native
OpenCode startup stall remains unresolved.

Pincite packet `pkt-9d4a1d5d763d01a8` supported explicit contextual errors,
context propagation, failure policy and preservation of existing behavior. The
bounded decision receipt validates and all three retrieval consumption records
are closed. The 29 unmet obligations concern unrelated data, configuration,
interface or monitoring surfaces, stronger external claims, or a separate incident
record where direct reproduction is already available. Their individual
nonmaterial classifications remain in the manifest.

## Installed verification

[CI for `d0f04d8`](https://github.com/halbritt/cairn/actions/runs/34559411262)
passed. The clean CLI from that commit is installed and its actual CLI/MCP
failure checks pass. The running API remains clean `ed29cbc` at PID 235929, with
the same executable digest; no restart or migration occurred. Fresh MCP processes
use the new installed binary; already-running facades keep their loaded build.
All 88 ordinary record versions retain the same complete inventory digest.

An installed hosted-profile search and JSON pull returned connection guidance
`bbf8593d-0b9e-470e-b55e-c500ed9e1dca` v1 with its expected body hash. Its explicit
connection/default-identity boundary remains consistent with current source and
tests. This verifies ordinary retrieval with the newer client and existing API;
it does not establish an incremental task benefit. The source/log hashes and
both installed identities are retained in the manifest.
