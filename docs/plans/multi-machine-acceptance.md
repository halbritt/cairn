# Multi-machine implementation acceptance

Status: implemented, reviewed and deployed on Proximal and Archon. Release
`b46a0e1` passed the required local checks and the production smoke test.
Evidence below distinguishes the disposable failure tests from deployment.
The owner authorized implementation and Archon deployment, with different model
families reviewing both plans and builds. The [proposal](multi-machine-cairn.md)
defines the intended behavior and preserved contracts.

## Work and independent review

| Work | Creator | Reviewer |
| --- | --- | --- |
| Overall proposal and integration | agent-112, Codex | agent-200, Anthropic; original proposal reviewed by agent-183, Anthropic |
| Central network API and local forwarder | agent-201, Codex | agent-200, Anthropic |
| Enrollment and operator setup | agent-200, Anthropic | agent-201, Codex |
| Native attempt and completion recovery | agent-204, Anthropic | agent-203, Codex |
| Failure harness and acceptance evidence | agent-203, Codex | agent-204, Anthropic |

Record the exact reviewed revisions and remaining findings when each review
finishes. Source inspection and test reports must identify which behavior they
establish. Store all credentials and raw runtime artifacts outside the repository.

## Completion evidence required

| Requirement | Evidence needed | Current result |
| --- | --- | --- |
| One central API/store, local service on each machine | Installed configuration and observed requests from Proximal and Archon; remote clients hold no DB credentials | Passed: Proximal API/store and Archon relay deployed |
| Simple setup | One enrollment import installs required profiles/service and passes connectivity without per-agent registration secrets | Passed: actual trial and production single-file enrollment on Archon |
| Existing CLI/MCP and native hooks work remotely | Real agent on Archon retrieves a selected shareable note, receives work and publishes an explicit reply | Passed: trial and production native exchange; sandbox limit below |
| Collection independent of checkout path | Both machines share collection identity with different workspace metadata | Passed: Archon temporary workspace, canonical Proximal collection |
| Machine and role isolation | Equal native IDs remain distinct; other-machine completion and ordinary-client observer calls refused | Passed: cross-machine session borrowing refused; delivery unchanged |
| Hosted privacy and remote operation limits | Local-only body retrieval and wrong-host managed-context registration refused | Passed: hosted history/pull refuse private bodies; remote allowlist enforced |
| Durable request/reply behavior | Exact source version delivered, explicit completion retained, response linked to request; task result inspected separately | Passed: one handled delivery and one linked reply; result read back |
| Lost response and duplicate retry | Repeat same request identity after dropped response; one committed event/completion and no repeated task execution | Passed: disposable two-relay and host journal fixtures |
| Long network partition | Finish a task across lease expiry, recover its result through tested contract, preserve execution/hold/generation checks | Passed: lease-expired completion retained under exact hold; host replay regression |
| Restarts | Surviving agent and pending completion reconciled after relay/service and central API restart without replacement execution bypass | Passed: SIGKILL relay/stale socket, API restart, persisted reply journal |
| Revocation and rotation | Revoked profiles refused; replacing credentials preserves intended machine/session identity | Passed: both machine roles revoked; rotation preserves session identity |
| Restore | Disposable restored DB refuses pre-restore execution/completion; explicit recovery permits fresh authorized work | Passed: real disposable dump/restore and generation fence |
| Local compatibility | Existing Unix API and local host adapters pass required regression gates | Passed: required gates and existing local presence plus seven wake services active |
| Cross-model review | Plan and code review records from opposite family, all material findings resolved | Passed: all material findings resolved; records below |
| Build and store gates | Required local checks and disposable PostgreSQL integration tests pass on integrated revision | Passed: `make check` and `make test-integration` at `b46a0e1`; unit receipt below |
| Deployment and usability | Services restart persistently, installed revision/config verified, operator setup/recovery instructions exercised | Passed: matching clean builds, enabled relay/watcher, linger=yes, production smoke |

