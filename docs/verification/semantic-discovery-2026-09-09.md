# Integrated semantic discovery, 2026-09-09

Optional semantic discovery now runs through Cairn's actual eligibility, index
packing and source-pull path. In a disposable store, all fifteen labelled answers
in the existing documentation workload survived within three returned previews.
The default lexical route returned thirteen within three. This is a working
retrieval capability; it does not establish independent model task benefit.

## Implementation and boundaries

`semantic: true` opts a context index request into a host-configured scorer.
The core passes only eligible optional notes. The local CPU worker scores full
notes through overlapping token windows and returns version/body-bound integer
similarities. Mandatory instructions are never sent for model ranking. Scope,
destination, currentness, authority, packing and expansion remain core decisions.

The core validates the exact returned set, pins model/algorithm identity and
seals a digest of every candidate score. Recompilation validates and uses those
retained scores rather than invoking a model. Requests use schema 7; default
queries and old receipts retain their prior schemas. Worker failure uses labelled
lexical fallback. Decoded invalid score sets are rejected in full and recorded
as `invalid_result` in the receipt. The
[operator guide](../semantic-discovery.md) specifies setup and resource limits.

## Actual CPU/API comparison

The experiment used the same twelve public documentation passages and seventeen
questions as the [offline comparison](semantic-retrieval-2026-09-09.md), with
one insertion order and 64,000 available input bytes/tokens under Cairn's
conservative byte-based accounting. These are authored development labels; only
`storage?` came verbatim from the user. Each condition used the authenticated
agent CLI against an actual Unix API and disposable PostgreSQL store. No answering
model, operational notes or hosted inference provider was involved.

| Route | First, out of 15 | Top three, out of 15 |
| --- | --- | --- |
| Default lexical | 11 | 13 |
| Optional semantic | 11 | 15 |

The storage-topic query changed from no lexical result to semantic rank 2. Its
exact full source body was then pulled through the returned handle. The data
location answer moved from rank 11 to rank 1. Both no-answer controls still got
semantic neighbours; there is no calibrated refusal threshold or answer claim.
The existing first-place regressions for some precise queries remain visible in
the per-query metadata.

Across the fifteen answerable questions, lexical requests took 0.20 seconds total
and semantic requests 67.06 seconds total on this host. The slowest semantic
request took 4.73 seconds. This worker starts a process and loads the model on each
request; it adds no persistent embedding service or note-derived vector cache.
These observations show a material latency cost, not a throughput benchmark.

A separate synthetic 14,708-byte note placed the requested credential-location
guidance after repeated introductory text. It ranked first for that question,
and its full body was pulled exactly. The example path is synthetic. This checks
that this long note's final guidance remains reachable; it is not evidence about
every 64 KiB note or real credential retrieval. Existing body-pull budgets remain
in force. Replacing the fixture's worker with an executable that exits failed
produced the expected labelled lexical result for an exact identifier query.

## Verification

- PostgreSQL/race tests verify that hidden, out-of-scope and revision-mismatched
  notes never reach the scorer; mandatory instructions remain selected. They
  check exact source pulls, stale-handle refusal after editing, frozen-score
  recompilation after that edit and integrity refusal after score corruption.
- Fallback tests preserve lexical results and distinguish unavailable workers
  from invalid returned identities. A tight-budget check includes the full
  degraded-status banner before accepting the envelope.
- Worker tests verify stripped credentials, bounded output, one JSON response,
  busy refusal and cancellation of a running process. The optional real worker
  supplies the CPU/API results above.
- MCP and native OpenCode checks exercise the new argument through ordinary
  authenticated access and verify fallback and incompatible browse arguments.
  They start no answering-model turn.
- Five packages written by the previously installed binary recompile under the
  candidate with identical packages and seals, covering schemas 5/6, exact and
  split identifiers, a question-only query, a vocabulary miss and browsing.

The first native check caught an argument-order error: adding the semantic flag
had attached an `else` to the wrong condition and omitted its query. That was
fixed before installation. Review also found that an embedded `bytes.Buffer`
could expose `ReadFrom` and bypass the output bound, and that changing the status
banner after packing could exceed a tight budget. Both were corrected and their
specific boundaries checked. The first legacy probe compared the CLI's historical
wrapper to its contained package; the corrected probe compares the actual
packages. Original failed logs remain retained.

The local backend has been prepared with `scripts/install-semantic.sh`.
Preparation alone does not enable it in the running API.

The reusable `scripts/check_semantic_api.py` check was then run through
`make test-integration` with the prepared worker and native OpenCode enabled.
It reproduced the same ranks, exact storage and long-note pulls, and fallback
after restarting only its fixture API without a scorer. Across its fifteen
answerable questions, lexical requests totalled 0.21 seconds and semantic requests
71.80 seconds; the slowest semantic request took 5.71 seconds. Four actual
semantic receipts from the first run also recompiled with identical seals under
the final core source without a scorer, covering ready, no-answer, long-note and
fallback cases. Neither run establishes answering-model task benefit.

The implementation decision used validated doctrine packet
`pkt-29933b0eb78e3aef`. Its receipt retains two nonmaterial generic procedure
obligations; the explicit behavior boundary, alternatives and consumer checks
are recorded directly. This supports the bounded implementation decision, not
full roadmap acceptance.

[Metadata](semantic-discovery-2026-09-09.json) retains source/binary hashes,
per-query ranks, timing, model identity and local evidence pointers. Raw fixture
store dumps and tool output remain outside Git. Broader task use, independent
benefit, large-corpus latency and concurrency beyond the stated fallback contract
remain open.
