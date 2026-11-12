# Release installation and acceptance contract

Revision: `release/4`, proposed maximum-document evidence amendment to the
accepted `release/3`. Review packets and Plane items identify the exact candidate
commit; root's shared index records accepted revisions. This is a specification
and source characterization, not implementation or deployment acceptance.
X-01 setup is closed under `shared/2`; each item's predecessor and review gates
still apply. The amendment below requires exact independent criteria review
and consumer agreement before its evidence disposition is consumed.

The authoritative dependency tables and migration corrections are in the
[implementation plan](../hybrid-runtime-implementation.md). They supersede
the architecture proposal's concurrent-model, instant-rollback and existing
vector-norm claims. The initial source baseline is
`d3c255a4bb75438e93527eaa1d83da780fd11eea`; inventory observations must record
their own current source and artifact identities.

Owner decision `98d46758-c61c-42b4-9912-76cf9dfb0dc0` (2026-11-10, version 1) replaces C2, FIRST and source-only test holds with isolation from production homes, production sockets, network and host processes, permits separate lifecycle and semantic release trains while retirement waits for both, and retains all other holds and the requirement for explicit grants for production changes.

## Ownership and interfaces

The release team owns installation, host adapters, independent acceptance,
documentation, deployment evidence and retirement. The overall lead owns
cross-team decisions, the shared contract index and landing queue. This file
has one release-team writer at a time. R-B2's independent baseline evidence is
separate from the installers it will evaluate; R-R1 reviews criteria before
implementation and exact candidates before landing.

| Interface | Provider | Consumers | Required boundary |
| --- | --- | --- | --- |
| Lifecycle command grammar, stdin/stdout/stderr, exit/refusal semantics | Foundation and Coordination | Installers and host adapters | Preserve the characterized full command surface, including already issued inbox commands; the proposal's abbreviated grammar is insufficient. |
| Binding admission, state files, ledgers and native sessions | Foundation | Coordination, installers, adapters | Preserve authenticated identity, compatible schemas, lock and persistence order; release does not implement a competing validator. |
| Journal, process identity, wake and custody | Coordination with Foundation primitives | Installers, adapters and installed acceptance | No replacement conversation, new credentials, alternate inbox claim or uncertain-action replay to refresh a client. |
| Worker binaries, model assets, derivation and rebuild runbook | Semantic | Release staging and deployment | Offline artifact identity, fixed executable launchers, one indexing generation across the shared database/job queue and tested reverse rebuild. |
| Install, pin, drain, restart and rollback | Release | Every team and supported client | Immutable selected code, explicit disabled staging, checked activation and observed live connections. |

Foundation supplies `/3` validation and any necessary shared state primitives;
release supplies installation and its independent black-box proof. The exact
`/3` descriptor and lifecycle command spelling must agree with Foundation's
reviewed contract before R-02 code starts. The overall lead's X-01 ownership
decision assigns `scripts/install-semantic.sh` to Semantic S-B2 for S-04
packaging. Semantic supplies the binary/model/native-library identity manifest,
architecture/ABI requirements and fixed rank/stream/embed launchers; release
supplies the host inventory, reviews installer compatibility and owns final
service configuration and staging activation. Release does not edit that
installer without transfer.

Semantic S-B2 also owns the first dedicated Cargo/build/actual-worker acceptance
Makefile targets. Root transfers Makefile ownership to release for R-05/final
suite wiring only after those targets land. Foundation F-B1 owns initial
lifecycle registration in `cmd/cairn/main.go` and `cmd/cairn/commands.go`;
Coordination uses separate implementation files and a callable dispatch seam.
Subsequent shared CLI/service edits require an explicit root slot.

## Current source facts and preservation boundary

These observations describe source, not current installations:

- `integrations/lifecycle/inbox_binding.py` declares
  `cairn.inbox-recall-binding/2`. It validates owner-only binding metadata,
  owner-controlled absolute configuration paths and hashes, complete pinned
  Python code, boolean activation and compatible memory/coordination profiles.
  Bound recall currently permits Codex and Claude, one to eight coordination
  configurations, distinct explicit homes when sharing memory, and integer
  context budgets from 1000 through 9500. Other harness support does not imply
  support for this binding.
- `scripts/install-claude-hooks.py` preserves selected existing memory
  configuration and custom state. It refuses replacement under an enabled
  recall binding. `scripts/install-agent-coordination.py` likewise preserves
  custom state and checks enabled bindings, including other accounts sharing
  the installed runtime. The tested legacy behavior does not establish that
  concurrent installers or a future `/3` transition are safe.
- `integrations/lifecycle/lifecycle_bundle.py` owns the legacy complete module
  inventory. [Installed lifecycle bundles](../../lifecycle-bundles.md) requires
  staged entrypoints to run without a checkout, execute validated bytes, keep
  bundle import caches isolated and refuse missing or changed helpers.
- `scripts/test_installed_lifecycle_bundle.py` runs installed Python entries in
  a network-isolated `bwrap` home without the checkout. It covers memory
  entries for Claude, Codex, OpenCode and Hermes and coordination entries for
  those harnesses plus Agy. Its Codex memory installer fixture replaces native
  hook trust setup; that fixture alone does not verify real Codex trust.
- The installed coordination surface includes the `inbox` family and the
  watcher's `--config-dir` option in
  `integrations/lifecycle/coordination.py`. Coordination owns its full grammar
  and issued-context compatibility fixtures; release must not invent an
  incompatible shorter recovery interface.
  Its `flush_journal` also has a specific `finish_reply` recovery exception:
  when a committed completion lacks the exact result reference and no response
  exists, it retries that same completion to recover the reference before
  creating the reply. Preserve this exception alongside ordinary pending-slot
  automatic replay; it does not permit replaying arbitrary settled/refused
  commands. `flush_inbox_intents(scope='attempt')` still flushes all
  conversation-owned journals; scope changes the settlement Boolean, not the
  replay set. Installed state/recovery acceptance follows Coordination's
  characterization of these cases and Foundation's session boundary.
  `NativeSession.settle(strict=False)` retains its prior diagnostic on a
  nonexception uncertain result, as covered by
  `integrations/lifecycle/test_native_session.py`; release must not accidentally
  strengthen this behavior when moving installation and adapters.
- `scripts/install-semantic.sh` currently uses `semantic/venv`.
  `semantic/embedding_command.go` supervises an executable command; service
  flags do not carry arbitrary launcher arguments. Semantic provides fixed
  mode/model launchers and the selected derivation. The record-keyed semantic
  storage does not retain complete Python and Rust generations concurrently.
  `semantic/index.go` seeds and leases `cairn.semantic_job` without a collection
  predicate and replaces documents per record. Writer exclusion therefore
  covers the entire shared database/job queue, including writers configured
  for other collections, not just the collection being released.

R-01 records the complete operation matrix from actual source and installed
observations. Each row names harness, account selector, operation/event,
configuration source, code/pin identity, supported or unsupported result,
source fixture, installed observation and live-client observation. Each live
session also records whether hooks are cached, the supported hook/MCP refresh
method and its actual scope, and evidence that the selected hook identity took
effect. A current MCP connection does not prove that cached hooks refreshed.
Inventory `/1`, two-file and `/2` selections separately; the existing
`scripts/check_effective_recall_deployment.py` recognizes historical `/1`.
R-01 also supplies the nonempty managed semantic-service architecture/ABI/CPU
inventory consumed by S-04. Separate positive managed targets from client-only
platform observations and inspection gaps; incomplete effective CPU/kernel or
loaded-library observations cannot become a shared default qualification class.
Record the new invalid-code preflight consequence separately: exit 1 has no
native-prompt veto, so reserved wake text may reach the model without required
context. The wake's report-and-stop instruction is the remaining guard on that
path. Root accepted this specification policy; it is not an observed installed
result or permission to claim native refusal. R-01 keeps that expected row and
its missing live observation explicit.
An unreviewed live legacy selection blocks its migration; frozen trials retain
their original executors and pins. Unknown or
unobserved entries remain explicit. Agy memory and each harness's idle wake,
cancellation and recovery are checked individually; neither a shared installer
nor a successful MCP connection proves the whole row.

