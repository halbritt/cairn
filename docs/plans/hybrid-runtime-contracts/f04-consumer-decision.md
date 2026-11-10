# F-04 production consumer and delivery boundary

Decision: select Foundation proposal A on 2026-11-06 for CAIRN-301 / X-01.
This refines delivery order; the full Go lifecycle migration remains required.
It does not close X-01, accept an implementation or activate production.

The [activation source slice](f04-activation-slice.md) records its concrete
private executable, compatibility caller and verification boundaries.

## First consumer

F-04 delivers exercised shared-state and binding-activation support. Its first
consumer replaces the `InboxBinding.effective_config` activation prefix in the
actual memory and coordination hook frontends. Port the existing admission,
enabled-snapshot and ledger activation behavior, including marker-first
persistence. Deliver the caller and Go implementation together. Do not allocate
or refund grants, claim a turn, call the store, or execute a journal transition
from this operation.

The private operation is proposed as `cairn lifecycle binding activation` with
`--entrypoint memory|coordination` and an absolute `--config` selection. The
original bounded hook-event bytes reach the child unchanged. A successful
reply is exactly one compact `{"enabled":true}` or `{"enabled":false}` JSON
line, with no stderr. Private failures are bounded and source-classified; the
parent retains its existing public streams, exception handling and exit policy.
Child launch, protocol or persistence failures never become `enabled:false`.

The parent has not acquired its ordinary memory/coordination session lock at
this point. Go alone acquires the existing activation lock and releases it
after the required durable boundary. No borrowed-lock protocol, dead mark-only
command, duplicate activation, or successful no-op adapter is accepted.

Preserve the parent's configuration snapshot as well as the original event.
The reviewed private protocol must bind the parent's checked configuration
bytes to those consumed by Go, either through a checked snapshot handoff or an
expected digest checked before any effect. A second path read that silently
consumes changed configuration is insufficient. The digest is a consistency
check, not authentication or permission to select another configuration.

## Independently authenticated compatibility caller

F-01/F-04 may introduce a temporary, immutable Python caller that understands
the `/3` binary binding. The following three selections remain distinct:

| Selection | What it authenticates | Required behavior |
| --- | --- | --- |
| Existing `/2` descriptor | Its exact complete legacy Python bundle | Execute the unchanged legacy route. Never infer Go selection from this pin. |
| New `/3` binary descriptor | The exact selected Go executable and its reviewed configuration binding | Verify the pinned and executing image through the F-01 policy. It does not authenticate Python. |
| Separate versioned compatibility receipt | Python bootstrap, public entrypoints, aliases and the complete retained implementation closure | Independently pin every executed member and the exact Go/config selections it may delegate to. Verify and execute the checked bytes from the immutable snapshot. |

The compatibility receipt is a new artifact, not an invented capability of the
current installer. Foundation owns its validation and callable protocol;
Release owns its immutable installation and activation. They must agree on
the concrete versioned receipt and loader contract, and obtain independent
review, before implementation consumes it. The closure includes the loader
itself, `inbox_binding`, `inbox_recall`, memory/coordination entrypoints and all
retained imported helpers. A self-described manifest or a binary digest alone
is not independent authentication of that closure. Use owner-controlled,
versioned paths, an independently pinned receipt selection and checked byte
snapshots; do not execute a different file after validation.

The receipt's Go/config selection must agree with the `/3` binding. Missing,
changed, mixed or incompatible selections fail before mutation. No existing
`/2` bundle or binding is edited in place or reinterpreted as `/3`. The original
`/2` route remains usable for rollback and existing callers.

Enabled hooks must continue through the complete retained handler path.
Current `load_bridge` and `validate` require `/2` and bridge/engine descriptors;
changing activation alone would fail later. The new compatibility loader must
explicitly load those retained roles from its independently checked closure,
while `/3` continues to authenticate Go only. It must never synthesize a fake
`/2` descriptor to authorize Go or silently fall back after Go starts.

F-04's fixtures stage the real admitted caller in disposable homes and execute
both complete public frontends, enabled and disabled, through actual child
processes. Prove exactly one Go activation, no duplicate Python activation
mutation, unchanged outer streams/exits, real retained-handler continuation,
configuration-race refusal, tampered/missing closure refusal, and immutable
legacy coexistence. R-02 need not land first: reviewed source staging fixtures
can establish this consumer without changing production. Later R-02 acceptance
must exercise the production installer against the same receipt contract.

## Compatibility closure policy and caller slot

The 2026-11-06 C1/C2/C3 refinement accepts the immutable selection and admission
policy below. Exact Foundation/Release delta review remains required. C1 also
includes the compact-command correction here; accepting the policy does not
certify the earlier candidate's complete issued-command coverage.

