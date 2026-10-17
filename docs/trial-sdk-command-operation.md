# Parent command operation coordinator

`scripts/trial_sdk_command_operation.py` implements one serialized parent
operation using the existing `ServiceLink`, profile-4 codec, original
`AttemptResources`, and pending `MemoryDispatchGate` lifecycle. It adds no
launcher, event loop, socket, provider, native argument mapping, or experiment
admission. The fixed builder still supplies current-read receipt authority;
`FixedCommandPolicy` validates command shape and route, not read permission.

The source base is `358b99956902f987afa34cd68da83da066f3b0f1`, including the
STARTED/progress-outcome protocol change from `eefd8c2`. The ledger dependency is
`babf6deff692d903414a5091458b61607f3437df`, cherry-picked as `92b29a3`: cumulative
known prefixes can be recorded for every held traffic ticket after freezing.
No normal grant, settlement, allocation release, or replay is allowed afterward.

## Fixed builder contract

Before constructing either endpoint's storage, the builder must hold their
combined standing quote in the original ledger: parent raw storage is 2,080
bytes and child raw storage is 2,592 bytes. An ordinary service-stdio reader
with a 512-byte backing makes that standing raw quote 5,184 bytes. Its working
quote must also cover simultaneous builder/service objects. The coordinator
requires a real `ServiceLink` whose READY has already been received and whose
READY credit has been finished. It does not establish service ownership,
authenticated transport, source custody, readiness, or descendant containment.

`OperationQuote` requires all seven fields: `parent_raw`, `parent_working`,
`child_raw`, `child_working`, `preparation_raw`, `preparation_working`, and
`job_body_working`. The builder must quote simultaneous retained aliases and
scratch. The parent needs the input aliases, request graph, canonical encoding
scratch, UTF-8 output fragments, joined results, and bookkeeping. Child
preparation includes decoded request text, JSON graphs, tuples and policy plans;
the child quote additionally includes `CommandJob`'s 1,548 raw bytes,
`TEXT_RESERVATION`, and its normalized body alias. Non-search normalization may
increase input text size, so the body quote must cover the policy's full bounded
Unicode result. Constructor checks establish minimum logical requirements, not
whole-process physical fit. The pure fixture uses explicit 40,000-byte parent
raw/64-MiB working and 65,536-byte child raw/64-MiB working quotes.

`CommandOperation(link, quote, operation=..., lease=..., deadline_ns=..., clock=...)`
allocates `local.operation` and the actual service domain's `operation` before
creating the request graph or encoding buffers. It also holds emergency control
credit B for 251 original traffic bytes. Operation and lease ordinals belong to
the fixed serialized builder. Construction or handoff failure freezes retained
commitments; the builder must retain its original ledger and service owner.

Bind the existing shared gate with `bind_gate(gate)`. Its fixed hook forward must
be this operation's `forward_hook`, or one builder-retained
`HookOperationForward(link)`. The latter binds successive operations only after
the preceding operation is closed and retired, on the same actual link and gate.
It never resets the gate's attempt budget. `unsupported_native` explicitly
refuses native forwarding. Call `prepare_hook(name, args=(), body=None)` once. The deadline must
be the original hook deadline, at most its actual first-read time plus five
seconds, rather than a longer outer attempt deadline. The gate may tighten a
timeout but cannot extend the original deadline. This coordinator requires its
forward's deadline to match exactly.

Call `step()` on actual reactor readiness. Each call schedules bounded work or
attempts at most one send and one receive. A false result means no progress;
the caller parks using the actual link interests and existing outer deadline.
It must not infer readiness from elapsed time. No subprocess, blocking wait,
poll loop, or sleep is performed here.

## Sequence and custody

PREPARE and bounded REQUEST chunks precede REQUEST_END. PREPARED invokes the
gate's hook `start` once. The gate debits before its fixed forward queues START;
the returned pending handle remains retained while the operation runs. A
returned forward means queued work, not CLI or API completion. STARTED is
required before any TURN.