The [dated prior deployment](../../verification/event-admission-deployment-2026-11-06.md)
names Proximal, Archon and Talos and documents account and repository pin drift,
live MCP refresh and running-image verification. It is discovery context, not
a current inventory or proof of the hybrid release. Re-inventory those hosts
and any additional installations discovered through existing configuration.
Unreachable or uninspected hosts are gaps, not evidence of absence.

## Temporary compatibility receipt and loader

The [F-04 consumer decision](f04-consumer-decision.md), committed in `32ca633`,
permits a new immutable Python compatibility caller as F-04's first consumer.
It replaces the activation prefix used by the real memory and coordination
frontends. This section specifies that caller's installation boundary;
Foundation owns the sole receipt validator and callable protocol. Release owns
immutable staging and activation. These are proposed interfaces until their
exact candidate receives Foundation agreement, independent delta review and
root's X-01 admission. They are not current installer capabilities.

### Receipt and independent selection

Use schema `cairn.lifecycle-compatibility/1`. The receipt is an ordinary UTF-8
JSON file; its independent pin is SHA-256 of its exact file bytes, including
whitespace. It has no self-digest field; duplicate JSON object keys refuse.
`source_revision` records provenance and grants no authority. The required
fields are:

| Field | Exact selection |
| --- | --- |
| `schema`, `source_revision` | The schema above and the source commit used to build this compatibility snapshot. |
| `snapshot_root` | Canonical absolute, persistent, content-addressed private directory containing the Python snapshot. |
| `members` | Map of relative member names to `{sha256, bytes}`; lowercase 64-hex SHA-256 of raw bytes and exact integer byte length. Includes bootstrap, loader, public aliases and every retained imported implementation helper. |
| `bootstrap`, `loader` | `lifecycle_compatibility.py` and the retained/adapted `lifecycle_bundle.py` in this new snapshot. They are checked payload code, distinct from the trusted launch verifier. |
| `roles` | Exactly `memory: memory.py`, `coordination: coordination.py`, `inbox_recall: inbox_recall.py`, `inbox_binding: inbox_binding.py`. The memory role also supplies the retained engine. |
| `aliases` | Map of each public alias basename to `memory.py` or `coordination.py`. Both are in `members`; alias bytes must equal that role's checked bytes. No arbitrary role or adjacent path may be loaded. |
| `binary` | Exact `/3` Go executable descriptor `{path, sha256}`. |
| `binding` | Exact versioned descriptor `{path, sha256, schema}` for the selected `cairn.inbox-recall-binding/3` file. |
| `memory_config`, `coordination_configs` | `{path, sha256}` descriptors, with the same ordered one-to-eight coordination membership as the selected `/3` binding. |

Member names are single basenames: no separators, traversal, duplicate canonical
names or symlinks. Alias names cannot shadow canonical members or the bootstrap.
Missing members, unknown schemas,
invalid field types, unknown or missing roles and mixed selections refuse.
The exact complete inventory for this receipt version contains all 17 members
of `lifecycle_bundle.py:MODULE_FILES`, the new bootstrap and the declared aliases;
the loader is already one of those 17 members. Reject extra or missing members.
Each member is at most 2 MiB, preserving the complete-bundle member bound;
the receipt itself is at most 1 MiB. Byte counts are exact integers, not Booleans.
An unlisted local import, an adjacent checkout or a process-global module cache
is not an execution source. A changed inventory requires a reviewed contract
version, not a manifest that silently broadens executable membership.
The final directory component of `snapshot_root` is the lowercase SHA-256 of
the member-name-to-SHA-256 map serialized as sorted-key, ASCII JSON with compact
`,`/`:` separators, matching the existing complete-bundle digest convention.
This directory identity does not hash the receipt that names the directory.

The owner-controlled, versioned host hook configuration independently fixes
the approved absolute interpreter and verified vector `V`: `-I -S -c`
followed by the reviewed stdlib-only verifier literal, the receipt absolute
path and expected SHA-256, separate `--role memory|coordination` and
`--entrypoint ALIAS`, the frontend config absolute path and trusted hook timeout
selection. Release records the verifier literal's SHA-256 with that launch selection
and verifies the installed argv, including the literal. No unchecked Python
file runs merely because it is beside the receipt. The interpreter, its
stdlib and the literal are the trusted launch anchor; the receipt authenticates
the subsequently loaded bootstrap and implementation. A receipt cannot
authenticate the verifier that is already interpreting it. Changes to the
owner-controlled launch anchor require the same controlled install, refresh
and readback as other hook configuration changes. Preserve its approved
interpreter/dependency closure in the declared offline rollback artifacts.
For a host's shell command field, render the complete vector with `shlex.join`;
readback must recover the identical vector with `shlex.split`. Do not add shell
expansion, substitutions or unquoted receipt/literal/config arguments. A host
that accepts an argv vector receives those same literal arguments directly.

The hash graph is acyclic: hook selection pins receipt bytes; receipt pins
Python members, binding, binary and configuration descriptors; `/3` pins the
same binary and configuration descriptors. Configuration files name the
versioned binding path, without a binding or receipt digest back-reference.
The memory entrypoint selects `memory_config.path`; the coordination entrypoint
selects exactly one `coordination_configs` path. An alias retains its canonical
role's selection; a different profile is not an interchangeable argument.
When admission reaches the binding, compare the declared path, digest and
coordination order across the receipt and `/3`; actual downstream configuration byte/hash and profile validation has
only the disabled-staging exception below. Matching only a binary hash is
insufficient. A change to the binding's enabled Boolean, descriptors or config
bytes publishes new immutable binding, receipt and selection artifacts.
Per-turn activation ledger decisions remain the compatible mutable state
surface; they are outside receipt hashes and do not publish a new selection.
No `/2` descriptor, frozen trial or cached legacy callback is edited in place.

This temporary Python `-I -S` launch (`V`/`L`) covers Codex/Claude memory and
coordination **hooks** and their issued inbox commands, including compact
operations below. The retained recovery and offline/operator inspection modes,
such as `inspect-held-refusal`, plus `watch --config-dir` under Python `V`/`L`,
remain on their explicit retained `/2` route until J-06/R-06 qualifies their
replacement. For the presence watcher, the direct Go `/3` entry
(`cairn-lifecycle coordination --receipt PATH --receipt-sha256 SHA --role
coordination --entrypoint ALIAS --config ABS --hook-timeout H --launch-root ROOT
--shell SHELL -- watch --config-dir DIR [--once]`) provides the receipt-checked
J-05/R-06 Go replacement over a dedicated copies directory `DIR` (enforcing
exact 1-to-1 membership between `DIR/*.json` and `receipt.coordination_configs`
with canonical generation-file verification, per-profile `--config`-substituted
mode `0500` launcher verification in `LaunchRoot`, no hook deadline, and
pre-lock plus per-cycle receipt/manifest/profile re-verification exiting `1`
with `CompatibilityPreflightError` and zero `.watch.lock` effect on initial
preflight failure; not an active production route before R-06 qualification).
Unsupported compatibility invocations are not silently redirected.
`codex_queue.py:_open` lazily imports `websocket` for those retained Python
modes; `-I -S` does not authorize an unchecked site-packages search. This hook
receipt does not claim their Python dependency closure or full native migration.

### Public launch and issued commands

Let `V` be Foundation's exact verified argv: approved Python, `-I -S -c`, the
trusted literal, `--receipt PATH --receipt-sha256 SHA`,
`--role memory|coordination`, `--entrypoint ALIAS` and `--config ABS`, plus the
trusted hook timeout selection. The alias maps to that role's canonical member
and the config is its corresponding receipt selection. The
command grammar is `V + ["--", *original_mode_and_args]`. The verifier/bootstrap
validates that tail and invokes the checked coordination role with the exact
original suffix and unchanged stdin. It does not accept another role/config
from the tail; a tail's `--config`, when present, must equal the selected path.
Existing command flags, context, versions, UUIDs, causation and authority checks
otherwise retain their source grammar and meaning. Hook entry uses the same
authenticated selection and its applicable original arguments.

