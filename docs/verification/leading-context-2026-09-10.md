# Long-note prefix ranking screen — 2026-09-10

Keep lexical v4. A fixed prefix tie-break improves the dedicated-note rank on
this small comparison, but most questions were authored from note subjects and
favor the proposed change. The existing semantic route resolves the original
Codex setup query when narrowed to procedures. Its unfiltered cold cost warrants
investigation before another default ranking change.

## Observed problem and fixed comparison

At source `5537557`, installed CLI/API `9b888e1` returns the OpenCode setup
procedure v15 first for `Codex setup configuration`, followed by the Codex
procedure v9. Both bodies match all three query terms; equal scope leaves the
newer OpenCode edit ahead. The appropriate note is visible second. No wrong
answer or downstream task failure was observed.

Ordinary hosted browse and complete pulls retained all twenty eligible notes
for this task without context pins: 57,544 body bytes, individually 586–11,743
bytes. These are shareable Class A notes with repository-wide task/run scope.
The first collection attempt exhausted a receipt's expansion budget; collection
continued with two whole pulls per page. Current source hashes and versions were
checked again during the subsequent live baseline comparison.

The plan fixed twelve questions and a dedicated target note for each before
scoring. Two queries came from the setup review, `storage?` repeats the owner's
earlier wording, and nine are investigator-authored subject diagnostics. A target
label identifies the dedicated note, not every possibly useful answer source.

The sole candidate adds distinct query-term matches in the first 160 body bytes
as a tie-break after existing whole-body lexical score and scope specificity,
before recency and record ID. The prefix ends at a complete UTF-8 character,
matching the existing preview boundary. No parameter or question was tuned after
scoring. No earlier public development cohort was rerun.

All twelve complete baseline ranked lists match the current paginated API,
including selected versions and body hashes. Reversed recency is a sensitivity
check over the same notes, not a separate live trial.

| Dedicated note | Current recency: baseline → candidate | Reversed recency: baseline → candidate |
| --- | --- | --- |
| Codex setup | 2 → 1 | 1 → 1 |
| OpenCode setup | 1 → 1 | 1 → 1 |
| Earlier note versions | 3 → 1 | 1 → 1 |
| Task-phase context | 3 → 1 | 2 → 1 |
| OpenCode Unicode validation | 1 → 1 | 2 → 1 |
| Semantic worker | 2 → 1 | 1 → 1 |
| Evidence spans | 2 → 1 | 1 → 1 |
| Storage | 2 → 1 | 1 → 1 |
| Implementation history | 3 → 1 | 1 → 1 |
| Qualitative task value | 2 → 1 | 1 → 1 |
| Agent connection | 1 → 1 | 1 → 1 |
| Striatum host capture | 2 → 1 | 1 → 1 |

The candidate improves nine ranks with actual recency and two with reversed
recency, without a target-rank regression here. This is useful development
evidence, but does not establish generalization. Questions about buried details,
boilerplate prefixes, incomplete prefix words, and differently structured notes
remain untested. Eligibility, mandatory instructions, quotes, failure signatures,
packing changes and historical replay were not candidate integration tests.

## Existing semantic route and an availability limit

Unfiltered semantic search for the original Codex query returned
`discovery.state: unavailable` and the labelled lexical fallback. The API service
was running. A separate invocation of the installed worker scored the retained
twenty-note corpus successfully in **20.015 seconds** and ranked Codex first,
with the installed model fingerprint
`057e65f9a513a73360112a43676db6aff5faf047e6822d1302ffc2c6de6b4a3b`.
The [API worker deadline](../../semantic/stream.go) is twenty seconds. This timing
is consistent with deadline pressure; the specific cause of the live fallback
was not captured and remains unconfirmed.

The existing `--semantic --kind procedure` route reduced the eligible input to
seven procedures, 28,630 body bytes. Two sequential actual API calls returned
`READY`, `discovery.state: ready`, identical score digests, and Codex first:
**10.181 seconds**, then **0.060 seconds**. Pulling that first result returned the
exact current v9 Codex procedure and its expected body digest. These observations
do not establish latency percentiles or an answering-model task benefit.

Native tools express the same optional filter as `semantic: true` and
`kinds: ["procedure"]`. Labels are fallible, so narrowing can exclude useful
notes; use it when the task calls for that kind, inspect discovery state, and
broaden if needed. This is an existing capability, not a new default.

## Decision, continuity and verification

Do not ship prefix ranking or tune these twelve questions. Revisit the candidate
on naturally arising questions about details within long notes. The immediately
actionable finding is cold semantic cost near the current deadline: inspect that
workload and preserve full scoring behavior before choosing an intervention.
No timeout, model, executable, configuration or ranking profile changed here.

The existing semantic-worker guidance was updated through native MCP from v5 to
v6. The body-only edit preserved every prior byte and all metadata other than
version/write time; an identical retry and fresh current pull were checked.
The added observation is 809 bytes, so this maintenance also adds retrieval and
reading cost. The retained corpus and measurements precede that edit. It is
continuity work, not evidence that the cold-cost issue is fixed.

The [metadata](leading-context-2026-09-10.json) records the fixed questions, ranks,
source identities, observations and private artifact hashes. Full operational
bodies, tool responses and comparison scripts remain under
`/tmp/cairn-leading-screen/`, outside Git. This is a locally reproducible screen
while those artifacts remain available, not a distributable public benchmark.

Validated doctrine packet `pkt-a4ee4c1b083d3e88` supports the narrow conclusion.
Representative comparison remains a material unmet requirement for production
selection; the decision receipt explicitly abstains from that selection. Typed
evidence and receipt schemas validate, and citation consumption is closed. The
docs-only repository change needs no store or native integration rerun; checks
cover reported values, local source references, links and preserved implementation
history. No answering-model execution or new task acceptance is claimed.
