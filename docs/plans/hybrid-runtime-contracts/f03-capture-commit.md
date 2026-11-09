# F03-M1 capture selection and commit

`memoryclient.Client.CommitCaptureSelection(ctx, CaptureInput)` validates a
selected checkpoint and up to three reusable notes, then saves the resulting
records in order. It ports the selection and save portion of
[`integrations/lifecycle/memory.py`](../../../integrations/lifecycle/memory.py)
(`selected_writes`, `save_note`, and the capture save loop). The Source SHA-256
is `6c9cafcde3600671ed11e4f49f220c086e33ebf998051969931a9e232059f597`.
The accepted F-R1 criteria are `68719609-0aea-43eb-aa5a-a9b570330227/v1`,
forwarded in implementation release `0668f454-d378-440b-b4a7-87e611172d0c/v1`.

## Input and validation

`CaptureInput.Selection` is one raw JSON value. Its exact keys are
`checkpoint`, `workstream`, `memories`, and optionally `checkpoint_state`.
Member names are case sensitive; the last duplicate member wins. Each memory
has exactly `record_id`, `kind`, `title`, and `body`. Durable kinds are
`decision`, `preference`, `lesson`, and `procedure`.

`ProjectBase` is a basename already resolved by the caller. Empty names,
quotes, and LF are accepted. An empty `ExplicitWorkstream` means absent.
`CaptureRecord` supplies an ID, integer version, kind, and body. Candidates
use the last duplicate ID; handoffs retain their supplied order, with
`Previous` checked last. The component leaves ID and version validation to
the service, including zero or negative versions.

MC1 is an explicit input-domain narrowing: raw selection Unicode, every
`CaptureInput` string, and the client's repository string must be valid before
any lookup or write. Raw JSON passes `jsontext.CheckUnicode` before decoding,
so invalid UTF-8 and unpaired surrogate escapes cannot become replacement
characters. Non-RFC tokens and trailing JSON values are domain errors. Nil,
empty, whitespace-only, or JSON `null` selection is the Source schema refusal.

Checks retain Source order. Checkpoint state precedes checkpoint content;
the checkpoint limit is 4500 original UTF-8 bytes. Note fields precede
candidate references. New titles are checked at their original 90-byte limit,
then stripped and prefixed. A reusable body's limit is 6000 bytes, checked
again after a new title is added. Python whitespace includes U+001C through
U+001F in addition to Go's `unicode.IsSpace`.

New durable titles use the first 40 complete UTF-8 bytes of `ProjectBase`
followed by `": "`. Workstream titles remove quotes from the basename before
the same clipping, and use `"Handoff: "` and `" / "`. Existing-ID reusable
bodies remain verbatim. A complete checkpoint adds `"Status: complete\n"`
before the stripped checkpoint body.

All entries are planned before the first record mutation. `Checkpoint`
lookups occur during planning, before the repeated-topic check for that
entry. An equal fallback body is a no-op and needs no ID, version, or kind.
A differing fallback body requires an explicit edit. Fallback records never
become write targets. A complete handoff requires a supplied match before
any fallback lookup. Explicit-workstream equality is checked on the raw topic
before topic validation.

## Writes and progress

The method borrows the existing authenticated SDK and repository scope.
Create sends only the request ID and a draft with kind, body,
`Scope{repo, "*", "*"}`, `shareable` sensitivity, and `self` claim type.
Revise sends the request ID, supplied record ID and expected version,
repository, and body. Session and diagnostics headers come from the SDK;
no identity or attribution fields are copied into these payloads.

The request ID is UUIDv5 in the URL namespace over the UTF-8 name
`[repo, kind, prior_id_or_null, prior_version_or_zero, sha256hex(final_body)]`.
Its fixed-shape encoder reproduces Python's compact `ensure_ascii=False`
JSON: short control escapes, lowercase `\u00xx` for other C0 controls,
and literal UTF-8 for DEL, U+2028/U+2029, HTML characters, and `/`.

Calls are sequential, each capped at five seconds under the caller's context.
The method stops at the first write error. It does not retry, roll back,
refund, or close the SDK. Create and revise add no recall accounting;
lookups use the existing `Checkpoint` accounting.

`CaptureResult.Saved` contains confirmed reply IDs in order, excluding no-ops.
A confirmed reply is object data with a non-empty string `record_id`; this
deliberately narrows Python's truthiness check. `Workstream` is set after the
final note save or no-op succeeds. Its value is the final body's text before
the first LF, including when the project basename contains LF.

Callers must retain a partial result when handling `ErrCaptureWrite`:

- `ErrCaptureSelection` has the exact Source refusal text.
- `ErrCaptureDomain` identifies unsupported JSON or Unicode before I/O.
- `ErrCaptureWrite` wraps the write cause: service, context, transport,
  `ErrNotConfirmed` for non-object data, or the fixed
  `"Cairn did not confirm the selected record"` confirmation failure.

Lookup errors are returned unchanged from `Checkpoint`. Every error before
the write phase returns an empty result.

## Focused validation and limits

The six `TestCapture...` families in
[`capture_test.go`](../../../internal/lifecycle/memoryclient/capture_test.go)
cover validation, reconciliation, request identity, partial failure,
cancellation and attribution, and accounting. They use the existing private
Unix socket fixture, synthetic credentials, the real Go SDK, and hand-written
UUID-name and whitespace expectations. The frozen membership is six top-level
tests and 45 direct subcases.

This component does not commit frontend capture markers, digests, status,
messages, shared state, or the hook meter. The private fixtures do not establish
server CAS or idempotency, capture frontend behavior, grants, privacy, hook
identity, or runtime qualification. CAIRN-319 remains In Progress; full F03
acceptance is separate.