Every newly issued Python inbox completion, ack, response, `finish-reply`,
advance/drain helper and cue reaches `V` plus its original command tail through
the short launcher `L` below. A fresh
process receives the checked snapshot/invocation context again; it cannot rely
on the hook process's module cache. `coordination.py:inbox_handoff` and
`inbox_cue_text` must use that checked launch vector instead of constructing
`[sys.executable, __file__, ...]`. The existing context `read` command is a direct
`cairn agent history` API CLI, not a Python entrypoint: preserve that direct
route and its source stdin. Wrap an actual Python read/helper with `V`; invent
no Python read verb. No shell evaluation, file-path reopening or implicit legacy
dispatch implements this handoff.

Root's `8f7357b`/`88b77474` closure also covers `inbox_context` compact, fallback
and next-item cues. New compatibility cues show
`L inbox compact read|inspect|settle|advance HANDLE` with existing disposition/
code arguments; machine execution reaches
`V + ["--", "inbox", "compact", OP, HANDLE, ...]` instead of the bare global
`cairn-inbox` launcher. Compact inspect
renews a lease; it is an issued-session operation, not the excluded offline
operator inspection. Before loading a handle-derived config or performing an
existing effect, bind the resolved root/config/context to `V`'s selected
configuration. `CAIRN_INBOX_ROOT` is a lookup input, not authority to select
another profile. Preserve TTL, owner/lease, causation, journal and native-turn
rules and the original command stdin/deadlines. Old `/2` bare launchers,
handles and issued argv stay untouched and usable.

### Short launcher and presentation bounds

Foundation's accepted `foundation/v4` at `8b768649` supplies the fixed renderer
and validation contract for `L`; Release stages, publishes, reads back and
retains it. For each exact `V`/configuration/timeout selection, `L` is a minimal approved
noninteractive shell script containing only `exec <shlex.join(V)> -- "$@"`.
It parses no event/handle, evaluates no tail string and selects no alternate
program or Python pathname. Original tail arguments, stdin and streams reach
`V` unchanged. The approved shell invocation must source neither `ENV` nor
`BASH_ENV`; installed fixtures use sentinel scripts to prove neither runs.
That qualification preserves admission's captured parent environment; it does
not sanitize/unset startup variables for the Go child.
An unqualified shell/target blocks publication, rather than assuming every
shell has identical startup behavior.

Generate `L` after finalizing receipt bytes and `V`. Stage it at canonical
`<persistent-private-launch-root>/<sha256-of-L-bytes>/inbox`, owned by the
invoking real UID, directory mode `0700`, launcher mode `0500`, with `/3`'s safe
ancestors/no-symlink policy. The Release selection/manifest records path, raw
byte count/SHA-256, vector/verifier digest, approved shell and Python closure,
and actual byte/argv readback. The receipt does not hash `L`, which embeds the
receipt digest. The shell, `L` and owner hook selection are an explicit external
trust anchor; `L` does not authenticate its own first instruction. Changes use
controlled install/readback and a new immutable identity. Keep every referenced
launcher with its receipt/argv until qualified retirement.

All newly issued Python-wrapper command fields and their cues, fallbacks, next
cues and turn-end reminders use short `L` plus the original tail, never the
multi-KB verifier literal. Direct context `read` through `cairn agent history`
and inner store CLI argv remain unchanged. This changes presentation, not
`V + ["--", *tail]` execution or command
grammar. Each model-visible shell command is at most **512 UTF-8 bytes**;
aggregate command/control prose in a cue/reminder is at most **2048 bytes**.
The actual encoded whole response/recall must also fit every smaller source,
configuration and harness budget, including Codex's 12000-byte
`additionalContextLimit`. Refuse installation choices that cannot meet them.
Never truncate required control/source text, silently fall back or report
delivery/settlement without the required bytes; retain source refusal/non-
delivery and pending evidence.

F-04/R-02 fixtures execute the **exact emitted shell-joined text** through the
approved noninteractive shell, not just programmatic argv. Include spaces,
quotes, non-ASCII paths and long legal arguments; assert byte-exact `V`/tail,
stdin and source effect for every complete/ack/respond/finish-reply/advance/
drain wrapper and compact read/inspect/settle/advance, including lease renewal.
Cover source-unavailable/fallback, next-cue and turn-end reminders alongside
maximum legal recall and encoded framing. Check wrong root/config/context
before derived load/effects, helper/alias/selection tamper and direct-member
refusal, and an existing `/2` bare `cairn-inbox` compact-handle success.
Verify launcher hashes/bytes/argv before publication and the no-startup-script
sentinels; argv-only smoke does not establish these presentation claims.

Retained Python API calls through `config['cairn']` keep their existing `/2`
provisioned path/owner trust level. A config pin does not hash-authenticate that
API CLI. The `/3` binary/running-image policy applies to the selected Go
lifecycle executable; Release inventories/verifies the retained API-client
selection through existing installed checks. Store authority/attribution stays
store-owned.

The new staged compatibility members and aliases refuse direct-path execution
before loading any adjacent loader, manifest or local implementation module.
Foundation supplies this entry guard and the checked context; Release installs
those exact bytes. This rule leaves the separate immutable `/2` copies and
already-issued `/2` commands executable. Root's F-B3 slot includes
`inbox_handoff` wrapper construction, `inbox_cue_text` drain rendering,
`inbox_context` compact/fallback/next-cue rendering and the minimum
checked-selection plumbing at `compact_inbox_command` config resolution.
Sequence that slot with Coordination before code. It transfers no grammar,
claim, journal, native-session or watcher transition policy.

### Checked execution, admission and disabled staging

The trusted verifier reads the selected receipt once, checks its independent
digest and validates owner, mode, symlink, regular-file and stable-descriptor
properties. It reads and validates the complete executing Python closure
before executing any member. Use Foundation's bounded checked-source
implementation: execute the exact captured bytes, including bootstrap and
loader, with a snapshot-local module cache. Never hash a member and reopen its
pathname for execution. The Go executable continues to satisfy Foundation's
real-UID, safe-path, digest and running-image device/inode rules; the receipt
does not replace or weaken `/3` binary validation. Before spawning Go, the
trusted caller checks the receipt-selected Go code and launches the verified
executable **file descriptor**, never a checked-then-reopened pathname.

Code trust and binding-data admission have distinct order. Once the trusted
verifier has entered, a missing/unsafe/tampered receipt, Python executing
closure or Go code is a declared new compatibility-preflight failure, even for
an excluded callback. It emits empty stdout, exit 1 and exactly one stderr line
by role:

- Memory: `Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError` plus newline.
- Coordination: `Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError` plus newline.

This path performs no token/API, ledger or activation-refusal-record effect and
does not promote the error to required-context/native-prompt refusal. It adds
no arbitrary exception text. `/2` behavior is unchanged; source equivalence is
not claimed for invalid new executing artifacts. The interpreter/stdlib/literal
remain the external approved trust anchor. Code preflight does not replace the
frontends' existing configuration/event parsing and their source error order.

Go then runs the source `inbox_recall` admission prefix exactly once with the
parent's captured environment, byte-equivalent values, cwd, checked config and
original event. Preserve all disable/child/account-home/event/privacy guards,
reserved-wake recognition, UUID/owner predicates and their source order.
Do not sanitize a subset, change `HOME`/`CODEX_HOME`/`CLAUDE_CONFIG_DIR`, rewrite
guard variables or inject `CAIRN_LIFECYCLE_CHILD`. An excluded prefix returns
its source skip/false result without opening/hash-checking the `/3` binding or
target config pins and without activation mutation. After admission reaches
the binding, check its receipt/`--binding-sha256` bytes and descriptor selections
and complete Foundation's pinned/running Go-image checks before state effects.
Issued inbox modes have no hook-admission bypass: authenticate their selected
code, binding and config context before their existing command authority checks
and effects. They do not repeat the hook's activation operation.
The prefix's skip/false result still returns to the retained frontend's
ordinary policy; it is not a whole-hook skip.

The explicit compatibility loader supplies the checked bundle and invocation
context to every retained role. In that context, `InboxBinding.load_bridge`,
`validate`, `coordinates_wake` and the bridge's `binding` call select the
checked retained bridge, engine and profile snapshots. They do not enter the
legacy `/2`-only descriptor path. The engine and bridge share that invocation's
resolved binding and cannot reread a different pathname or execute activation
again. This is a new explicit loader route; it never synthesizes a `/2`
descriptor to authorize Go. Existing immutable `/2` entrypoints keep their
original complete Python loader and execution behavior.
Other unresolved/operator Python activation callers remain `/2`-only: they
must refuse `/3` without this authenticated invocation and must not mutate a
Python activation ledger as an alternate route. A caller-supplied Boolean or
environment flag cannot manufacture that checked context.

