# F02-M2: whole unfinished checkpoint refresh

`Client.RefreshCheckpoint` in `internal/lifecycle/memoryclient` restores at most
one current whole unfinished ordinary note. It composes the existing concrete
`Search` and `Pull` on the caller's authenticated SDK. It ports
`current_checkpoint_view`, `current_pull`, `recall_timeout` and
`workstream_prefix` from `integrations/lifecycle/memory.py` (SHA-256
`6c9cafcde3600671ed11e4f49f220c086e33ebf998051969931a9e232059f597`).
Criteria `47876ac5-a694-4a67-8749-b3fd20c4ad6c/v1` and candidate release
`f6542476-f1b6-4681-a1a6-d5f47bb09ab8/v1` define this component's scope.

## Caller and input contract

`CheckpointRefreshInput` contains a caller-resolved `ProjectBase`, a stored
`Workstream` string pointer and the current service result's `Selected`
selections. No filesystem resolution occurs here. The client stays serial and
per invocation. The caller owns SDK `Close`, the enclosing context/deadline,
output fitting and every frontend state or grant change.

Checks are lazy and ordered:

1. Nil workstream returns empty text and reason without inspecting other input.
2. A present workstream requires the entire project basename to be valid UTF-8,
   before removing quotes and clipping at 40 complete UTF-8 bytes. The prefix is
   `Handoff: ` + clipped basename + ` / `.
3. A wrong title prefix returns empty text/reason before title UTF-8 validation.
   A matching prefix requires valid title UTF-8, at most 256 original bytes and
   no CR, LF or quote. Ordinary title refusal returns empty text/reason; the
   title is never stripped or subjected to capture's 90-byte limit.
4. A selected record body beginning with the exact title and LF returns
   `covered_by_required` before context/deadline or unused-record validation.
   This shortcut checks body prefix only, without asserting whole-note status.
5. Only then must the caller supply a nonnil context with a deadline. Missing
   context/deadline and invalid pre-try input UTF-8 return a fixed redacted
   `*core.Error` with code `INVALID_REQUEST`, empty Text/Reason and nil Cause.

A canceled or expired deadline-bearing context is an optional operational
failure. Remaining time and cancellation are checked before each Search/Pull,
so an already exhausted call does not initiate its counters. Each operation
receives the smaller of two seconds and remaining caller time; capability
negotiation and index share the Search cap. No total budget is restarted and
there is no retry.

## Search, identity and whole-body checks

Search uses the quoted title, room 16000, kind `note` and explicit initial
offset zero. It traverses at most three pages. Page existence and matching
offset precede entry checks. Matching entries have class A, kind note, a summary
with title plus LF and no conflicts. Matches accumulate in order without
deduplication. More than one match is ambiguous before any Pull. A continuation
must increase the offset; continuation after page three is incomplete, with no
fourth Search. A sole match is pulled only after traversal ends.

The internal whole-pull helper copies the generated `ExpandRequest`. Its
admitted arguments are exactly the current presenter's three strings:
`request_id`, `receipt_id` and `handle`; Span is nil and OpeningBytes is zero.
The copied request ID is UUIDv5 in the URL namespace over the compact
`ensure_ascii=False`, sorted-key name:

```text
["cairn.lifecycle.whole-pull/1",{"handle":H,"receipt_id":R,"request_id":Q}]
```

Q remains unchanged in the original arguments. SDK protocol guards are added
later and are not part of the seed. The private fixed-shape encoder does not
define generic JSON or number canonicalization.

The raw Pull record must have the same ID and integer version as the entry and
a string body. A nonempty optional body hash checks the original UTF-8 body
before extent or content checks. Identity/hash failures are optional failures.
`source_extent == "partial_span"`, any non-null `span`, a wrong body prefix or
more than 4500 original UTF-8 bytes yields `not_whole`. Raw span presence is
retained: missing/null is allowed, while false or an empty object is non-null.
Other raw extent types do not become typed-decode failures.

Completion checks the text after the title with Python's leading-only lstrip
semantics: Go `unicode.IsSpace` plus U+001C through U+001F. The literal prefix
`Status: complete\n` then gives `complete`. A terminal LF is significant;
capture's both-end strip would erase it and is not used.

The restored text starts with
`Current saved unfinished work (ordinary note, verify against this task):\n`
and compact JSON with keys in order `schema`, `record_id`, `version`,
`source_extent`, `body`. Schema is `cairn.checkpoint-refresh/1`, extent is
`whole`, and the body stays verbatim. String encoding preserves literal UTF-8,
HTML characters and U+2028/U+2029 as Python `ensure_ascii=False` does.

## Result and failure policy

`CheckpointRefreshResult` has `Text`, `Reason` and diagnostic-only `Cause`.
Empty Reason represents Source None. Other reasons are exactly
`covered_by_required`, `incomplete_search`, `ambiguous`, `not_found`,
`not_whole`, `complete`, `restored` and `unavailable`.

Caught optional Search/Pull/consumer failures return the exact Source cue with
reason `unavailable` and nil outer error:

```text
Current unfinished checkpoint was not restored for TITLE; search current Cairn memory before relying on earlier context.
```

Cause retains the inspectable failure and is never rendered, persisted or
converted into a required-context refusal. Existing Search receipt handling,
Pull counters, known-zero preflight and confirmed credit updates are reused.
Credit effects precede later consumer validation; no refund is made.

## Composed Source domain and limits

Python `Memory.search` invokes `cairn agent search`, which prints the same typed
`indexview.View` as `Client.Search`. Page/Selected projection is therefore
shared behavior, not a Source parity gap. The historical preparation's opposite
claim is superseded by criteria MC1. No raw-presence Search interface is needed.

The domain is the current ordinary producer/service Search and Selected shapes,
integer versions, valid Unicode and the generated three-string arguments.
Raw Pull remains raw in both paths. Non-producer numeric version forms, invalid
Unicode and null/non-object selection/record shapes are not claimed to preserve
every Python exception class. Raw extent/span presence semantics remain literal.

Five focused test families have 35 direct subcases, frozen as 40 test names
before authoring. They use private Unix socket fixtures, synthetic credentials
and the real SDK; literal UUID-name, whitespace and restored-text expectations
are independent of the implementation's encoders. Published tests are not part
of that selector. These fixtures establish no production or runtime acceptance.

F-B2 retains full recall frontend ownership. This method does not commit
budget_deferred, ledger/status/seen/digest/markers, required/checkpoint refresh
metering or grants. R1/R2/N4 from F02-M1 remain open. Full F02/F01/F03/F04,
frontends/state/grants/privacy/installed/runtime/kit/campaign acceptance and the
original18e9, failed844 and C2/O-C4/F04/RTERM boundaries remain unchanged.
