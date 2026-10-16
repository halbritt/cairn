# SDK hook control profile

The selected direction is a controller-owned SDK hook interface. The owner asked
the agent on 2026-10-14 to recommend a route, make decisions and proceed without
stopping for questions. This selects a separate `sdk-hooks/1` profile alongside
the existing command-hook declaration. It changes physical stdin closure to a
single frozen user message followed only by bound control replies. It changes
delivery evidence to structured callback results. It grants no extra user turns,
tools, memory calls, input bytes or provider capacity.

The existing `compile_launch` command-hook declaration retains its behavior.
The SDK compiler uses the same fixed executable, six builtins, eight turns,
permissions, model/effort and isolated settings/MCP declaration. Initialization
registers only SessionStart, UserPromptSubmit, PostToolUse and PostToolUseFailure,
each with the existing five-second hook timeout. The controller creates the
request identities; callers cannot add hooks, users or control commands.

`SDKLaunch` is distinct from the command-route `CodingLaunch`. It exposes a
control writer and `guaranteed_context_bytes`, with no separate `input_message`
or command-hook `hook_context_bytes`. Its owner writes only the controller's
queue. The command bootstrap rejects an SDK launch before generating a user
file. An SDK owner must bind empty command hooks (`hooks: {}`), empty setting
sources and the reviewed settings/MCP mounts; an argv declaration alone does
not establish that native loaded those settings.

## Control and write contract

Initialization must be fully written before accepting its response or any hook
callback. The user is sent once, after matching successful initialization and
completion of any earlier reply writes. SessionStart can arrive before the user;
other selected events require the complete user write. A callback must match a
frozen callback ID, event and session. Request IDs cannot be reused.

The matched initialize success response accepts the original three fields
`subtype`, `request_id` and `response`, or exactly those fields plus both
`pending_permission_requests` and `pending_user_dialog_requests`, as constructed
by the selected native source. Both added values must be empty lists. Nonempty
lists refuse with `sdk_initialize_pending` before queuing the user; this profile
has no permission/dialog request handler. Partial extensions, other keys and
wrong types still refuse. The nested `response` remains a bounded opaque
dictionary; account, permission and discovery declarations establish no authority.
This extension applies only to initialization. Callback replay comparison still
covers the complete reply envelope and content.

Writes are incremental. Attempted/queued bytes and completed local writes are
separate facts. A callback reply is complete only after every byte is reported
written. A failed or zero-progress write makes the controller unusable. Frame
bytes never interleave, including when a callback arrives during a partial write.
Unknown, malformed, duplicate, cancelled or out-of-order control traffic refuses
explicitly. A failed controller cannot be reopened or silently retried.

`receive` accepts only the three control frame types: `control_request`,
`control_response` and `control_cancel_request`. The owner must demultiplex
ordinary system, assistant, user, stream and terminal records before calling
it, and refuse unknown outer record types. Sending an ordinary record to this
control-only interface poisons the controller.

The driver must serialize controller calls and report each actual write before
another state transition. A callback can defer an unstarted user frame and
invalidate a previously offered suffix. The driver must reacquire that suffix
after receiving or replying to a callback; a byte count cannot authenticate a
stale write buffer.

Known replayed replies may be correlated by canonical byte count and SHA-256
after the local reply write completes. The writer retains this bounded digest
instead of another raw reply copy. A replay is not schema acceptance, settlement,
hook continuation or model delivery: the inspected native parser can replay a
response even after rejecting the pending promise. An unmatched response is an
error, rather than a reason to repeat the consumed diagnostic or relax its
whitelist. That diagnostic's unretained 269-byte response remains unidentified.

## Bounds and evidence

All initialization, user and reply wire bytes share the existing 8500-byte
capture-input ceiling. Their selected-input charge, the appended instruction and
the existing 512-byte reserve share the 9500-byte ceiling. Preparation must leave
at least 1000 bytes of hook room after initialization and the frozen user. There
is no separate control-byte allowance. Memory results and independently measured
producer output must also be charged by the composed evaluator before admission;
the control writer alone does not enforce the full selected-input measurement.
The controller exposes remaining wire and selected bytes separately. It reserves
empty reply envelopes for every known unanswered callback and one not-yet-seen
SessionStart and UserPromptSubmit. Additional tool events reserve an empty reply
when observed; preparation does not predict their count. Context can spend only
the smaller remaining allowance after these reservations. Per-callback capacity
distinguishes escaped JSON content bytes from conservative raw UTF-8 bytes.
`reply_wire_bytes` reports the exact profile envelope cost, including escaping
and the newline. With empty context it gives this profile's minimum reply,
not a source-verified minimum for every native encoding. `reply_budget` is a
read-only snapshot separating pending empty replies, mandatory unobserved
replies and room for escaped context. It remains available after refusal;
it grants no bytes and does not count external producer or memory charges.
There is no unconditional remaining-tool-event count: event names, request-ID
lengths, context and startup reservations affect that count. Eight turns does
not bound tool calls. A workload-fit claim requires a separately supported
callback bound and its encoded replies within both original limits. An empty
reply still spends bytes and cannot be written after exhaustion.
The SDK compiler's hook context capacity uses the conservative value, allowing
for worst-case six-byte JSON escaping of one UTF-8 byte. It can be below the
1000-byte minimum raw hook room because envelopes and escaping consume space.
The existing stream/queue, 128-hook/64-KiB ledger, five-second hook, capture-relative
30-second last native result, native-search two and combined-expansion four
ceilings remain applicable. Hook searches are counted separately; forbidden
native history does not become available through this profile.