Always authenticate the selected launch anchor, receipt, executing caller
closure and Go code. Binding data is checked when source admission reaches it,
not before an excluded event's guards. Preserve the source's disabled-staging
exception for the final downstream pins: a false effective activation decision
does not open or hash-check final engine/coordinator configuration pins being
replaced. The current frontend still reads its own owner-controlled configuration
once; the actual digest is checked at the child even while its final descriptor
hash is being staged and not yet valid. A reserved wake may read the source-required owner-controlled
configuration that locates the existing activation ledger without requiring
its final pin. Do not eagerly perform full profile validation on that path.
The full final configuration/hash/profile checks apply when the effective
decision is true, including a retained per-turn true decision under a now-false
manifest Boolean. An `enabled:false` flag alone is neither a drain proof nor
permission to execute unauthenticated compatibility code.

### Parent and activation child handoff

The proposed private child remains `cairn lifecycle binding activation
--entrypoint memory|coordination --config /absolute/config.json`; add
`--config-sha256` with SHA-256 of the parent's original checked configuration
bytes, `--binding-sha256` from the checked receipt, and the same `--receipt`
path plus `--receipt-sha256` as the checked launch selection. Go validates that
receipt metadata against its independent pin, uses its exact role/config/binary
descriptors and checks their agreement when admission reaches the binding,
before activation effects. Reading the receipt is code-selection trust, not an
early binding-data read. A changed/mixed receipt at this handoff reports the
fixed private `CompatibilityPreflightError` class with reason
`compatibility_receipt`; the parent renders the declared no-effect preflight
result, not an activation/native-prompt error. The child validates its
config digest before effects, and checks the binding digest when admission
reaches the binding, against the exact bytes consumed before activation
mutation. It must either use those same checked snapshot bytes
or refuse a changed path read. These digests prove consistency, not authority
to choose another profile. Retain the parent's parsed snapshot for the complete
enabled continuation; later validation or handler loading cannot silently
consume a newer configuration. Preserve the original bounded hook-event bytes
on stdin unchanged; serializing the parsed event again is insufficient.

Go owns the one enabled snapshot and activation decision. The parent has not
acquired its ordinary memory/coordination session lock; Go acquires/releases
the existing activation lock and preserves marker-first persistence. It does
not allocate/refund grants, claim a turn, call the store or flush a journal.
The parent removes the opt-in from its private effective config only for an
actual false result, then continues the retained public handler. Child launch,
protocol, configuration-race or persistence errors cannot become false.

Success is exit 0, empty stderr and exactly one compact JSON line,
`{"enabled":true}` or `{"enabled":false}`, including its terminating newline.
Private failure is exit 1, empty stdout and at most 4096 UTF-8 bytes on stderr:
one `{"error":{"class":CLASS,"reason":SOURCE_SITE,"errno":NUMBER_OR_NULL}}`
line. Foundation fixes the source-class/site vocabulary and maps it back to
the parent's existing exception category; no raw path, event or exception text
is added to this protocol. Preserve distinct missing-file, permission, lock,
JSON, value, key and type errors, and the source's uncaught-error boundary.
Malformed/extra/oversized output, launch failure and unexpected child exit are
failures. The parent alone renders its characterized public streams/exits,
including existing required-context and native-prompt refusal behavior. No
child frame is echoed and no error launches a Python activation fallback.
An uncertain Go write remains uncertain under the existing recovery rules.

For failures without a valid source frame, preserve actual spawn `OSError`
subtype/errno; convert enclosing timer expiry to `TimeoutError`/`ETIMEDOUT`,
not `subprocess.TimeoutExpired`. Signal death, panic/status 2, unexpected exit,
empty/malformed/extra/oversized/unknown frame, invalid UTF-8, invalid success
shape, stdout with failure or stderr with success become `SubprocessError`.
Read both bounded pipes concurrently; stop/reap only this invocation's child.
Never forward its status 2 or retry an uncertain activation. Parent interruption
keeps its existing host signal/exit semantics while cleaning up that child.

OS/timeout failures use the existing activation exception paths, including the
proven-required memory distinction, Codex `SessionStart` stop JSON and the
coordination reserved-wake refusal/recording path. Transport/protocol
`SubprocessError` instead has empty stdout and exit 1: memory emits exactly
`Cairn lifecycle diagnostic: phase=activation error_class=UnexpectedError` plus
newline; coordination emits exactly `Cairn coordination: SubprocessError; session presence unavailable`
plus newline. These transport errors have no required/native-prompt promotion
or refusal-record write. The memory catch surrounds only this IPC call;
unrelated retained-handler errors keep their source boundary. Foundation's
reviewed frontend/event failure table fixes all ordinary/source-frame outcomes;
fixtures cover every row, wake/non-wake, optional/proven-required, both
`SessionStart` variants and interruption. No private child output is echoed.

Use one absolute enclosing deadline: capture monotonic `t0` at the earliest
trusted verifier entry, before receipt/member validation. The trusted installed
hook row supplies `H`, never event input. Release places that exact timeout
element in `V` and reads it back; a hook process does not read its host's row.
Different 13/150 or 2/15 selections require distinct exact `V`/`L` even for one
configuration. Set `D = t0 + H`, or an earlier native
deadline when supplied. The child gets exactly `max(0, D - now)`, including
spawn, both Go image hashes, pipe I/O, normal full 1.5-second ledger wait and
persistence. There is no fresh phase budget or shorter arbitrary sublimit;
only enclosing expiry interrupts the source lock-wait policy. Retained handler
work uses the remaining same budget and preserves tighter existing API limits.
Missing/invalid trusted timeout selection refuses before child launch. Use the
actual R-01 installed row: memory recall 13 seconds and listed capture 150;
Claude/Codex coordination 2 for `SessionEnd`/`Interrupt`/`PostToolUse`, otherwise
its listed 15-second events. Do not invent support or enlarge host deadlines.
Measure verifier/hash/startup/activation overhead against those exact budgets.
This is the hook deadline only. V-launched issued inbox/compact commands keep
their existing command and intent-lock deadlines, need no hook-timeout row and
perform no activation. Do not impose the hook timer on them.

The child decodes raw event/config data with Foundation's lossless isolated-
interpreter source profile, not Go's default JSON coercions. Preserve case-
sensitive keys, duplicate-last values/first positions, lone surrogates, exact
integer/nonfinite/depth/encoding rules and per-field Python type/failure order.
A surrogate identity/privacy path must fail at its original boundary before
an activation write when the source does. Python `-I` ignores interpreter env
overrides such as `PYTHONINTMAXSTRDIGITS`; characterize that actual reference
profile. Merely forwarding original bytes does not establish decoding parity.

### Required acceptance and staging order

F-04 stages its real admitted compatibility caller in disposable homes before
R-02's production installer exists. Execute both complete public frontends as
real processes, enabled and disabled. Prove exactly one Go activation,
no duplicate Python activation mutation and real retained-handler continuation;
an activation-only command or successful no-op wrapper is insufficient.
Run newly issued completion/ack/response commands as fresh real processes after
hook activation, preserving context/causation/stdin and checked continuation.
Run compact read/inspect/settle/advance and the resulting next-item cue through
the same checked launch. A mismatched resolved config/root/context must refuse
before effects; keep a legacy `/2` handle/launcher success case. Cover inspect's
lease renewal without reclassifying it as effect-free inspection. These are
F-04 source-staged and R-02 actual-installer process fixtures.
Include a tampered helper/alias and direct-member execution negative so an issued
command cannot bypass the independent launch. Include config replacement between
parent and child, changes before downstream
continuation, missing/tampered bootstrap/loader/helper/alias, mixed receipt and
binding/config/binary, false staging with unfinished final pins, retained true
activation under a false manifest, and unchanged legacy coexistence. Compare
public stdout, stderr, exits, state and ordering with the characterized source.
The child must refuse uncertainty without a second activation attempt. Cover
admission environment/account/privacy/UUID rows and binding-read order: with
valid code, an excluded event must skip even if the unopened binding is invalid;
with invalid executed code, observe the exact new preflight result and no effects.
Include the normal 1.5-second contention, every failure/deadline table row and
source-lossless event cases. Cached enabled A must not count as disabled merely
because immutable disabled B was published; supported refresh/readback is the
required precondition below, not an invented quiesce API.