The trusted launch vector `V` contains the independently selected interpreter,
verifier literal, receipt path/digest, role/alias and config. New issued Python
commands reach `V + ["--", *original_command_tail]` through the short trusted
launcher `L` defined by [Foundation's launcher contract](foundation.md#compact-launcher-commands-and-presentation).
Preserve original stdin and command authority checks. Revalidate the selection in each new process; do not
assume the hook's checked context survives. Direct `cairn agent history` remains
direct. Hook deadlines do not replace inbox command/intent-lock deadlines.

This includes existing `inbox compact read|inspect|settle|advance` operations.
They are session-scoped issued commands, distinct from excluded offline/operator
inspection such as `inspect-held-refusal`. The current `inbox_context` separately
renders bare `cairn-inbox` cues, so changing only `inbox_handoff` and
`inbox_cue_text` is insufficient. New compatibility compact cues render
`L inbox compact OP HANDLE ...`, including fallback and next-item cues; the
launcher executes exactly `V + ["--", "inbox", "compact", OP, HANDLE, ...]`.
Bind the handle-resolved config/root/context to the checked
selection before loading its derived config or performing existing effects.
`CAIRN_INBOX_ROOT` is a lookup input, not authentication. Preserve owner, TTL,
lease, causation, journal and native-turn checks. Existing `/2` bare launchers,
handles and issued argv remain untouched and usable until qualified retirement.

Release installs one immutable, versioned `L` per exact vector/config selection
under a persistent private content-addressed path. The approved noninteractive
shell script only executes the quoted vector followed by `-- "$@"`; it neither
parses the tail nor chooses another program. `L`, the approved shell and owner
hook selection are the explicit external trust anchor, not self-authenticating
receipt members. Record exact bytes, path, hash and vector identity on controlled
installation/readback. Generate `L` after the receipt; the receipt does not hash
`L`, avoiding a cycle. Retain referenced old launchers. Qualify the selected
shell against `ENV`/`BASH_ENV` startup execution while preserving the captured
parent environment required by admission.

All newly issued Python wrapper command fields and their cues, fallbacks and
turn-end reminders use `L` plus the original tail. Never expose the full verifier
literal in these commands. Direct `context.read` and inner store CLI argv keep
their existing forms. Each rendered command is at most 512 UTF-8 bytes and
aggregate command/control prose at most 2048 bytes; actual encoded output and
recall must also fit every smaller configured/source/harness limit. Reject
unrepresentable installation choices, without truncation or false delivery.
Fixtures execute the exact emitted shell text, including quoting/non-ASCII and
long legal arguments, all wrappers, compact modes and reminder variants.
Programmatic argv-only tests do not establish this presentation contract.

Foundation F-B3 receives the sequenced compatibility-only slot for the issued
argv constructor in `inbox_handoff`, command rendering in `inbox_cue_text` and
`inbox_context`, and the minimum checked-selection plumbing at compact config
resolution. This extends its original event/config caller slot after F-01.
It does not transfer command grammar, claim, journal, watcher or native-session
transition ownership. Coordination acknowledges the exact delta and defers
overlapping edits until this reviewed caller lands. Include real compact
read/inspect/settle/advance, next-cue, mismatched-selection and legacy-handle
process fixtures in the affected independent reviews.

Immutable enabled selection A is not disabled merely by publishing disabled B.
Supported per-session refresh and readback of B precede D1/D6. Cached A or
unsupported/unknown refresh blocks production activation/transition. Refresh
does not settle retained true activation grants or prove drain; ordinary
owning-session settlement and retained old closures remain required.

Authenticate the receipt and complete executing closure before execution;
launch Go through its checked executable descriptor. With valid code, preserve
source admission before any `/3` binding-data or target-pin read, using the
captured parent environment, cwd, event and checked config. An excluded activation
prefix returns its source skip/false result without binding access or activation
mutation; the retained frontend still follows its ordinary policy. This is not
a whole-hook skip. Admitted work still performs
the full binding/image/config checks before effects. Invalid executing code or
receipt selection is an explicit new compatibility-preflight failure, including
on excluded callbacks: empty stdout, exit 1 and the fixed role-specific
`compatibility_preflight/CompatibilityPreflightError` diagnostic defined by
Foundation. It causes no ledger, refusal-record, token or API effect. Do not
reclassify ordinary source binding/state failures as preflight failures. `/2`
behavior and the no-fallback rule remain unchanged.

## Item and milestone allocation

- F-04 precedes F-02/F-03 and J-02, with no J-05 dependency. It accepts the
  exercised shared-state/activation subset, not a claim that every grant or
  native-session operation has migrated.
- Remaining grant-ledger operations land with their actual F-02/F-03 memory
  consumers. Their compatible persistence and delivery behavior remain HC-03
  requirements; activation alone cannot close HC-03.
- Native-specific exports land with their first actual consumer. J-05 owns
  native-session orchestration and delivers the compiled `session.JournalBoundary`
  and `journal.SessionJournalAdapter` with that consumer. J-02/J-03 supply the
  sole journal operations and recovery facts. An earlier F-02 native-state read,
  if needed, lands with that reader rather than as an unused API.
- Foundation retains ownership of shared state/lock primitives and contributes
  compatibility review. Coordination owns native-session transition policy;
  HC-04 accepts it. No full native-policy migration is claimed by F-04 or HC-03.

Foundation and Coordination must preserve the already specified flush scopes,
strict settlement behavior, exact-result recovery and error taxonomy. Until
the real Go orchestration consumer lands, use the retained Python executor or
explicit unavailability; never acknowledge a successful no-op flush.

## Closure required

Foundation updates F-04/HC-03 criteria and the domain contract. Coordination
acknowledges J-05 ownership and the one-way dependency. Release agrees the
receipt/staging boundary. F-R1 independently re-reviews B4 against the revised
exact candidate; affected Release/Coordination criteria receive delta review.
These steps close the specification finding only. Runtime checks, independent
candidate review, trunk integration, all-host deployment, observation and final
retirement gates remain required.
