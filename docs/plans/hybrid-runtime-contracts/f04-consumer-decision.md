# F-04 production consumer and delivery boundary

Decision: select Foundation proposal A on 2026-11-06 for CAIRN-301 / X-01.
This refines delivery order; the full Go lifecycle migration remains required.
It does not close X-01, accept an implementation or activate production.

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
