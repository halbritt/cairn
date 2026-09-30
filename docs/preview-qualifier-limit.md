# Bounded preview boundary expansion (CAIRN-112)

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
faithful interpretation: the governing `Do not` is missing.

`core/preview_qualifier_test.go` retains these legacy characterizations, including
long trailing conditions, code/path punctuation, Unicode and a preceding-sentence
control. A 283-byte semantic passage also loses a trailing condition when its
preview displays only 154 source bytes. These constructed cases establish a
possible failure, not its prevalence or measured harm on an owner task.

## Implemented slice

New context indexes seal presentation policy `cairn.preview-boundaries/1`.
After the existing admission and final total-budget trimming have finished, the
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

## Replay and compatibility

The presentation identifier is an optional canonical-CBOR field. It is not a
public JSON field and does not itself consume display room. Absent identifiers
preserve the original canonical bytes and legacy preview path. Recompilation
uses the saved policy, facts and body versions; it must reproduce the original
seal even after a later edit. Unknown policy identifiers fail integrity checks.

An older API reader that does not know this sealed field cannot read new-policy
receipts: dropping it fails the canonical seal check (`INTEGRITY_FAILURE`).
After an API rollback, perform a fresh search to obtain an old-policy receipt;
do not reuse an unreadable handle. Existing JSON MCP facades do not seal these
packages and continue to pass search results and complete pull arguments.

The existing request-UUID contract also remains: a search recalculates its
package before accepting an idempotent retry. If a compiler policy changes its
seal across upgrade or rollback, retrying the old UUID yields `STALE_PACKAGE`,
even when source records are unchanged. An explicitly fresh request UUID obtains
a new receipt. Same-policy retries retain their original receipt and accounting.
This change does not silently reinterpret or overwrite earlier requests.

## Verification and limits

The focused tests cover the ordinary-budget improvement, exact 445-byte fallback,
later-record admission, exact total accounting with and without the explicit
memory budget, path/Unicode handling, visible-source containment, semantic
locators, currentness, new and historical replay, unknown-policy rejection, and
request retry behavior. Native MCP tests check escaped search plus checked-span
result envelopes and preservation of whole mandatory context. Existing conflict
and instruction tests remain part of the disposable integration gate.

This is a bounded presentation improvement. It does not establish general
qualifier preservation, better retrieval ranking or improved real task outcomes.
No model/provider experiment or production rollout is part of this slice.
