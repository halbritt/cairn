# Value case: recalled guidance during an adapter repair

Cairn plausibly helped focus an investigation that repaired a real defect. A
saved lesson about OpenCode argument defaults was retrieved, checked against the
execution paths, and extended into a finding about missing argument validation.
The resulting repair prevents malformed sharing flags from creating shareable
notes. This is a qualified example of useful work with memory in the loop.

The repair is directly supported by retained reproduction and verification
artifacts. Memory's contribution to choosing and developing the investigation is
an inference supported by the retrieved lesson and the contemporaneous work
report. There is no estimate of the improvement over doing the same work without
memory, or of net benefit after retrieval, investigation and maintenance costs.

This review was written by the Cairn coding agent on 2026-09-09 under the owner's
clarification that qualitative and cumulative value evidence is admissible. It
does not represent a new human acceptance or revise any historical task outcome.
The [original repair report](opencode-validation-2026-09-09.md) and its
[manifest](opencode-validation-2026-09-09.json) remain unchanged.

## The work and its evidence

The practical task was to check how the native OpenCode adapter handled argument
defaults and malformed arguments. Cairn's sharing choice matters: omitted sharing
defaults to local, and a JSON boolean is required to request shareable capture.
The earlier implementation treated the string `"false"` as truthy.

| Step | Inspected evidence | What it establishes |
| --- | --- | --- |
| Retrieve prior guidance | Saved search and full pull of lesson version 1, matching the original manifest hashes | The investigator received specific guidance about defaults and parsed arguments; this is more than an index exposure. |
| Recheck its applicability | The contemporaneous repair report, normal-session check source and retained results | The earlier debug route did not establish normal-session validation. The investigation tested the actual execution path and found a broader gap than the original lesson described. |
| Reproduce the defect | Pre-repair native/API log | A request containing `shareable: "false"` returned a newly created record instead of the expected refusal. The adapter source explains the truthiness conversion. This used synthetic content in a disposable store. |
| Repair and verify | Commit `d457b294f4dc7445128b3554f36e092dc83352db`, post-repair integration log and normal-session results | All five handlers use their declared strict schemas before settings or CLI access. Malformed requests are refused; valid capture, retrieval, edit and permission paths passed the existing checks. |
| Retain the correction | Native OpenCode edit response and fresh native Codex retrieval report | The original lesson was revised from version 1 to version 2 and the corrected body was available in another harness. This verifies continuity of the guidance; later task benefit from version 2 is not observed here. |

The historical source hashes for the adapter and both relevant check scripts
match `d457b29`. All 31 private artifact entries in the original manifest still
match their retained hashes at this review. The [review metadata](value-case-validation-2026-09-09.json)
records that inspection. No benchmark or model cohort was rerun.

The normal-session check used scripted loopback completions to exercise the
actual harness; it did not ask a model to discover the defect. The coding agent
did the investigation. That distinction limits claims about independently
replicated agent behavior, but it does not erase the useful repair or disqualify
the investigator's use of memory from consideration.

## Interpretation and alternatives

My assessment is that the saved lesson was a plausible useful starting point:
it carried a concrete concern about argument handling into the investigation,
where checking it led to a more consequential finding. Memory was useful as
fallible guidance to examine. The original lesson's explanation was incomplete;
following it without checking the actual execution path could have preserved a
mistaken assurance about validation.

The evidence cannot separate that contribution from the investigator's existing
conversation context, familiarity with the adapter, direct source inspection,
or ordinary defect review. Another investigation might have found the same bug
without Cairn. The sequence was selected retrospectively because its artifacts
were available and it had a useful outcome; it is not a representative sample
of all memory use. There is no measured time saving, prevented operational
disclosure, independently repeated discovery or established long-term benefit.

Costs include reading the old lesson, checking its claims, investigating the
broader behavior, correcting the note and maintaining the new checks. A mistaken
initial scripted check also had to be corrected. Those costs are documented as
work performed, but the retained evidence does not support a net savings figure.

This therefore adds a source-supported qualitative case alongside the existing
configuration-task and knowledge-transfer observations. It leaves general and
durable value open. A later real task that uses the corrected guidance could
extend this case, including a contrary result; there is no need to stage another
identical task merely to obtain a score.

## Consequence for evaluation

Keep the outcome of the work, the proposed contribution of memory, and confidence
in that explanation separately visible. A lack of controlled attribution means
the contribution remains uncertain. It does not assign it a value of zero.
Conversely, a verified repair alone does not demonstrate that memory improved it.

The existing [assessment and review path](../use-outcome-loop.md#qualitative-and-cumulative-review)
can retain narrative judgments and selected evidence without adding a value
score. A source-linked case can span multiple receipts and related work; task
acceptance labels continue to describe the particular task they assess. The
review decision and remaining doctrine obligations are identified in the metadata.