Network interruptions and database restores run against disposable trial state.
The existing production memory store is never a test or cleanup target. Production
deployment follows successful isolated validation; retain a rollback path and
verify live non-destructive memory/request workflows after installation.

## Recorded trial and review evidence

The integrated live candidate was `12bcb49`, built from a clean ordinary clone
with its VCS stamp intact. Archon ran the same binary. Its HTTPS relay connected
to a disposable PostgreSQL-backed API on Proximal, using a tailnet TLS endpoint.
No production database was used for failure injection, restore, or cleanup.

The real Codex session on Archon was `01a0e01a-ba2e-7092-8f6f-322cd81dadac`.
It searched and pulled note `2f0ae338-bc6a-4340-92ab-742eaeb81c92` version 1,
then received request `32ee64ab-0e8c-4c45-863f-b40fe435555f` through native hooks.
Delivery `e5abb0c2-eb2f-4533-aade-bb582ff73e2d` was handled in one attempt.
Response `1db5c1d2-cb2c-4a17-bab4-736e11ee1ced` cited completion result
`0bde4c08-e5d0-4655-af7d-c5e41747b009` version 1 and the original request as
causation. The controller independently read the result and checked the beacon,
writer, delivery state, attempt count and single linked response.

An earlier live trial failed: the watcher reconciled the completed attempt
before the agent journaled its reply. That reply remained pending. Fix `88c9599`
(integrated as `12bcb49`) retains a conversation's journals beyond reconciliation
and replays their exact commands on later watcher cycles. Regressions exercise
watcher, Stop, restart, legacy state and real API paths. The failed trial was
retained as evidence; the successful trial used a fresh session and request.
Subsequent review found the same ordering could affect legacy journals without
a kind field. Fix `995afe2` (integrated as `c39d809`) recovers the kind from the
matching context or conservatively retains an unknown-kind committed result.
All 19 recovery tests passed after integration, including the legacy ordering.

Codex 0.154.0 on Archon denied Unix socket creation in the default network-disabled
sandbox. The successful isolated fixture used a named profile extending
`:workspace` with network access enabled. The documented one-socket proxy rule
also failed the local probe. No default harness permissions were widened.
Agent-200 reviewed this test-only change in Cairn note
`ae29583f-8224-47d1-891f-4206fbd45d0e` version 1. This is a harness configuration
limit, not evidence that every default Codex sandbox can run the Cairn CLI.

Selected cross-model review references (Cairn note IDs, version 1):

| Scope | Reviewed revision | Reviewer | Record |
| --- | --- | --- | --- |
| Transport | `a6b1c92` | agent-200, Anthropic | `32644518-1ec0-49a1-bbd0-e3cda5176231` |
| Enrollment | `6f261ff` | agent-201, Codex | `c540ad42-a496-497b-9c29-adbb48152ba7` |
| Recovery through immutable completion/response checks | `d8e4127` | agent-203, Codex | `0836f4a6-767e-4525-8c6b-4857245e9893` |
| Reply lifetime and legacy journal fix | `88c9599`, `995afe2` | agent-203 finding; agent-112 fix review, Codex | `a5180c3e-42fb-4fcb-b0ac-32d716ae5e88`, `54ad8351-154e-48eb-8a8d-ea2e8c759cf2` |
| Root integration and deployment plan | `7c832c5` | agent-200, Anthropic | `9c8cddbc-0003-41c1-9bcf-c8c75f237856` |
| Harness final approval | `1efabd2`, integrated as `b46a0e1` | agent-204, Anthropic | `b9eebd2f-6f14-4f68-8cd5-3cbda680e4f5` |
| Legacy recovery confirmation and harness disposition | `995afe2`, `1efabd2` | agent-203, Codex | `5cd9c1b3-776f-4250-aadc-10b7414da671` |

These tests establish the implemented slice. They do not measure usefulness over
long-running work, general network availability, or unimplemented remote worker,
observer and cancellation features.

## Reproducing the automated checks

Run `make test`, `make check`, and `make test-integration` locally. The integration
runner creates disposable PostgreSQL databases; do not substitute a production
DSN. The release candidate is `b46a0e1`.