Before each TURN, control credit A holds 406 bytes: TX 53 and receive cursor
`(56, 224, 40, 33)`. B remains held. Only then does the coordinator grant
`min(1536, original_available)` CLI bytes, further bounded by any existing final
ceiling. PROGRESS settles only that exact ticket's known sum. For a final zero
offer when the original ledger still has room, it holds a positive one-byte
ticket and offers the known zero subset. The final ceiling never revives.
Wire grant ordinals come from TURN's freshly issued data ticket and DELIVER's
freshly issued A ticket, which follows every previous data/delivery ticket.
They remain increasing across successful operations on the same ledger and
link. DELIVER never uses the older emergency B ticket ordinal.

The real core's healthy PROGRESS, OUTPUT, text and STATUS branches determine
when an automatic reply cursor ends. No queued or sent CANCEL is forgotten.
For the last DELIVER, B's future cursor `(41, 41, 41)` is committed before the
trigger while A owns the text receive. The same RX backing then adopts B.
This covers STATUS, a crossed ACK, and RELEASED when a refusal replaces text.
The coordinator never refunds an unobserved terminal cursor; a refused last
DELIVER uses it for the eventual RELEASED. A nonlast text needs no terminal
cursor until cancellation; its future ACK is committed before CANCEL.

`cancel(reason=1)` records intent. The sole sender completes an already-queued
record, commits any missing ACK receive, then sends CANCEL once. PREPARED,
STARTED, PROGRESS, OUTPUT, text, STATUS and a prior REFUSE may cross cancellation.
ACK6 is required before RELEASE. Known prior refusal and progress remain
available; a known refusal result includes `refusal`, `prior_refusal`, and
`cancelled`. Calling cancel after RELEASE is queued returns false because
release is already final and the wire contract allows no subsequent CANCEL.

Known RELEASED ends the child allocation. Only actual operation closure under
the original deadline calls `gate.complete_pending`. `result()` then returns
`CommandTextOutcome` or the known-refusal dictionary. The parent allocation
remains charged. Its source caller must end or transfer every result/request
alias before calling `retire()`, which clears this owner's references and
releases `local.operation`. A successful CLI exit is not API confirmation.

Uncertain transport, PROGRESS outcome 1, deadline expiry or interrupted handoff
freezes original allocations and traffic commitments. Known prefixes remain
charged. The coordinator fails the exact pending gate handle when it obtained
that handle; if the handle's return was interrupted, the gate's pending dispatch
remains unsettled. There is no replay or default-route fallback. Operation
cleanup does not replace the builder's external service containment and wait.
Before requesting another A credit, the coordinator compares its exact maximum
with original available traffic. A known capacity shortfall preserves B and
uses CANCEL/ACK6/RELEASE cleanup, without issuing another CLI turn. DELIVER can
offer a smaller positive text chunk when its TX and RX fit. If even that cannot
fit, the result reports `budget_refusal: operation_control_exhausted` alongside
the actual service acknowledgment. Capacity refusal before the first PREPARE
creates no child aliases or gate debit and reports no fabricated service refusal.
Any later unknown cleanup still retains the actual unresolved commitments.

## Local checks and limits

Run `timeout 20s python3 -B -m unittest discover -s scripts -p
test_trial_sdk_command_operation.py` from the repository root. The tests compose
the real ledger, gate, ServiceLink, RecordSocket validation, codec, transcript,
CommandService and CommandJob. Only transport/native-effect and clock seams are
inert fixtures. They exercise exact traffic totals, queued START EAGAIN, maximum
text terminal precommit, stage and crossed-reply cancellations, known refusal,
zero final offer, uncertainty, deadline and interrupted gate/settlement handoffs.
They also cover stdout above 128 KiB, replacement-decoded stderr whose encoded
volume exceeds 128 KiB, two successful operations sharing the same gate and
ledger, and known control exhaustion before TURN and during text delivery.

The initial missing module failed before implementation. A subsequent unknown
PROGRESS test exposed `root_unknown` when quarantine ran inside the borrowed
frame consumer. Moving quarantine after frame completion fixed that failure
while retaining the reported prefix. These are pure checks, not socket/kernel,
native SDK, account, provider, full-107, or measured-usefulness acceptance.