R-02 stages the exact same reviewed receipt/loader contract through the real
installer, offline without a checkout, preserving unrelated selections. Its
durability map includes syncing the complete immutable closure and receipt,
publishing their versioned paths, rendering `L` after receipt/`V`, syncing and
publishing its immutable bytes/manifest and verifying byte/argv readback before
publishing a disabled owner-controlled launch
selection, supported refresh/readback, then final validated activation under
the applicable barrier. Receipt publication alone is not atomic hook activation.
Inject interruption/concurrent-install faults at each actual durable boundary.
Complete watcher/config-dir/shared-user cutover and reverse selection remain
R-06/R-07 work; this agreement creates no new production mixed-executor unit.
Retain compatibility selections, interpreter and still-referenced code with
the existing issued-argv and rollback closure until retirement is qualified.
F-04 acceptance does not close HC-03's remaining grants, HC-04 native policy,
HC-05 installed coverage, X-01 or the full runtime migration.

## Required install and transition behavior

The cases below are requirements for new code. They have not passed merely
because a corresponding legacy test exists.

1. Stage each lifecycle executable in an owner-controlled immutable,
   hash-addressed location. The receipt identifies source/build and executable
   SHA-256. Its binary, binding and configuration descriptors select the same
   bytes used at execution. Validate absolute paths, ownership, write modes,
   hashes and supported schemas before effects. The proposal specifies an
   executable mode of 0755 within a private 0700 staging directory; existing
   unrelated owners and modes must survive installation.
2. `/2` pins authenticate only the complete retained Python bundle. They
   never authenticate a Go executable. Continue through the selected legacy
   executor or require explicit drain and `/3` repin. Keep the previous
   immutable release for explicit rollback; no automatic pruning occurs.
   Use versioned paths for `/3` bindings and their configuration snapshots;
   never rewrite an old `/2` binding into `/3` under cached legacy callbacks.
   An old entry given a `/3` descriptor, or a new binary presented only a `/2`
   descriptor, fails closed before effects with the characterized streams and
   exit status. Deliberate `/2` routing invokes the preserved legacy executor,
   rather than treating its pin as binary authentication.
3. Disabled staging permits preparing files without enabling callbacks.
   Activation follows successful contract validation and verified drain.
   An enabled binding, unsettled callback/attempt, conflicting installer,
   incompatible state or changed selected code must prevent unsafe replacement.
   Do not treat changing an `enabled` flag alone as proof of drain.
   For the Agy 1.5.6 client pinned by the native fixture, account
   `$HOME/.gemini/config/hooks.json` and project `<workspace>/.agents/hooks.json`
   entries are cumulative: the same `cairn-coordination` group in both layers
   executes twice and delivers context twice, including identical
   commands. An installer must select one active layer for each Cairn hook and
   detect a conflicting copy in the other layer before writing; it must not
   leave both copies active. The current installer does not implement this
   check: for Agy hook configuration, it reads and writes only the given
   `--settings` file and does not inspect the other layer. R-06 must qualify the
   completed check and the resulting selection across cutover and rollback. The
   [native Agy fixture](../../developer/native-agy-hook-fixture.md) observes the
   duplicate dispatch and removal behavior; it does not qualify installer
   conflict detection or a complete transition.
4. Preserve unrelated configuration, account and collection bindings,
   credential selectors, custom state paths and state bytes. Do not provision
   credentials or inspect their contents. Test preservation with synthetic
   configuration and credentials in disposable homes.
5. Complete or safely fail atomic staging, pinning and activation under
   concurrent install and interruption. Inject failures before and after
   each durable boundary; the result is a valid old selection or an explicit
   disabled, recoverable selection, with no partial active configuration.
   Failed or uncertain external effects are reconciled rather than replayed.
6. Drain old hooks and native attempts before switching their executor. Refresh
   watchers and clients through supported mechanisms. Verify actual running
   images, account and repository-local pins, active MCP/channel connections
   and conversation continuity. A staged binary or version string alone is
   insufficient. Unsupported refresh remains an explicit release blocker for
   an installed client; do not replace its conversation to bypass it.
   Process parentage alone does not identify a thread's active MCP connection.
   Check connection identity separately from retained process accounting, and
   verify the native refresh method's actual scope before using it. An
   app-server-wide reload is not a thread-specific force-reconnect operation;
   an accepted reload does not prove replacement or reaping.
   Keep every interpreter, entrypoint, configuration snapshot and context path
   named by a live hook or issued completion/reply argv executable and compatible
   until all its unsettled references finish and its session refresh is proved.
   Existing `coordination.py` embeds `sys.executable` and its own absolute path
   in issued argv. No reference may be silently redirected to an incompatible
   descriptor. An explicitly unsupported cross-version invocation has a tested
   fail-closed diagnostic; it is not a substitute for preserving supported
   unchanged explicit retries. Missing refresh/reference evidence blocks removal.
7. Reverse lifecycle rollout through the preserved immutable `/2` release only
   after the same drain, pin validation and supported client refresh. Compatible
   persistent state remains readable and writable by the selected executor;
   any discovered incompatible state requires an independently reviewed
   transition before dependent implementation.

## Activation units, drain and coexistence

The production transition unit is a host's presence watcher plus **every**
binding served by its selected `--config-dir`, all associated memory bindings,
callback entrypoints and account/repository/session selections. Any shared
runtime root, ledger or native-session state expands the unit to include its
other users. One release owner controls it. `/2` and `/3` may coexist across
independent units; this contract does not permit two production watchers or
executors concurrently owning the same unit (because `<DIR>/.watch.lock` is
scoped to `--config-dir`, R-06 unit cutover must stop the retained `/2`
watcher or reuse the unit's single active bindings copies directory under
service ownership so `/2` and `/3` watchers never concurrently serve bindings
sharing a `state_dir`). A mixed unit stays on its retained
legacy selection until every member can transition safely. No Go watcher is
assumed to serve `/2` sessions without an explicit separately tested contract.

R-02 proves immutable staging, pins and activation controls using the implemented
F-01/F-04 admission/state operations. It cannot certify Go recall/capture or a Go
watcher that its dependencies do not yet provide. Complete watcher drain,
switch, restart and rollback qualification belongs to **R-06**, after R-03,
R-04, R-05 and S-05; its J-06 dependency includes J-05 and F-02/F-03. R-07 repeats
that qualified procedure on actual units. This allocation preserves the plan's
dependency edges and HC-05 gate; root must record it in X-01's reconciliation.

A compatibility receipt pins the binding's enabled bytes. It therefore cannot
support the legacy binding's in-place disable: editing that file invalidates
the pin, and a cached hook still names its old immutable enabled selection.
Publish a new immutable disabled selection and use the host's supported refresh
to prove that **every** affected cached session now executes it before D1/D6
can be established. R-02 must refuse activation for a unit with unknown or
unsupported refresh; staging files alone is permitted. R-06 qualifies the actual
per-session refresh/readback with the full unit, before R-07 production use.
A fixture that still executes cached selection A after disabled B is published
must block transition; after a supported refresh to B it must demonstrate B's
identity and source disabled behavior. Unknown sessions or an unobserved refresh
remain blockers. Retained true activation decisions still require their ordinary
owning-session settlement; refreshing a disabled selection alone proves neither
drain nor a barrier against independent coordination claims or wakes. The other
drain predicates and the D3/D6 clarification gates remain required.

A drain receipt names the unit, affected session/agent/execution IDs, source and
pin identities, start/end observations and the held quiescence barrier. Every
predicate below must be true, with no unknowns. Historical terminal fields need
not be deleted; inspect their correspondence and status, not key absence.