## Incoming documents and producer input

Incoming document size is separate from the 64-KiB retained ledger. The selected
SDK entry accepts at most 128 KiB of original JSON wire and canonical frame
content, including whitespace in the original count. This supports an otherwise
valid 70-KiB callback. It is an explicit input restriction, not a native-schema
bound or proof that all original tasks fit. Larger callbacks require separately
reviewed working-set and producer semantics; they must not be truncated.
Graph validation permits dictionaries and lists that fit the selected document
bound, with at most 131072 nodes including keys, depth 32, finite floats and
integer magnitudes of at most 1024 bits. These are explicit parser restrictions,
whose compatibility with arbitrary native tool results remains unproved; they
replace the earlier unsupported 64-key/256-entry/2048-node restrictions.

The transport owner incrementally decodes bounded UTF-8 blocks, releases raw
blocks, validates the complete document and demultiplexes it before transferring
an `OwnedJSON` token to `receive_owned`. The token is consumed once, including
when the controller refuses. Direct bytes/dictionary `receive` is a pure
compatibility entry point; it does not implement raw transport accounting.
Duplicate keys, malformed tails, invalid UTF-8, nonfinite numbers and unpaired
surrogates refuse before callback retention or producer execution. A prefix is
never a validated document.

Raw read blocks, decoder carry, output queues, temporary byte copies and producer
transport chunks share the original aggregate 128-KiB queue. A complete raw line
or canonical producer body must not be assembled by the driver. Decoded Unicode,
parsed graphs and encoder Unicode tokens are explicit working allocations under
the same 512-MiB aggregate attempt limit as native, backend and producer processes.
Moving data into Unicode grants no extra process memory. Kernel pipe/socket
buffering is separate from the userspace queue counter and remains within the
composed resource boundary. Pure Python allocation estimates do not establish
whole-attempt memory use or effective runtime containment.

Callbacks expose bounded `iter_input_json` chunks for complete canonical semantic
JSON delivery. The owner must count new producer-input transport volume, actual
short writes and output while preserving the five-second deadline from the first
incoming byte through validation, hashing, producer work and complete reply write.
Canonical delivery does not promise byte-identical original wire. The explicitly
named `diagnostic_projection` is only for the fixed marker producer: it keeps
binding/tool metadata and whole-input hashes after full validation. It omits the
body and cannot stand in for complete input to a general producer.

Delivery ledger `/2` keeps original-frame byte count/hash and its encoding source
separate from canonical input count/hash. A stream-line hash excludes LF while
the aggregate stream charge includes it. Direct bytes are hashed as supplied;
dictionary callers have a labelled canonical-frame hash. These are local
correlations, with no native-origin authentication. The ledger retains no input
body. Queue, stream, producer and working-memory composition still requires an
independently reviewed owner before runtime admission.

The controller retains bounded correlation and local-write facts. A producer's
actual stdout, stderr and wait belong to its producer observation. They must not
be invented as native command-hook stdout, stderr or exit 0 for an SDK callback.
The SDK delivery ledger needs its own version and must not loosen the existing
command-hook H decoder or reinterpret its rows.

The local writer currently serializes additional context for all four events.
That shape still needs source-pinned native compatibility checks, especially for
PostToolUseFailure. Hook success or continuation cannot establish delivery: the
native callback path can catch a schema or request error and continue. A native
diagnostic must observe each event's unique synthetic marker in the next model
request through an independently held witness that the six tools cannot forge.
The scripted local backend can establish this narrow claim without a paid model
call; its results do not establish serving-account capacity or usefulness.

Callback IDs, JSON framing, source hashes and PID/UID relationships establish no
exclusive native invocation authority. Actual producer execution, native-channel
actor exclusion, native continuation, selected H custody, observed child waits,
external cleanup, account identity, full-run capacity and task usefulness remain
separate implementation and verification work. Complete bootstrap and provider
launch remain unavailable until that composition exists. Missing evidence is
work to resolve; it is not a requirement to ask the owner to select routine
implementation details.

The separate [SDK process proxy](sdk-spawn-proxy.md) supplies the public stream
and lifecycle seam for a future Python-owned native adapter. Its synthetic local
checks cover bounded decoding, backpressure and returned/uncertain write loans.
It supplies no control-frame writer, callback schema, IPC authentication or actual
native launch. The [original-line forwarder](sdk-original-line-forwarding.md)
preserves validated JSON spelling for the future transport owner.
The [diagnostic launch mapper](sdk-launch-mapping.md) validates the fixed spawn
tuple and proposes its executable/print mapping without starting native.
