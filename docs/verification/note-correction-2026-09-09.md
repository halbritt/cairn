# Stale-note correction exercise, 2026-09-09

The model followed obsolete guidance and returned an unusable edit request.
The subsequent correction session reached its output limit without editing the
note. This exercise does not demonstrate successful model-selected correction
or improved downstream behavior. The cohort is stopped; its skipped conditions
must not be described as evaluated.

## Setup and results

At Cairn `7928355`, a disposable PostgreSQL store received the retained v5
OpenCode procedure as a new ordinary note at fixture version 1. That real prior
procedure describes full-draft editing. The operational OpenCode procedure was
already v6, documenting body-only editing; the experiment did not replace it.
The corrector received a selected current excerpt from `docs/mcp.md` and was
asked to correct the obsolete paragraph while preserving the preceding text
and metadata. Consumers were asked for the smallest supported edit arguments,
with an explicit option to abstain when they could not establish the interface.

OpenCode 1.18.21 ran `deepseek/deepseek-v4-flash-0731` through the existing bounded
relay. Native Cairn search/pull tools accessed only the fixture API. The corrector
also had edit access. The host observer identity and database credentials were
excluded from model processes. Host process outcomes, retrievals and task
assessments were retained separately.

| Condition | Observed result | Current assessment | Provider requests | Elapsed seconds |
| --- | --- | --- | --- | --- |
| No memory | Returned null arguments and correctly abstained. | Unknown, v2 | 2 | 16.40 |
| Stale memory | Pulled/cited the obsolete procedure, denied body-only support and returned a draft containing the output-only `attribution_state` field. The request was rejected. | Rejected, v1 | 4 | 71.34 |
| Corrector | Searched and pulled; no edit call or final JSON. Fixture procedure stayed at v1. | Unknown, binding quota, v2 | 4 | 60.02 |
| After correction | Not run: the stop rule fired. | Unobserved | 0 | — |
| Direct source | Not run: the stop rule fired. | Unobserved | 0 | — |

The prospective request limits were 2 for source-free/direct-source controls,
6 for memory readers and 8 for the corrector. Per-response output limits were
1,024, 2,048 and 4,096 tokens respectively. Process limits were 120 seconds for
consumers and 180 for the corrector, with a 90-second provider response limit.
All three observed processes exited zero. That did not establish task success.
No relay request was rejected; the final corrector response reported `length`
at the configured 4,096-token limit. Its native step record reports zero output
tokens and 4,497 reasoning tokens, while the relay reports 4,096 completion
tokens. These accounting fields differ; they do not establish why the allowance
was exhausted or justify attributing the failure to note length.

This was one fixed-order exercise with one model and one stale source. Budgets
differed by role, and target/request UUIDs differed by consumer. Neither a causal
memory advantage nor a general model capability follows from these observations.
The stale condition documents an incorrect answer with a cited stale source;
it does not isolate that source from other possible causes.

## Assessment review

The controller's all-checks gate initially classified the compliant abstention
as rejected. A clarification recorded after inference started but before answer
inspection recognized that the frozen prompt expressly allowed null. The review
retains the original assessment and adds unknown v2. It also refines the
corrector's unknown assessment to identify the observed output limit. Two actual
stale-condition citations were recorded as agent testimony, not causal influence.

The first review saved both assessment corrections before failing an assertion
that expected the API to name the rejected field. The API deliberately returns
a generic bounded-JSON refusal. A second review assertion also omitted the CLI's
status prefix. The final review restored the retained corrected dump, preserved
both v2 assessments, verified the actual refusal, recorded the citations and
verified that the fixture procedure remained unchanged. No model was rerun.

The API refusal is `INVALID_REQUEST: invalid bounded JSON request`.
`localapi/server.go` rejects unknown JSON fields; `core/types.go` excludes
`attribution_state` from `Draft`. The source establishes the invalid field;
the generic response alone does not identify it.

## Applied maintenance and next decision

Reviewing live retrieval also exposed obsolete full-draft-only guidance in the
separate Codex setup procedure. This coding agent corrected that passage through
the installed ordinary body-revision API, producing v4 from v3. A fresh native
Codex client pulled the exact replacement body. The check started no model turn;
it establishes a real saved correction and client retrieval, not independent
model adoption or downstream benefit. The model exercise's fixture remained
separate and unchanged. `docs/mcp.md` now explicitly names `attribution_state`
among fields excluded from replacement drafts.

Continue using the existing body-only correction interface. This result does
not justify another storage API, more recovery machinery or a budget increase.
A next useful observation is whether a later task actually retrieves and applies
the corrected procedure. If model-selected maintenance is exercised again,
separately state its hypothesis and inspect the binding's output behavior before
claiming a note-format problem. The stopped cohort must remain a negative result.

## Evidence

[Metadata](note-correction-2026-09-09.json) records hashes and retained local
pointers. Raw model output, memory bodies, credentials and database dumps remain
outside Git. The original report/dump and all review attempts are preserved under
`/tmp/cairn-note-correction-model-a`; `review-c/report.json` and
`review-c/reviewed.dump` contain the completed review. These local retention
pointers are not portable repository fixtures. The operational correction and
fresh native pull are retained under `/tmp/cairn-codex-correction-*`.

Doctrine packet `pkt-c73ea2eb2d668dd3` retains a validated abstention receipt:
causal-repair obligations remain material and unmet for a benefit claim.
The report uses repository-contract precedence, evidence before intervention,
observable process completion and hypothesis-led investigation. The metadata
retains the packet hash, corpus/retriever identities, receipt and residuals.
Verification in this continuation covered restored-store review, exact native
retrieval, preserved note metadata, JSON and documentation links. Product code
and the installed executable were unchanged; the product test suite was not rerun.