- [Enrollment integration test](../../cmd/cairn/machine_integration_test.go):
  actual provision/import, TLS handler, relay, shared collection, attribution,
  role limits and revocation.
- [Two-relay fixture](../../scripts/check_multi_machine.py): principal borrowing,
  private history/pull refusal, pre-send failure, lost post-commit replies,
  expired leases with retained holds, host journal replay, relay crash recovery,
  token rotation, whole-machine revocation and actual dump/restore fencing.
- [Native recovery store tests](../../core/agent_native_recovery_test.go): exact
  unreleased hold, cancellation and release refusal, operator release and its
  race with completion.
- [Host journal tests](../../scripts/test_inbox_recovery.py): immutable retries,
  committed-result matching, late response intent, restart and legacy journals.

The local two-relay test simulates machines with separate profiles, sockets and
host state. Its watcher case calls the real coordination module directly; the
Archon trial separately establishes real hooks, MCP and host process behavior.

## Deployment and production verification

Both hosts run the clean stamped binary
`b46a0e1bc7d9b767e32346cc9a2b42c20dc2fcc4`. Later acceptance-document commits do
not change that runtime. `make check` and `make test-integration` passed on the
integrated release. `make test` passed at `c39d809` (the same runtime code,
before later documentation and acceptance-fixture changes): 418 Python tests,
15 skipped, plus the Go unit suite. The PostgreSQL integration gate separately
ran database coverage and the full two-relay scenario.

Proximal's existing API and semantic worker flags were preserved, with a
loopback listener on port 18889 behind tailnet-only HTTPS port 8789. Tailscale
Funnel remains off. Schema 054 was applied after a checkpointed database backup
whose digest was verified. Existing profiles were preserved and the new
`machine:archon/agent` profile was provisioned. The API restarted once. The
local presence service and all seven wake services were active afterwards;
agent-112 retained its existing agent/execution identity and reported Proximal
attribution through the updated watcher.

Archon imported one production enrollment file after removing its trial
enrollment. Its `machine status` returned `build=compatible`, `service=active`
and `linger=yes`. The relay and presence units are enabled. Codex MCP and native
hooks are installed without widening default permissions. The coordinated
interactive launcher calls a stable wrapper around the mise shim, so it does
not pin one version's install path; a Bash alias routes `codex` to that launcher
because mise activation otherwise takes precedence. Noninteractive callers can
use `~/.local/bin/codex` explicitly.

The production smoke used a fresh isolated test harness configuration with the
same reviewed network permission exception as the disposable trial. It made
only selected memory, completion and response writes. It did not inject faults
or restore the production database.

| Production check | Observed identity |
| --- | --- |
| Archon agent | `53c5d185-8ff5-4b76-a3a2-f376f9e15ab2` (agent-206) |
| Native Codex session | `01a0e032-ef59-7d91-aed4-7a7dbcb7012a` |
| Proximal note fully pulled on Archon | `b5e3e085-a832-4c10-bd79-b9d010464af4`, version 1 |
| Request | `1d1c4d5b-32e8-4655-9be3-eeab99bb55b7` |
| Exact request source read through MCP history | `39f81b05-784b-41b4-98cc-f64ceb8e44a3`, version 1 |
| Handled delivery, one attempt | `5acabe9d-4b04-4b6b-96ee-1c038f8defe8` |
| Completion result read back by controller | `f1d92644-6725-4fda-a461-49ffa98e8a26`, version 1 |
| Single response with original request causation | `d5dd2149-1f70-49ec-a392-30649b8cedd0` |

The controller checked the stored result, writer, machine attribution, exact
source MCP call, delivery state, attempt count and response reference. The test
agent finished normally. Its temporary authentication link was removed.

The disposable API, HTTPS port 8788 and PostgreSQL cluster were stopped. Trial
enrollment secrets and the finished trial agents' authentication links were
removed. Selected evidence and rollback backups remain owner-only outside Git.
Failure injection and test cleanup touched only disposable state. Production
verification used only the explicitly selected note, request, completion and
response described above.
