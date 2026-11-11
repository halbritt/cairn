# F03 provider-selected capture frontend

The unbound Go memory entry now connects the existing capture admission,
transcript snapshot and locks to checkpoint/candidate retrieval, a selected
provider subprocess, `memoryclient.CommitCaptureSelection`, and the existing
final state CAS. This unit does not enable a bound capture callback or change
installed hook rows. Configurations without a `claude` selector remain at the
explicit substantive-capture unavailable boundary.

The process oracle is the complete retained Python frontend at
`integrations/lifecycle/memory.py`, SHA-256
`0e1076bf999f880b09923783985206985fe093f1b4ee7a348ceb5ae968e1c1c2`
on base `1bda72a657b4fa5ccaeb2e09ad62a1af8a950bf0`. Its capture, candidate,
selector and selected-write bodies run unchanged. The paired fixture substitutes
only an external local selector executable and a Unix API service. No real
provider is called.

The selector gets the Source prompt/schema and excerpt, with hooks, tools,
settings sources and MCP disabled in the same argv. It inherits the caller
environment with `CLAUDECODE` removed and `CAIRN_LIFECYCLE_CHILD=1`, and runs in
a fresh, subsequently removed empty directory. Its direct child is cleaned up
on failure. The 35-second deadline is also bounded by the caller context; each
output pipe is read concurrently and capped independently at 1 MiB plus one
overflow-detection byte. Stdout must be UTF-8 JSON object data. Process errors,
reported provider errors and invalid selections do not advance capture state.

The supported selected-write domain remains MC1 from
[f03-capture-commit.md](f03-capture-commit.md): valid Unicode and RFC JSON,
string record IDs and integer versions. Pulled versions must fit Go's `int`;
invalid types and overflow refuse before provider execution, without conversion
to zero. Non-RFC selected values and unpaired surrogates are domain refusals.
The provider output is not accepted as a caller-supplied capture result.

A stored workstream must be a string when present. Integer, Boolean, null,
list and object values raise `TypeError` before checkpoint discovery or the
selector, preserve the saved state and retain the Source error report. An
absent workstream still uses the derived session title; an empty string still
performs the empty-title checkpoint lookup.
The string must also contain valid Unicode before conversion to Go text.
Raw stored lone-surrogate escapes receive a stable Go domain refusal before
checkpoint retrieval, selector execution or state mutation. This deliberately
refuses low surrogates U+DC80 through U+DCFF too: Python can pass their bytes
through `surrogateescape`, whereas this Go consumer accepts valid Unicode.
The resolved title must also be NUL-free, including a title derived from an
explicit event workstream. NUL is a valid Unicode scalar but cannot occur in
Source's subprocess arguments. Go refuses before `Checkpoint`, preserving
the exact stored bytes and the existing post-lock error observation. Source
raises `ValueError`; Go reports its fixed `HookError` NUL diagnostic. These
malformed inputs do not claim equal diagnostics.

At this JSON-string conversion, non-scalars and NUL are the content-level
argv exclusions. Other scalar controls, quotes, backslashes, Unicode
noncharacters, scalar-range boundaries, separators, U+FFFD and astral text
retain the tested exact Source queries within the shared input/resource
limits. Out-of-range code points cannot be expressed by JSON Unicode escapes;
invalid encoded JSON is a reader boundary, not this string conversion.

Owner decision `98d46758-c61c-42b4-9912-76cf9dfb0dc0/v1` supplies the three
input classes: Python-writer and recorded hook inputs require paired exact
behavior, malformed/hostile data requires a stable safe Go refusal, and resource
exhaustion/interpreter limits require documented Go behavior. The inherited
lossless reader uses unlimited integer digits; the selector's bounded output
and deadline do not establish all-input CPython resource parity.
In particular, a stored title has no new arbitrary length cap here. Linux
per-argument `MAX_ARG_STRLEN` and aggregate argv/environment bounds can make
Source fail with `E2BIG` before its child starts. Go does not construct that
argv and continues through the existing client request/transport limits.
The fixture's 131070-byte ASCII title fails before any Source API call; Go
performs its version probe and then refuses the search through existing
request validation. This is a class-c ordering/resource difference, not a
claim that Go always accepts long titles. Aggregate bounds also depend on
the environment and stack limit; no exact all-input parity is claimed.

The existing client plans every write before the first mutation. Creates and
revisions retain Source order and UUID identities. A confirmed earlier write
survives a later failure, while the frontend digest and markers remain
uncommitted. A successful final note, including a no-op, supplies the
workstream. The existing final CAS merges independent recall fields and refuses
capture/binding conflicts; API writes cannot be rolled back by that refusal.

The capture report runs after the capture and shared locks are released.
Selected model names, duration, usage and cost pass the Source filters. Reports
are best effort and never change capture success or retry eligibility. SDK
diagnostics report the actual Go image hash; the Source CLI reports the Python
body hash. Those implementation IDs intentionally differ.

The selection fixture has 29 test methods: 25 execute 43 complete Source/Go
pairs. Four other methods cover three malformed pulled-version refusals,
four raw stored lone-surrogates, eight stored/explicit NUL cases, and the
long-title resource difference, with separate Source observations. The pairs
cover null selection, new durable/checkpoint writes, revision, no-op,
candidate discovery and budget refusal, prewrite invalidation, provider
protocol/UTF-8/output-limit failures, timeout, bounded metadata, partial writes
and retry reconciliation, concurrent state conflicts and merges, Codex snapshot
markers, and Unicode writes for Claude and Hermes. Three focused Go process
tests cover an expired caller, a retained descendant pipe, and partial-output
accounting at timeout. The original capture boundary fixture remains unchanged.

Passing fixtures establish the tested frontend relation, not provider
usefulness, server idempotency/CAS, runtime acceptance or permission to activate
the entry. Rollback is to the retained Python hook path; no live configuration
is changed by this unit. Foundation owns review and integration of this candidate.