| Predicate and source | Required negative fixture / safe outcome |
| --- | --- |
| Disabled binding and no effective positive activation grant for an owning turn; inspect `inbox_binding.py` and `grant_ledger.py` decisions, not just the manifest Boolean. | REL-DRAIN-D1: disabled manifest with a retained positive ledger grant still blocks transition. |
| Every native-session intent/attempt is terminal with matching authoritative close, completion/ack and requested reply facts; `native_session.py:ATTEMPT_KEYS` defines the retained fields. No active cancellation/custody hold remains. | REL-DRAIN-D2: retained active intent, attempt or mismatched close blocks; a correctly terminal retained record is not falsely classified as active. Stale process identity alone does not prove settlement. |
| Owning sessions/watcher have completed their authorized journal settlement with the equivalent of `flush_inbox_intents(scope='all')`; no pending/running slot or live unresolved reply obligation remains. `reply_possible` is checked separately because the existing settled Boolean excludes it. | REL-DRAIN-D3: pending slot, lock owner, missing exact completion reference or unresolved `reply_possible` blocks. Recover the narrow `finish_reply` case through its existing owner; never replay arbitrary settled/refused or held/failed work. |
| No active wake attempt and no live delivery lease/hold for affected sessions. Sources are authenticated `agent wake-attempts` with `active:true`, and the existing store-owned `cairn.agent_delivery`, `cairn.agent_wake_attempt` and session-inbox state, using reviewed read-only status inspection. | REL-DRAIN-D4: an active wake, nonterminal hold or live lease blocks even when no hook process exists. Missing visibility is unknown, never zero. |
| Watcher and wake launchers for the unit are stopped/joined; no old lifecycle callback is running. Ordinary capture already admitted finishes against the retained immutable release. | REL-DRAIN-D5: a running callback, delayed child or restarting unit blocks; do not kill a conversation or fake retirement to make the predicate pass. |
| A demonstrated quiescence barrier prevents new callback admissions, inbox claims and wakes throughout the check/stage/refresh/activate interval. It covers cached hooks, direct issued entrypoints and every unit member, not merely competing installers. | REL-DRAIN-D6: inject an admission/claim/wake between inspection and activation; it is prevented or invalidates the receipt and activation refuses. A quiet sleep or a new lock ignored by legacy code is insufficient. |
| Selected configuration/pins and effective session hooks are read back under that barrier; rollback or failure leaves a complete old selection or disabled recoverable unit. | REL-DRAIN-D7: interrupt each durable boundary and race a configuration/reference change. No partial selection becomes active. |

Journal flushing sends commands and is **not** a read-only inspection method.
Sessions finish their own supplied contexts; the watcher uses only its existing
authorized recovery path. The release inspector observes settled facts and
never completes another agent's delivery or claims its inbox. Foundation and
Coordination must provide a side-effect-free fact seam for any missing
observable before dependent acceptance. Fixtures map status inspection to the
actual schema and access checks; this specification invents no new CLI API.
The orchestrating invocation must not itself retain an affected unresolved
native attempt. Arrange a supported safe boundary/control invocation without
re-registration, a replacement conversation or an exemption from the predicate.

Compatibility qualification also runs real `/2` Python and `/3` Go processes
against the same fixture lock paths, journal, native-session record and ledger.
Exercise contention, timeout and crash at every persistence boundary; after
each handoff the other executor reads the surviving state, including pending
and `reply_possible` journals. Verify one mutation owner, preserved lock order
and exact retry/reply references. This is a disposable compatibility test,
not permission for an unqualified mixed production unit.

Before R-02 implementation review, publish the install lock's path, unit scope,
wait/refusal behavior and ordered durability map. The map includes staging and
syncing immutable bytes/receipt, publishing the immutable path and directory,
writing/syncing versioned configuration and pins, disabled selection publication,
supported refresh/readback, final activation publication and its parent sync,
and recovery-receipt persistence. Inject faults on both sides of every actual
durable boundary. Only the final validated selection becomes active; a
multi-file copy is not an atomic activation protocol.

## Maximum-document semantic evidence

Release consumes the "Conditional maximum-document reference" section of the
[Semantic worker contract](semantic.md), proposed `semantic/4`, as the normative
ordered sequence in steps 1–6, selected by Overall decision
`0ba29009-f422-4a11-a94b-018f1d3139b0` version 1. It applies only to the sealed
`"!" * 65536` case reviewed in S-R1 verdict
`378ded83-462a-47f1-82d8-f5243bef82db` version 1, seal SHA-256
`ef04e09d5bb7d906740da0f6406b14b4de3d14a6dfe664d313ce8ebb87815a4d`.
That verdict verifies the artifact inventory and leaves numerical qualification
open. The old external 90-second Python timeout is direct-supervisor
characterization with an unrecorded full environment; actual Go/production
timeout behavior was not observed.
The single at-most-180-second oracle attempt is consumed. Retain both immutable
observations and their limitations separately.

Steps 1–3 govern W's declared derivation and proof in the relevant namespace,
the pinned 294-text recipe and digest table, token-ID/length checks before
model execution, raw CLS match, and the two same-execution A/B references.
Step 4 governs the corpus-bounded and stateless mapping disposition, its
contradiction rule and the prohibition on reconstructed historical evidence.
No unapproved wrapper change or write under sealed evidence is permitted.
The ceilings remain exactly two additional B1 A/B passage executions and one B2 document
request. Every attempt is recorded. Any failed, uncertain or interrupted
attempt or failed pre-inference proof ends that experiment with its gap retained.
Any further execution, including a re-attempt after a fix, needs a new Overall
decision.

Overall construction amendment `b8bde736-20e4-4957-88f3-2acc7109e4fb` version 1
permits only the existing RW bind's host-source operand to change to a fresh,
empty, owned `0700` scratch directory outside the sealed root. The `--bind`
flag, guest `/sandbox` destination and all other prefix arguments stay fixed,
apart from the previously permitted declared read-only tool/input additions.
This is a declared derivation of W, never byte-identical reproduction, and
grants no extra attempt or budget reset. Release's receipt binds original and
derived argv plus their exact diff; resolved scratch path, owner, mode and
emptiness; rejection of symlink substitution and overlap/alias with sealed or
protected live trees; and before/after seal and ownership/mode checks. Preserve
all old files/directories, including unsealed delivery material, mount needed
old evidence read-only, and put every new log/result/receipt outside that root.
Retain cleanup evidence: remove only the newly owned scratch after the worker
and descendants are joined. Run all step 1–3 proofs against this construction;
the host-path change alone proves no numerical equivalence.

Step 5's one real-Go Python request is specifically operation `document`, text
`"!" * 65536` (65536 UTF-8 bytes). All steps 1–3 checks must pass, including
the 294 match, both normalized row hashes and actual worker maps, and the exact
S-01b isolation/recording harness candidate must be independently accepted.
Prove network isolation and mapped OS-home/cache protection, including
`getpwuid` and `expanduser` with HOME absent, in the Go-spawned child's namespace
before the request. The A/B proof does not transfer. Go's unchanged
`CommandEmbedder`, actual frame, sanitized environment and 90-second timer
remain required; record outer isolation overhead separately without extending
that timer. Normal startup/identity exchange permits no extra document/model
probe or repeated request.

Release's installed-runtime receipt names the currently selected installed
`worker-embeddings` launcher by absolute path and SHA-256 before the run,
cross-checked against R-01's installed row. This is never S-04R's staged
telemetry-disabled launcher. Run installed launcher, venv and model bytes
read-only with before/after digests and no service-selection change. Bind the
Go binary/source identity, interpreter digest, launcher argv/digest and actual
worker ORT/NumPy/OpenBLAS image hashes sampled externally from
`/proc/<pid>/maps`; do not inject a probe into the unchanged Python child.
Record the exact Go frame digest/UUID/decoded operation and text, and sanitized
environment names with only non-secret values. Preserve outcome, cleanup and
cancellation evidence.

The concrete O-4 through O-6 differences from `378ded83` version 1 remain
explicit: oracle Python serialization differs from Go's compact frame;
the oracle omitted `CAIRN_SEMANTIC_STREAM_CACHE=1` and added HOME, four XDG
variables, TMPDIR, FASTEMBED_CACHE_PATH, PYTHONDONTWRITEBYTECODE and PWD;
the oracle used direct remapped `python -B` instead of the fixed launcher.
Each input-specific source argument needs file:line proof in fixture provenance
and independent review, using the recorded Go frame/environment as comparison
inputs. These are case-specific arguments, not literal compliance or general
equivalence. The old 90-second run's missing full environment is a separate
limitation.

