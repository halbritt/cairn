# CAIRN-223 source-fit pilot, 2026-10-24

## Frozen input and scope

The synthetic panel has six cases and 15 scheduled arms, one replicate each.
Manifest SHA-256:
`d1acec4b07892215831a7e46330afa04c5e70c9132d15ca4060b41a6a399f909`.
The baseline source checkout was `7ea0e14a68f3de50eed3a11c4225d16d6672bb0a`.
The trial used the native Claude Code subscription route (`claude.ai`, Max),
CLI 2.1.310, `sonnet` at medium effort; completed native stream events resolved
to `claude-sonnet-5-5`. API keys and OpenRouter routes were removed from the
child environment. The source store was disposable PostgreSQL, not operational
memory. The direct whole-source arm was a positive control.

## Local preflight

`check_source_fit_panel.py` accepted all six frozen cases and 15 scheduled
arms. The disposable-store preflight exercised the actual lifecycle hook:

| Case | Current hook observation | Diagnostic arm |
| --- | --- | --- |
| Buried live qualification | Only a 1,536-byte partial span of the 11,482-byte mixed note arrived; the current 512 KiB qualification was elsewhere. | The exact hint returned a 188-byte partial span; the author-maintained 310-byte current extract arrived whole, while the original body was verified intact after supersession. |
| Wrong-project Atlas | Whole Cairn and Trail notes both arrived. | The explicit project hint still delivered both whole notes. |
| Current export correction | Current and historical route notes arrived whole. | Direct whole-source control was scheduled. |
| Required conflict | Two mandatory class C records were issued and disputed in a separate store. | Current hook refused the operation (`memory command exited 2; operation not confirmed`); it did not silently choose one. |
| Semantic unavailable | Lexical recovery note arrived whole without a semantic worker. | Direct whole-source control was scheduled. |
| Inaccessible source | The local-only record did not enter the hosted hook view. | Direct arm excluded it too. |

These are source-delivery observations, not model task outcomes. The hook's
unrelated optional candidates and index entries are not evidence of relevance.

## Native pilot ledger and outcome review

Eight of 15 scheduled runs were admitted: seven completed, the eighth is
failed/unscored, and seven were never run. The eighth run's reported usage
pushed the total to 108,574 tokens, 8,574 above the frozen 100,000-token
ceiling. The driver had checked the cumulative total only after each run. It
stopped immediately after that observation. No remaining arm was admitted.
The overrun is a harness defect and is retained in the denominator. A new
pre-admission reserve check and tests now refuse a next run before this
observed near-ceiling state; the same freeze will not be replayed.

| Case / arm | Exploratory task outcome |
| --- | --- |
| Buried / current | Safely abstained from giving a live allowance. It identified 2048 KiB as offline-only and noted that its partial source lacked the current value. Its answer reported a permission denial on an attempted Cairn search. The terminal trace confirms a search call, but the runner did not retain its tool result, so recovery was untested. |
| Buried / direct whole source | Answered 512 KiB and qualified the historical 2048 KiB figure. |
| Buried / exact hint | Answered 512 KiB and qualified 2048 KiB, but explicitly stated that only the beginning and end of the 11,482-byte source were seen. The unread middle prevents complete-source acceptance for this arm. |
| Buried / author-maintained extract | Answered 512 KiB from the whole current extract, identified 2048 KiB as historical/offline, and disclosed that it had not checked the preserved original. |
| Wrong-project / current, direct, exact hint | All three answered 30 days for Cairn Atlas and explicitly excluded Trail's 90-day motorcycle navigation note. Both relevant and wrong-project notes were delivered whole. |
| Export correction / current | The answer named current `/v2/export` and historical `/v1/export`, but the run is unscored because it crossed the frozen token ceiling. |

The required conflict, semantic-unavailable, inaccessible-source and remaining
export arms have no provider outcome. Native Cairn tool recovery has no
accepted result: the only attempted search was reported denied by the model.
The driver now declares only Cairn search/pull as allowed
tools. At the time of this pilot, that change had local syntax and guard tests
only; it has not been provider-verified. Do not infer a production retrieval
change or token saving from this panel. The partial-source qualification, tool
recovery, controls, and paired task costs still need an admitted, separately
frozen evaluation.

A later [model-free native tool preflight](source-fit-tool-recovery-2026-10-24.md)
verified local search/pull discovery and execution with these runner flags. It
did not change this pilot's provider result or complete task acceptance.

The runner retains selected answers, resolved model and usage in a local
cache directory outside the repository. It does not commit raw model output,
private sessions, credentials or operational memory.

The trial driver's sanitized native-tool ledger now also validates supported
Cairn search/pull payloads and records emitted record ID/version, whole versus
partial extent, source and delivered digests, and byte counts without retaining
source bodies. Unsupported or malformed result shapes remain `unknown`; native
tool failures remain errors with no source items. This improves provenance for
future admitted runs but does not fill the unrun arms, score task outcomes, or
establish subscription behavior.
