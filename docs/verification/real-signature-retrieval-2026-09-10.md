# Retained failure lesson retrieval — 2026-09-10

The installed signature lookup retrieves the lesson from a real failed repair
after a new review pins its exact version. Ordinary text search already ranks
that lesson first for all three queries checked. This case supports the retrieval
contract but shows no ranking advantage and adds no model-task benefit evidence.

## Source and method

The source is the [observed model failure](observed-model-failure-2026-09-08.md),
preserved in its reviewed trial dump. Its rejected environment repair produced
proposal `f41b3559-59db-428f-92c2-3ba77bd39345` and local A lesson
`16a90b22-ae29-436c-a73f-343c7a2dc273` version 1. The source assessment is version
2. The lesson remains fallible, local and unpromoted.

The check restored a private copy into disposable PostgreSQL and migrated it
through 033 using clean Cairn `61627b3`. It verified the eight original artifact
hashes before and after, checked the retained evidence digest, and preserved the
source assessments and lesson. The operational store was not used. A private
verification dump was retained before the disposable cluster was removed.

The lesson applies to revision `9757fddf68ba3d731a9e3d874e601a0ce2ce17af`.
Searches declared that historical revision with different task/run and execution
labels. Those labels are test inputs, not observed agent executions. This is a
post-review historical recurrence check, not original-time availability or advice
for the current code revision. No answering model or repair task ran.

## Observed results

| Check | Result |
| --- | --- |
| Legacy converted review, with historical context | No signature match; its lesson version remains unknown. |
| New review version 3, explicitly linking lesson version 1 | Signature-only index returns the lesson; exact local body pull succeeds. |
| Missing revision | Index succeeds but omits the lesson with `CONTEXT_MISSING`. |
| Different revision | Index succeeds but omits the lesson with `CURRENTNESS_MISMATCH`. |
| Hosted destination with matching historical revision | Local lesson remains excluded. |
| Earlier and later signature search receipts | Both recompile exactly after the new review. |
| Original trial, source assessments and lesson | Unchanged. The new review exists only in the verification copy. |

The three text queries were chosen from the public failure description before
the successful comparison. Each returned the lesson at rank 1:

- `explicit environment overrides lost`
- `inherited GOCACHE survives`
- `child process ignores requested environment`

These are diagnostic queries on one small retained corpus, not an independent
benchmark. Equal first rank here does not establish general equivalence between
text and signature search. No latency, context-cost or review-cost improvement
was measured.

## Probe corrections

The first attempt used the operator CLI to inspect an observer-owned assessment
and received `AUTHORITY_DENIED`. The corrected probe respected that boundary and
used a local read-only SQL digest solely to check preservation of the restored
assessment history.

The next attempt omitted the historical revision and expected a match after the
new review. The explanation instead showed `CONTEXT_MISSING` for both relevant
records. Supplying the lesson's actual revision resolved that probe error. The
final check also verifies missing and mismatched revision exclusions. It neither
broadens the lesson's applicability nor changes the retrieval implementation.

## Next action and limits

Leave signature retrieval unchanged on this evidence. Use the existing ordinary
interfaces during real work and revisit this feature when a relevant retrieval
miss or observed task cost gives a reason to do so. Cumulative, qualitative and
indirect benefit remain valid evidence; a single-turn mechanical win is not a
prerequisite. E1, E4, D1 and D2 remain partial.

The [metadata manifest](real-signature-retrieval-2026-09-10.json) records exact
identities, receipts, source hashes and private scratch locators. It contains no
lesson body, assessment body or raw model output. Scratch paths are retention
pointers, not a durable archive.

Pincite's validated release supplied final packet `pkt-1ddf7666ccf3178f` with
typed evidence, a validated decision receipt and closed citation traces. The
packet offered general investigation guidance, with no precisely nominated
specialist concept for this question. Its 15 unmet obligations are individually
retained as nonmaterial in the manifest: no product repair, new service contract,
structural change, abstraction or asynchronous behavior is proposed here.