Seal each B1 reference set and the B2 record, whether timeout or reply, with
digest manifests. REL-EQUIVALENCE and REL-MANIFEST cite them alongside the
original oracle seal and any population disposition. A timeout is bounded
isolated real-Go evidence; a successful reply is a separate immutable artifact.
Compare a reply with the sealed oracle and escalate differences to Overall,
preserving both references. R-01, R-05 and REL-PERF keep direct-supervisor,
oracle and isolated real-Go timings separate; none is production latency.
Experiment outputs, including a B2 reply, are reference artifacts and never
index coverage or proof that the Python source is covered.
Until the authorized real-Go Python run returns a valid reply, a Rust reply
within 90 seconds counts as a population difference, including when Python is
unmeasured, blocked or failed. Overall owns its recorded disposition with
Semantic and Release review.

Before counting numerical coverage, obtain exact S-R1 and R-R1 acceptance of
the Semantic amendment and exact R-R1 acceptance of the Release delta. Send
accepted revisions, consumer agreement and proposed shared-index wording to
Overall. Actual experiment evidence receives separate review. All 256 Rust
vectors and spans, bit-identical raw-output gates, pairwise `<=1e-6`, exact
integer scores and order, and execution through unchanged real-Go 90-second supervision remain
mandatory. No new 180-second oracle, deadline inflation, production action,
source-oracle repair or backend/tolerance/role change is authorized by this route.
Criteria acceptance passes no coverage, runtime, R-05/R-06 or deployment gate.

## Rollback, observation and retirement

Before R-08 the rollback target is the retained `/2` executor and Python semantic
worker, including their interpreter, websocket package, venv/native libraries,
model/config assets and fixed launchers. R-08 may remove their **active**
requirements only after an offline, hash-pinned restoration closure is retained
and tested for each supported target architecture. A directory of Python source
alone is insufficient. Post-R-08 rollback restores that declared closure from
offline artifacts into a clean disposable home, restores the selected bindings
and launchers, and executes lifecycle behavior plus the semantic reverse rebuild
against a disposable database with the retired dependencies initially absent.
No network download or assumed global package satisfies this fixture.

The post-retirement target therefore remains the executable retained Python
release, not an implicitly chosen older Go/Rust build. Changing that target
requires a root decision and its own accepted rollback fixture. Keep all paths
still needed by live issued argv/hooks until reference and session-refresh proof
permits retirement. Unknown references or unavailable restoration artifacts
block R-08; preserving them is preferable to an unsupported removal claim.

Root's `4b912bb` reconciliation adds **S-04R / CAIRN-355** as a named prerequisite
of S-05 rollback acceptance and R-06 qualification. The separately classified
retained-Python telemetry repair requires its prescribed S-R1 and Release
rollback reviews, isolated process evidence and an immutable selected closure;
this receipt agreement does not accept that repair. Preserve the frozen
numerical oracle and installed runtime during characterization. Any changed
derivation/rollback identity needs its explicit accepted criteria rather than
being reported as restoration of an unchanged identity.

An observation lane is host × harness × account/binding, plus one semantic lane
per shared database/job queue. R-01 maps each lane to supported operation rows
and expected identities. Each installed supported memory event and each
supported request/reply/recovery path needs an observed cycle; include idle,
busy, restart, refusal and cancellation only where supported. Unsupported rows
have explicit refusal/no-op expectations and are never counted as successful
supported operations. Each lane needs at least 24 continuous hours after its
last relevant runtime change, and the semantic lane needs a full rebuild.

A relevant change affects the lane's executed binary, active configuration,
launcher, model or supervised service. Reset every affected lane, including
dependent lanes when the semantic service changes. Rehearse destructive rollback
in disposable homes/databases using the exact release artifacts. If a separately
coordinated production rehearsal is performed, its rollback and roll-forward
restart the affected observation window. R-09 must account for the final removal
release's affected lanes; pre-removal observation cannot silently certify it.
Record the minimum cycles, timestamps, unexplained anomalies and unobserved rows.

R-07 also has a transition/backup ledger. Normal semantic derivations are
regenerated from unchanged authoritative source by the tested S-05 reverse
rebuild; this is not a database-schema rollback. Lifecycle state stays
schema-compatible and its rollback snapshot is retained and exercised. Any
additional authoritative data/schema or incompatible state transition requires
a separate root decision, current backup/restore evidence and an exact-version
restore rehearsal in a disposable environment before the transition. Never use
an existing production database for tests or cleanup; missing evidence blocks
the affected transition.

## Independent fixture and evidence matrix

Fixture IDs below are acceptance cases to implement or map to retained tests,
not new commands or claims of existing coverage. R-01 records the exact test
entrypoints, inputs, expected bytes/state and evidence for each harness row.
Each successful case includes a concrete negative or interruption case.

