# F02 bound Codex SessionStart recall consumer

This tranche implements the `/3` bound Codex `SessionStart` memory cell with
`recall_mode=agent_tools`. It establishes no installed or production acceptance.
The public `cairn lifecycle` command stays disabled.

The receipt-selected Python memory parent invokes the checked executable once
with `lifecycle memory recall`. The existing held binary descriptor, captured
event, parent config/binding digests, receipt and original enclosing deadline
are retained. The executable admits and validates the binding once, then uses
the checked config/event snapshots for the Go memory body. It does not invoke
the API CLI or fall back to Python rendering/store work after that dispatch.
The parent forwards the bounded private result's hook streams and exit code.
Private process failure retains F04's fixed failure protocol.

`memoryhook` consumes the authenticated SDK and `memoryclient` for search and
whole unfinished checkpoint refresh. Its strict Codex ledger reader preserves
unknown fields and arbitrary integer refresh counters. The caller holds the
existing nonblocking memory session flock through the grant and state writes.
The initialization marker and grant ledger use durable replace before output;
ordinary state retains the source temporary-write/rename policy. SessionStart
does not allocate a native turn or reserve a new optional allowance. Required
selection hashes, serialized hook charges and separate required/checkpoint
refresh counters are part of the consumed path.

The same SDK reports `cairn-lifecycle/recall-meter/1` after releasing the memory
lock. Reporting is best effort and respects the remaining enclosing deadline.
Search/pull counters and receipts come from the actual memory client; operation
timings observe initiated operations, including checkpoint refresh. There is
no inbox delivery at this SessionStart cell, and no inbox-recall meter is
fabricated. The executing private binary's SHA-256 is reported as its own
`lifecycle-memory` implementation identity. This diagnostic remains separate
from the server's authenticated principal and observation channel.

| Cell | Implementation in this tranche |
| --- | --- |
| Bound `/3`, Codex, SessionStart, agent_tools | Go body behind checked private process |
| `/2` and unbound memory | Retained Python |
| Codex UserPromptSubmit | [Prompt consumer under construction](f02-codex-prompt.md); outside this landed Start tranche |
| Codex tool events, Stop/capture | Retained Python; unported here |
| Claude memory and every other harness | Retained Python; unported here |
| Coordination/journal/watcher and installer/adapter | Outside author scope |

`scripts/test_f02_start.py` contains 28 process tests, with additional table
cases. Class A cases use producer-shaped configuration, native start events,
source grant writers, real files/flocks and a private Unix HTTP fixture. The
candidate enters through the actual receipt-selected public launcher. The
oracle runs the retained memory body and actual API CLI; its activation manifest
uses the retained `/2` schema and only the unused SessionStart bridge loader is
bypassed. F04 independently covers that prefix. Both use the same provisioned
fixture paths and synthetic owner-only token.

The corpus compares exact public stdout, stderr and exit codes, and persisted
state with scalar types and object order retained. Corrupt files are compared
as exact bytes. It covers empty/degraded recall, Unicode whole required context,
policy and optional refusals, whole-budget refusal, charged-grant refresh,
compact/resume whole checkpoint restoration, malformed/empty API replies,
search timeout, missing socket, lock contention, anti-reset/corrupt ledgers,
fsync failure before delivery, privacy, retrieval hints, executing identity,
recall meters and invocation trace. A Go → Python → Go process sequence retains
native reservation, emitted charges, large integers, Unicode and negative zero.
A separate Go test faults inside the body and asserts its fixed unexpected
exit-1 diagnostic; it adds no production fault switch.

Wall-clock timestamps and observed elapsed measurements differ between real
processes and are checked separately. The 27 cases using the real private command retain normal randomness and check
generated RPC UUIDs for canonical non-nil shape. An additional controlled-randomness
process case compares every complete RPC body, keeping request IDs unchanged,
including the derived whole-pull and meter IDs. That test uses a temporary Go
entrypoint which seeds only `uuid.SetRand` before the actual private dispatcher;
the Source controls only generated UUIDs, retaining its actual CLI/API/body. It
also asserts the temporary executable identity separately. No production entropy
knob is added. Deterministic request fields, receipt IDs and grants are compared
unchanged in every case. The fixed whole-pull UUID derivation and unchanged
paired arguments are separately covered by the existing checkpoint-refresh
component tests. Executing identities are asserted separately: the source
memory file SHA for Python and the actual private executable SHA for Go. Neither
identity is erased from comparison. These declarations do not authenticate the
store principal or observation channel.

