# Question-word retrieval change, 2026-09-08

The query “What is the monthly subscription price?” previously returned a
run-status passage solely through “what.” Ranker `lexical-scope-recency/4`
removes that false positive. It filters the fixed words `what where when why who
whom whose which how do does did` in addition to the existing function words.
Negation, obligation words and complete underscore identifiers remain searchable.
This is a measured change to lexical matching, with mixed ranking effects.

## Comparison

The unchanged [development workload](../../core/testdata/retrieval-quality.json)
contains twelve documentation passages, fifteen answerable questions and two
no-answer controls. Baseline commit `26b43d1` uses ranker v3. Both versions ran
through the public compiler in disposable PostgreSQL with identical scope and
budgets. The two insertion orders expose recency ties; they are dependent variants.

Counts below are out of fifteen answerable questions. Returned means the labelled
passage survives packing anywhere in the result.

| Order / mode | First, v3 → v4 | Top three, v3 → v4 | Returned, v3 → v4 |
| --- | --- | --- | --- |
| Forward / body | 11 → 11 | 13 → 13 | 13 → 13 |
| Forward / index | 11 → 11 | 13 → 13 | 14 → 14 |
| Reverse / body | 9 → 10 | 12 → 12 | 13 → 14 |
| Reverse / index | 9 → 10 | 12 → 12 | 14 → 14 |

The price question now returns zero notes in all four conditions. The lost-reply
question returns three notes instead of five, keeping its labelled answer first.
The citation-count question returns two instead of four, also keeping its answer
first. Reverse-order replacement improves from third to first; the scope answer
now survives reverse body packing at fifth.

One answer rank worsens: the storage-location passage falls from seventh to
eleventh in the forward index. Its reverse rank stays fourth; it remains absent
from forward body packing. The query `storage?` still returns nothing because
the passage uses different vocabulary. The GPU no-answer control still returns
unrelated notes. The [comparison data](question-words-2026-09-08.json) retains all
returned labels, answer ranks and rendered sizes, including these failures.

These are authored development questions already used to tune retrieval.
They establish the stated changes on this workload, not general retrieval quality,
independent relevance judgments, model task acceptance or cross-harness benefit.

## Compatibility and verification

Public regression tests first reproduced the unwanted question-only matches in
body and index modes. They now pass, retaining mandatory instructions, matching
answers, explicit empty-query browsing and sealed recompilation. Token tests
preserve v1–v3 semantics, negation, obligation words and full identifiers.
`make test-integration` and `make check` pass, including the actual Unix API probes.

Ten packages produced by an actual v3 binary recompile through v4 with identical
packages and seals. They cover both modes, unanswered and question-only queries,
whole and split identifiers, and an empty query. As with earlier ranker upgrades,
reusing a compile request ID across the upgrade returns `STALE_PACKAGE`; issue a
new request to retrieve current context. Historical recompilation remains available.
The scratch compatibility probe initially expected identical compile retries;
source inspection and the observed refusal corrected that expectation.

The change adds no migration, dependency or request field. Scope, authority,
destination, mandatory-context and packing rules remain unchanged. Filtering can
lose relevance when a removed word is itself the subject, such as an explanation
of the word “when”; complete identifiers remain available.

The decision used Pincite packet `pkt-c518faadc5a0aa51`, with full identities in
the comparison JSON. Repository precedence, evidence before intervention and
preservation of historical semantics informed the change. The local decision
records 26 nonmaterial obligations concerning unchanged schema, interfaces,
configuration, operations and broader review procedures. Deployment is recorded
separately from these source-level checks.
