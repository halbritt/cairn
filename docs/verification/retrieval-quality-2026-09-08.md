# Documentation retrieval quality, 2026-09-08

Cairn now retrieves a note about `CAIRN_HOME` when asked for `cairn home`,
without relying on that note being newer than other Cairn notes. This is a
measured retrieval improvement on a small development workload. It does not
establish better task outcomes, transfer, or benefit across harnesses.

## Workload and baseline

Commit `aba227dddabf5f2c7c317e235c83bc6c36b33cf6` froze the
[workload](../../core/testdata/retrieval-quality.json) and measurement code before
the first run or ranker change. It contains twelve exact passages from Cairn's
documentation at `754d088468d89d2fad0c8d453ec8bd677a5c642f`, fifteen questions with
one labelled answer each, and two questions whose answers are absent. Only
`storage?` is a verbatim user question. The other questions and relevance labels
were authored for this experiment; this is not an independently judged benchmark.
The four storage questions also make the topics unevenly represented.

The probe creates ordinary local A notes through the public store in disposable
PostgreSQL. Every note has the same repository-wide scope. It exercises full
context and index compilation with 64,000 available tokens and the default 6,000
optional limit, then reads the protected explanation. Titles, filenames and
relevance labels are not added to the searchable body. The two insertion orders
reverse relative recency to expose tie sensitivity. They are controlled variants,
not independent samples. No operational memory or model is involved.

The baseline uses `lexical-scope-recency/2`: distinct shared words, followed by
scope specificity, recency and record ID. An underscore stays inside a word.

## Result

Each count below is out of the fifteen answerable questions. “Returned” means
the labelled answer survives packing anywhere in the delivered result.

| Order / mode | First, v2 → v3 | Top three, v2 → v3 | Returned, v2 → v3 |
| --- | --- | --- | --- |
| Forward / body | 10 → 11 | 12 → 13 | 13 → 13 |
| Forward / index | 10 → 11 | 12 → 13 | 14 → 14 |
| Reverse / body | 9 → 9 | 12 → 12 | 13 → 13 |
| Reverse / index | 9 → 9 | 12 → 12 | 14 → 14 |

Only the answer rank for `CAIRN HOME` changed: body rank 6 → 1 and index rank
8 → 1 in forward order. It was already first in reverse order. No other labelled
answer moved up or down. Exact `CAIRN_HOME` stayed first in every condition.
The [comparison data](retrieval-quality-2026-09-08.json) retains returned IDs,
answer ranks, omission reasons, rendered sizes, aggregates and source hashes.

Several failures remain:

- `storage?` returns nothing: its answer passage uses “database,” not “storage.”
- “Where does Cairn keep its data?” ranks its answer seventh or fourth in the
  index. Forward body packing omits it entirely.
- “What permits applying a record to more tasks?” ranks its answer first or
  seventh depending on insertion order; reverse body packing omits it.
- Both no-answer controls return unrelated notes in every condition. The monthly
  subscription question retrieves the run-status passage through the word “what.”

These observations justify investigating vocabulary mismatch, common-word noise
and recency ties separately. They do not justify declaring the ranker good, nor
do they establish which broader ranking method would help. This workload has now
been used for development; future generalization claims need fresh questions and
a task-level comparison.

## Change and verification

Ranker v3 adds nonempty underscore components and retains the complete token.
It preserves the existing function-word filter and negation, scope and authority
gates, destination checks, budgets and tie ordering. The change is shared by
compilation and historical recompilation, with no schema or public request change.
Exact identifier queries may now return notes sharing only a component; that
recall/precision tradeoff remains visible rather than being called exact-only search.

The public identifier preference test failed before the change and passes after
it for full bodies and index pointers, including sealed recompilation. Literal
token tests protect v1/v2 semantics. Four actual receipts written by the installed
v2 binary recompile under the candidate with identical packages and seals:
plain identifier words, the exact identifier, function words, and punctuation.
The candidate's new plain-word query then selects the intended note first.

`make test-integration` and `make check` pass, including PostgreSQL race tests
and actual CLI/Unix API checks. Two existing overlap-count assertions changed
from one to three for `fixture_error` (whole token plus its two components).
A transitive-use test's supposedly distinct sentinels shared `_dependency`;
replacing them with distinct single-word markers preserved its one-exposure
invariant. The original failures are retained in the local test log.

Reproduce the current measurement with:

```sh
CAIRN_RETRIEVAL_REPORT=/tmp/cairn-retrieval-report.json make test-integration
```

Run the same command from a separate checkout of `aba227d` for the v2 baseline.
The first baseline report was accidentally overwritten by a scratch evidence
file; the retained baseline is a repeat on unchanged code before implementation,
which reproduced the observed rankings. A legacy probe initially misparsed the
successful migration response; its corrected run supplies the compatibility proof.

The implementation decision used Pincite packet `pkt-3e9c00cee4ef2c67`, SHA-256
`3e9c00cee4ef2c670a54f8c11cf5c4eaa962dac8d11ddc50f84badc04b922bb0`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, retriever
`retriever-ec995ecdd083b2c8`. Repository precedence, evidence before intervention,
population scoping and rank-before-truncate informed the experiment. The local
decision receipt records 23 nonmaterial obligations concerning unchanged identity,
authorization, interface and presentation surfaces, named procedures, and operational
parity outside this source-level experiment. Deployment is a separate observation.