The original 27 process tests, four affected Go packages and 48 F04
regression tests passed before B1 review. Foundation also reports the merged
27/48 batch passing; these results do not establish review acceptance. The B1
repair adds paired compact/resume coverage for an explicit filesystem-root
project. Python gives `/` an empty basename, so the adapter now passes the
existing source-compatible `baseName(project)` to checkpoint refresh. The
regression compares exact streams/state and checks restored context, refresh
status, separate counters and the serialized checkpoint charge. It retains the
ordinary nonroot case. Repaired-candidate exact results are recorded separately. The retained source corpus runs 494 tests across 27 modules;
493 execute successfully and one requires `OPENCODE_TEST_BINARY` to name a
patched native OpenCode build. That unavailable native probe remains a coverage
gap in an unported cell. The source corpus includes:

| Source fixture modules | Boundary covered here |
| --- | --- |
| `test_codex_turn_budget`, `test_startup_recall`, `test_codex_lifecycle`, `test_receipt_accounting` | Codex grants, startup and receipt preservation |
| `test_hook_error_diagnostics`, `test_recall_error_diagnostics`, `test_recall_failure_observation` | Fixed caught failure/refusal behavior |
| `test_recall_observation`, `test_inbox_recall_observation`, `test_hook_invocation_trace`, `test_selector_process_observation` | Meter and trace meanings; no selector is ported here |
| `test_native_session`, `test_grant_ledger`, `test_hook_admission`, `test_unix_peer_transport` | Shared identity, state and guard primitives |
| `test_inbox_recall_bridge`, `test_inbox_ledger_compaction`, `test_recall_task_grant`, `test_claude_inbox_recall`, `test_claude_channel` | Retained binding/bridge and other grant consumers |
| `test_codex_queue`, `test_hermes_queue`, `test_opencode_queue` | Retained queue behavior |
| `test_capture_concurrency`, `test_capture_observation`, `test_claude_lifecycle`, `test_hermes_lifecycle` | Retained capture and other harnesses |

Other installer/adapter, trial, controller and native-runtime fixtures do not
establish this selected-cell acceptance; their ownership/holds remain with
Release and the campaign. The source corpus is regression evidence, not a
claim to port those cells. The initial 16 Codex turn-budget tests ran before
construction; subsequent comparisons retain the literal Python body.

Focused builds and process probes run in the shared namespace wrapper, with
`CC=gcc`, a `cc` alias to gcc, `TZ=America/Los_Angeles`, `TMPDIR=/tmp` and
`GOFLAGS=-buildvcs=false`. Owner decision `40d0c42c/v1` now permits the standard
`make check`, `make test` and `make test-integration` gates on the host; the
wrapper restriction still applies to other probes. Production changes still
require a grant.
The repaired author commit `69d557c1` passes the four affected Go packages, all
28 F02 process tests and host `make check`, with a clean checkout before and
after each check. Foundation's combined `7bbf15cc` also passes all 28 F02 and
nine staged R04 hook tests. The staged fixture supplies the authenticated Unix
API reached by the new cell; every original emitted hook row, success assertion,
producer-input removal and state assertion remains. Before the root-path repair,
the exact merge passed all 48 F04 and 24 installed R02 contract tests.

Disposable integration passed all 31 Go packages and the Python fixtures before
protocol skew during author construction. The earlier namespace run stopped at
the linked-worktree Git clone. On `7bbf15cc`, host `make test-integration` selected
and passed the remaining 12 fixtures, including protocol skew's 26 combinations,
source-opening history, native cancellation and the three systemd process probes.
The worker-pool probe used its generic worker fixture; a native OpenCode binary
and optional semantic workers were not supplied. Foundation validates the final
merged tree with `make check` before landing. These results cover this selected
cell and its compatibility; they do not establish full F02, installed
qualification, production acceptance or measured usefulness.

During construction the corpus caught Unicode escaping, persisted field order,
pending-requirement persistence, API failure classification and the strict
ledger reader's memory-wrapper error class. Those corrections are included.
F04's temporary failure-image fixture now copies its required `localapi`
dependency; its activation-only strace test retains UserPromptSubmit coverage,
and the new F02 test checks the migrated SessionStart's one checked Go exec,
zero API CLI subprocesses and one current config read per parent/Go process.

Rollback remains Release's explicit drain/repin to retained immutable artifacts.
There is no deployment or activation in this tranche. Native C/CPython, frozen
branches, preserved failed F03, production/controller/runtime and subscription
holds remain intact. No new agent or provider is introduced. Class B receives
safe fixed refusals; Class C allocation/interpreter limits do not require a
CPython provider. Go's bounded process streams and enclosing deadline apply.