| ID | Required observable cases | Current source/reference and evidence needed |
| --- | --- | --- |
| REL-BASE | Every supported harness/operation and explicit unsupported case; source expectation separated from installed and live state. | `scripts/test_installed_lifecycle_bundle.py`; affected harness guides and read-only host discovery. Record exact source SHA and missing observations. |
| REL-STREAM | Exact stdin bounds, stdout/stderr, exit status, required-context/prompt refusal and optional degradation. Preserve diagnostics and attribution. | Foundation and Coordination differential fixtures plus independent installed hooks. Declare normalization only for nondeterministic fields; never erase refusal, identity, ordering or state differences. |
| REL-IDENTITY | Wrong owner/mode/hash, stale process identity, wrong account/home and another session's context refuse before effects. Store owns principal and observation channel. | `inbox_binding.py`; `IdleWakeupPreflight` and `CompactInboxInstaller` in `scripts/test_install_agent_coordination.py`; Foundation/Coordination process fixtures. |
| REL-FRESH | Run at least one effectful installed event for every supported R-01 row, offline without checkout or retired dependencies, using a disposable API/store and real selected executables/host adapters. Assert the resulting write/retrieval/journal effect and exact streams/status, plus missing/changed-file negatives. | The cited legacy installed-bundle tests prove closure smoke behavior only: `--help`, hint/no-op memory and excluded coordination are not effectful acceptance. R-02 covers implemented F-01/F-04 paths; R-03/R-04/R-06 complete actual supported memory, hook/watch/inbox/recovery rows after their dependencies. Claude Stop is coordination, not memory capture; Agy has no current ordinary lifecycle memory hook. Do not invent support to fill the matrix. |
| REL-PIN | Pin and execute the same immutable bytes; reject mixed installations, stale sidecars and unsafe ownership/modes. Check both account and repository-local selection. | Legacy binding/bundle validation; future Foundation `/3` admission and R-02 installed tests. Include a replacement race rather than checking only a hash before execution. |
| REL-COMPAT | Independently pin the new complete Python compatibility receipt, trusted launch literal and exact `/3` selection; execute checked bootstrap/loader/alias/role bytes and preserve complete enabled/disabled frontend behavior. Reject mixed selections and config/binding races without a second activation or fallback. | The concrete receipt, disabled exception and private handoff above. F-04 first proves real complete frontend processes in disposable source staging; R-02 then proves the actual installer emits and executes the same contract offline. Neither replaces R-06's whole-unit qualification. |
| REL-LEGACY | `/2` stays with Python; `/3` binds the Go binary. Test both incompatible cross-version directions fail closed, versioned binding/config paths, retained issued argv and cached hooks, and the declared activation-unit topology. | Legacy validation and new real-executor cross-read/contention/crash fixtures; no in-place `/2` to `/3` binding rewrite and no unqualified mixed production watcher. Frozen two-file and bundle trials stay unchanged. |
| REL-DRAIN | Every predicate D1–D7 above has positive and negative evidence under a held quiescence barrier; stop, stage, repin, refresh, restart and reverse selection preserve the whole unit. | Legacy enabled/shared-account refusal tests are partial smoke coverage. R-02 proves available F-01/F-04 staging/activation controls. R-06 qualifies the full watcher/hook unit after J-05/F-02/F-03 and adapter dependencies; R-07 repeats it for production. |
| REL-ROLLBACK | Reverse `/3` to retained `/2` with compatible state, pins and supported refresh. Before selecting the semantic rollback generation, stop and join every index writer sharing the database/job queue and prevent inactive definitions from restarting. | Custom-state/ledger preservation tests in `scripts/test_installed_lifecycle_bundle.py`; R-02 disposable homes and S-05 disposable-store switch/reverse-rebuild evidence. |
| REL-PROCESS | Real `flock` contention, cross-executor state reads, crash, pipes, supported cancellation, shutdown, process-tree cleanup, restart and state persistence. | `scripts/test_install_agent_coordination_timeout.py`, `scripts/test_install_agent_coordination_active_turn.py`, `scripts/test_install_wake_service.py`, and Foundation/Coordination process tests. Synthetic app-server cancellation does not establish public Store cancellation support. In-process mocks do not replace process assertions. |
| REL-HERMES | Actual `MemoryProvider`, registration and hook objects preserve host returns, custody/cancel and shutdown while delegating policy/state. | `integrations/hermes/`, `scripts/check_hermes_lifecycle.py` and `scripts/check_hermes_coordination.py`. Preserve required Python and test the installed adapter with missing/refusing selected binary. |
| REL-HOOKS | Required OpenCode plugin objects and each supported Claude/Codex/Agy hook retain their host contract and native trust requirements. | `integrations/opencode/`, `scripts/install-opencode-hooks.py`, `scripts/install-codex-hooks.py`, `scripts/install-claude-hooks.py`, `scripts/install-agent-coordination.py`; real native trust/refresh evidence separate from mocked installer fixtures. |
| REL-LIVE | Verify actual executable and semantic worker image/closure, fixed launcher hashes, selected L/V/verifier and approved-shell identity/readback, active connection identity, and effective hook/issued-command identity for each live session. Detect cached old hooks, old issued argv, repository overrides and retained processes separately. | Caller-origin `implementation_id` in `docs/client-observations.md` is reported, not attested or per-session evidence. Pair it with a session-correlated native hook/host observation; a manifest alone does not show which L that session executed. Coordination requires an equivalent supported signal or an explicit blocking gap. Repeat before release, after rollout and on the final removal commit; process parentage and a current MCP connection do not prove hook refresh. |
| REL-SEMANTIC | Inventory every index writer sharing the database/job queue, including other collections; stop/join all running writers and prevent inactive definitions from restarting before switch or reverse rebuild. Disclose partial coverage, fallback and backoff. | S-05 tests competing writers configured for different collections in one disposable database. Selected-model current version/body hashes and present, valid passage rows must cover every eligible active nonforgotten record. Check parent correspondence, ordinals, byte spans and vector validity against the accepted worker contract; document/queue counts alone are insufficient. |
| REL-EQUIVALENCE | Map each old/new assertion and seed observable faults that both checks must reject before old coverage is removed. Required model tests execute rather than skip. | R-05 records applicable changed refusal/exit, reordered operations, request-ID reuse, missing persisted field, wrong span and corrupted pin faults, their expected failures and actual results on both executors/checks. An insensitive old assertion is a disclosed gap, not permission to weaken or silently repair the reference. The maximum-punctuation case additionally cites the original oracle seal, separately reviewed B1/B2 digest manifests and Overall's population disposition under the conditional sequence above; authorization and criteria agreement do not count as numerical coverage. Retain frozen trial/SDK surfaces. |
| REL-PERF | Compare the same workload and machine conditions; distinguish build/setup time, subprocess latency, worker footprint and suite duration. | Reproducible R-01 baseline and R-05 comparison with exact commands, environment, sample count and durations. Proposal targets are not observed results. |
| REL-DOCS | Every affected current-facing CLI/config/example and operational claim agrees with implemented source and checked invocation. | Architecture/status, CLI/lifecycle references, harness guides, semantic install/rebuild, testing, deployment and recovery. Apply docs-guard; proposed commands stay labelled proposed. |
| REL-MANIFEST | One exact candidate binds source, Go/Rust/model/config and fixed-launcher hashes, compatibility L/V/verifier identity and approved shell/Python trust closure/readback, running worker/interpreter/library closure, derivation, schema, host/session matrix, checks, findings, limitations and executable offline rollback closure. | R-06 candidate evidence, R-07 installed/live observations, transition/backup ledger and R-09 final reconciliation. Include the maximum-punctuation oracle seal, reviewed B1/B2 digest manifests and required population disposition. No credentials, raw sessions, generated binaries or operational memory committed. |
| REL-OBSERVE | Each defined lane completes its minimum supported cycles and 24 continuous hours after its last relevant change; the shared-queue semantic lane completes a full rebuild. | Use the lane/reset/rehearsal rules above, disclose unobserved rows and failed/pending work, and reset after affected removal/runtime changes. Time alone is insufficient. |
| REL-RETIRE | No active configuration, process, cached session hook or unsettled issued context needs a removed file/dependency. Every session has new hook-identity evidence; no retired identity is observed during the qualified window. | R-08 exact candidate, reference/session/process inventory, initially dependency-free offline rollback restore plus lifecycle execution and semantic reverse rebuild in disposable environments. Keep required Hermes Python, frozen trials/SDK and still-referenced compatible paths. Unknown identity/reference or missing rollback closure blocks removal. |
| REL-FINAL | Recheck and redeploy the exact removal commit everywhere, including every long-lived client, then reconcile findings and clean pushed trunk. | R-09 final manifest and independent review. Pre-removal deployment evidence does not verify the removal release. |

Tests use synthetic fixtures, disposable homes and disposable PostgreSQL.
Existing operational stores are never used for tests or cleanup. A test skipped
because `bwrap`, a native binary or model assets are absent remains missing
coverage. Existing installer mocks are recorded as such and cannot establish
native handshake, process or live-client acceptance.

## Work items and release gates

Stable external IDs use source `hybrid-runtime-consolidation` and
`hybrid-runtime-v1/<key>`. Every release item is a direct child of CAIRN-301.
The dependency tables remain authoritative; dependencies are enforced even
when the tracker cannot display native blocking edges.

| Key | Plane | Builder role | Blocked by |
| --- | --- | --- | --- |
| R-01 | CAIRN-303 | R-B2 | X-01; read-only discovery and existing-fixture work may start immediately |
| R-02 | CAIRN-304 | R-B1 | F-01, F-04, R-01 |
| R-03 | CAIRN-305 | R-B1 | F-02, F-03, J-05, R-02 |
| R-04 | CAIRN-306 | R-B1 | J-06, R-02 |
| R-05 | CAIRN-307 | R-B2 | F-02, F-03, J-06, S-05, R-01 |
| R-06 | CAIRN-308 | R-B2 | R-03, R-04, R-05, S-05, S-04R |
| R-07 | CAIRN-309 | R-B1; release lead serializes production | R-06 |
| R-08 | CAIRN-310 | R-B1 | R-07 and observation exit criteria |
| R-09 | CAIRN-311 | R-B2; release lead controls deployment | R-08 |
| HC-05 | CAIRN-312 | Release team | R-02 through R-06 and the full installed harness matrix |
| HC-06 | CAIRN-313 | Release team | R-08, R-09, HC-02, HC-05 |

The installed Plane CLI lacks relation operations and no legacy relation tool
is exposed to this session. Native blocking edges are **unverified**; body
dependencies and the campaign map enforce them. Native parent IDs and stable
external IDs are read back separately. Team/model/session assignments are
metadata, not invented Plane memberships. Exact live session assignments and
operational inventory are retained outside Git.

Before building, Opus 5.5 Max reviews these criteria and the current source.
Before landing, it reviews the exact candidate commit independently, and every
blocking finding receives a fix and re-review. Green tests do not close a
finding on their own. Each author owns local validation, queued integration,
pushing main and removing the authored branch/worktree.

Runtime/store changes require `make check` and `make test-integration` locally;
lifecycle, backup and restore changes additionally run the relevant
`make test-lifecycle` coverage. Actual worker acceptance must execute the
selected Python reference and Rust artifact with prepared model assets.
Documentation-only setup does not establish any of these runtime gates.

R-06 produces a reviewed release candidate; only coordinated R-07 execution
changes production. R-08 cannot retire a runtime before observation exit and
independent acceptance. R-09 revalidates and redeploys the final removal commit.
HC-05/HC-06 remain open until their actual criteria are met. Report implemented,
tested, deployed and measured claims separately; none alone establishes full
design acceptance or usefulness.
