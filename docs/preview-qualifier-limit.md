# Preview boundaries and missing qualifications

Previews remain discovery hints. Inspect the current source and verify its
applicability before using advice. A complete-looking sentence does not establish
that omitted paragraphs, currentness constraints or competing positions agree.

## Demonstrated loss

The legacy preview algorithm leaves at most 40 bytes before a query match and
then advances to a whitespace boundary. It maximizes distinct query terms, not
qualification coverage. With twelve repetitions of `Background context. ` before:

> Do not under any circumstances in the production environment reuse cached validation results. More details follow.

query `reuse cached validation results` displays:

> ...in the production environment reuse cached validation results. More details follow.

Its exact source span is offset 271, length 83. Source fidelity does not imply
faithful interpretation: the governing `Do not` is missing. Similarly, a
283-byte semantic passage can lose a trailing condition when its 160-byte
preview stops before the end of the clause.

## Implemented slice

Context indexes default to `cairn.preview-boundaries/1`. Marker-aware callers
can opt in with `compact_preview_entities: true`; those indexes seal
`cairn.preview-compact/1`, combining the metadata fallback below with the existing
boundary expansion. False and omitted flags have identical request semantics.
The flag is invalid outside a context index. Historical receipts retain their
original presentation, including absent identifiers.
After admission and final total-budget trimming have finished, the
compiler considers each admitted advisory preview in its existing order:

1. Find simple sentence boundaries surrounding its existing source span.
2. Keep **every byte of the existing source span**, adding only contiguous source
   before or after it. Preserve the body hash and exact byte locator.
3. Require the result, including omission markers, to fit 160 UTF-8 bytes.
4. Accept only if the exact encoded entry cost fits remaining optional room and
   the final rendered index charge fits remaining total room. Explicit
   `memory_budget_bytes` retains its conservative MCP string-escaping charge.
5. Otherwise leave the original preview and locator unchanged.

The pass never adds, drops or reorders admitted records, changes paging or
omission counts, or reallocates a later record's bytes. It leaves mandatory
instructions, class C previews and competing-position previews untouched.
Semantic `match_span` continues to locate the complete scored passage, even when
`summary_span` expands. Full instructions and conflicting positions still require
whole pulls; ordinary pulls still revalidate access and currentness.

For the short-negation example, an ordinary optional allocation of 800 bytes
allows the missing sentence opening to be added while retaining `More details
follow.` The exact saturated allocation of 445 bytes retains the old preview.
A complete 93-byte sentence alone would cost 458 bytes; even the minimum
contiguous `not` plus complete query phrase interval costs 454. Neither fits 445,
and replacing the old excerpt could remove already visible context. The new
policy therefore does not promise a repair at that boundary.

Boundary detection is deliberately limited: ASCII `.`, `!`, `?` terminate only
before whitespace or the body end; `。`, `！`, `？` are also recognized. Internal
punctuation in paths, decimal numbers and configuration keys is not a boundary.
Proposed spans containing backticks or double quotes are left unchanged. There
is no language parser, abbreviation resolver or negation/scope classifier.
Long sentences, cross-sentence conditions, already full previews and ambiguous
punctuation can remain truncated. No new incomplete-context label or automatic
body delivery is introduced.

Expanded previews spend real receipt room and can leave fewer bytes for later
pulls. The configured caps and credits are unchanged; the returned remaining
bytes reflect the expanded index. Across separate receipts, retries and outer
host wrappers, callers must continue to account for aggregate task context.
The existing lifecycle lexical fallback reads forward from `summary_span.offset`;
an earlier preview start can shift that bounded source read earlier. Monotone
containment applies to the displayed preview, not every byte of a later fallback
window. Semantic fallback still prefers the unchanged `match_span`.

## Compact preview metadata

A preview's optional-policy charge includes its serialized metadata and handle
allowance, so attached entity associations can push a higher-ranked preview
above a small optional ceiling even when its source body fits. Both unpaged and
`offset: 0` calls skip an entry that exceeds the optional ceiling.

Before omitting a standalone optional A/B entry for `OPTIONAL_BUDGET`, the new
policy tries removing its `entities` list and setting `entities_omitted` to the
number removed. It uses that form only when its exact serialized cost, including
the marker and existing handle allowance, fits the optional ceiling and is smaller
than the full entry. Admission still checks the remaining allocation; a paged
result defers a compact entry that can fit only on the next page. Final total
packing still applies, including
explicit memory budget escaping charges and any minimum pull reserve. This does
not raise any cap or guarantee that the later body fits.

The compact form preserves the ID, version, class, kind, body digest, summary,
`summary_span` and `match_span` exactly. It skips subsequent sentence expansion;
other entries retain the existing boundary behavior. The marker means that
association details were omitted, not that the source has no associations. A
checked pull returns the source's full association metadata. Stored associations,
eligibility and ranking are unchanged. Mandatory selections, class C entries and
conflict/competing groups never use this fallback. Source applicability still
requires inspection, and caller-supplied associations remain fallible hints.

The change can affect which optional records and page offsets are delivered.
That change is versioned; it is not applied to retained old-policy receipts.
Compaction only addresses optional admission pressure. If total-envelope packing
later drops an entry, this implementation does not run a second compaction pass.

## Replay and compatibility

The presentation identifier is an optional canonical-CBOR field. It is not a
public JSON field and does not itself consume display room. Absent identifiers
preserve the original canonical bytes and legacy preview path. Recompilation
uses the saved policy, facts and body versions; it must reproduce the original
seal even after a later edit. Unknown policy identifiers fail integrity checks.

An older API reader cannot accept `cairn.preview-compact/1`: it either loses an
unfamiliar sealed field and fails the canonical seal check, or rejects the
unknown presentation identifier (`INTEGRITY_FAILURE`). Clients that drop
`entities_omitted` do not opt in and receive the full-entity presentation and
policy. Full pull handles remain unchanged.

The authenticated version response separately declares
`preview_capabilities: {"schema":"cairn.preview-capabilities/1","entities_omitted":true}`.
`agent search` and `agent start` probe it once per invocation; the MCP facade
probes once before its first search/preparation, or reuses its first successful
`cairn_client_info` version response. Native OpenCode search and preparation
use the CLI and preserve the marker. Missing, malformed, duplicate, extra-field
or unknown-schema declarations do not opt in. A probe transport/authentication
failure is an error, not permission to try a different request. Build revisions
and protocol version are not capability evidence.

A live MCP facade keeps its initial choice, including an absent/unknown choice,
for all later searches, preparations, pages and retries. Raw `localapi.Client`
calls, `agent index` and ordinary whole-context compile/run do not automatically
opt in. An explicit caller must preserve the omission marker before setting the
flag; this changes no access, mandatory-selection or byte-budget rule.

If the API does not support `compact_preview_entities` after a positive choice,
the request fails rather than resending with the flag removed or a new UUID. A
CLI retry across invocations whose negotiated choice changed returns
`IDEMPOTENCY_CONFLICT`; reconcile and explicitly choose a fresh request UUID.
The opt-in is part of the existing request digest and presentation is part of
the canonical seal. Same-choice retries retain their receipt and accounting.
Other compiler changes can still produce `STALE_PACKAGE`. Historical replay
never negotiates a new presentation. This bounded presentation mechanism does
not establish general qualifier preservation, better ranking, or improved task
outcomes.
