# Assessment writes after profile changes — 2026-09-10

Authenticated assessment writes now check the receipt's original destination
and the current profile's repository before applying a write or returning a
cached response. History reads already enforced those checks. Update the API;
no database migration is required.

## Reproduction and repair

A disposable API check created an assessment under a local receipt, then used
an authenticated hosted profile with the same principal to retry the exact
request. The old API returned the earlier local assessment despite refusing its
history through the hosted profile. New requests could also append to the local
receipt. Reassigning that principal to another repository could bypass the
repository check on a cached response.

The configuration prohibits duplicate principals in one server. This concerns
retained receipts after a profile change or comparable historical state;
provisioned local and hosted profiles normally use different principals. The
retry supplies the full original request, including its reason. This is not
UUID-only disclosure of an unknown narrative or an observed production incident.

`Store.AssessRunForDestination` checks receipt access and destination inside the
existing mutation transaction, before the cached-response lookup. The API passes
its authenticated destination to that method. Request identities and digests,
version and evidence checks, witness and stored formats remain unchanged.
Trusted direct-store `AssessRun` keeps its existing behavior.

This deliberately stops accepting inconsistent earlier API traffic: mismatched
requests return `AUTHORITY_DENIED`. No old assessment or request is rewritten,
and a new request ID cannot bypass the binding. Access to an earlier receipt
requires its properly authorized original binding or the trusted operator path.
This enforces the existing receipt policy, without a new assessment standard.

## Verification

The baseline public-API regression failed by returning the synthetic prior local
narrative. With the guard, both cached retries and new writes refuse destination
changes in either direction for agent and observer profiles. A same-destination
repository reassignment also refuses. Each check reads back unchanged original
history through its proper store. Matching profiles retain exact idempotent
responses and their original witness and narrative.

Focused PostgreSQL/race checks, the full disposable integration suite and
`make check` passed. The [manifest](assessment-binding-2026-09-10.json) retains
logs and hashes. No answering model was invoked; the checks establish the API
contract, not increased task value.

The issue was found while preparing native assessment tools. Their unfinished
MCP change is saved at
`/tmp/cairn-native-assessments-20260910/native-tools-wip.patch`; it is not part of
this repair or an installed capability. Native history passed its first real-API
check; the write-tool test failed because that tool was not implemented. Those
interfaces remain follow-up work after this underlying correction.

## CI comparison correction

[CI for `14c61eb`](https://github.com/halbritt/cairn/actions/runs/34564347551)
passed the refusal assertions but failed the new retained-history comparison.
`TZ=UTC` reproduced the failure locally: pgx's timestamp and JSON-decoded history
represent the same instant with different Go `time.Location` values. The test
now converts both timestamps to UTC before comparing every assessment field.
Focused disposable checks pass in UTC and the host timezone, and `make check`
passes. This changes the test only; the initial failed CI result remains recorded.

## Installed build

[CI for `bb600c5`](https://github.com/halbritt/cairn/actions/runs/34564679136)
passed, including the PostgreSQL race suite, Python checks and authenticated
CLI/MCP integration. The clean build is now installed for the CLI and running
API. Their reported source revision is
`bb600c561ce7592f6e5b9f4577b17038f1d0fed1`; both executable hashes are
`1476433adc7892282cfe6985c466fa05e2a75a83bf4a5e4e4d34167aa9440adb`.

An ordinary hosted search and full pull returned the current assessment guide
v3 with its expected body hash. The protected use report still refused that
agent profile. The API restarted; PostgreSQL did not. All 91 ordinary record
versions retained their exact inventory fingerprint, and the semantic worker
and its service settings retained their hashes. No migration was run. Native
assessment tools are still pending; this installation repairs the existing API.
