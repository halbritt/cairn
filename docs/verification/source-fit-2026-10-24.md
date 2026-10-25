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

## Gate for another provider run

Do not resume or rewrite the stopped output, and do not reuse this panel's
manifest as a new run. The six source-qualified cases already cover buried
whole-source qualifications, wrong-project terminology, current corrections,
required conflicts, semantic-unavailable lexical fallback, and local-only
exclusion. The current arm is the baseline; direct whole-source access is the
positive control. Hints and the author-maintained split are separately declared
diagnostics. The recovery freeze reuses these case definitions under a new run
identity and hash so its attempts have a separate denominator from the stopped
pilot.

The separate recovery freeze is now
[`trials/source-fit/recovery-2026-10-24/FROZEN.json`](../../trials/source-fit/recovery-2026-10-24/FROZEN.json),
with SHA-256
`7924fd78fb240c5910778486e043f6b0d08c1c38c5daa7a5844e3e83c6f99430`.
It has six cases, 15 planned arms, one replicate, a 100,000 reported-token
ceiling, 90 seconds per arm, 1,500 seconds total, and 1,200 output tokens per
arm. It records the prior stopped panel hash as its parent and pins the source
baseline to `06f96b23315d36e4d54ee2fffba03f0d182ed457`. The old manifest and
its failed/unrun denominator remain unchanged.

Both panel files pass the validator as six cases and 15 arms. The disposable
PostgreSQL preflight ran against the new hash: it returned a partial span for
the buried mixed note, kept Cairn and Trail Atlas sources distinct in the
delivered set, refused the required conflict, and did not expose the local-only
record. The author-maintained current extract arrived whole. This verifies the
new panel selection through the local preflight only; it does not add provider
task outcomes.

The checker, disposable-store preflight and runner now accept an explicit
`--panel` path. Validate the new freeze before any provider admission:

```sh
python3 -B scripts/check_source_fit_panel.py \
  --panel trials/source-fit/recovery-2026-10-24/FROZEN.json
```

The provider entry point is pinned by the same hash and must write to a new
private output directory outside the repository:

```sh
python3 -B scripts/run_source_fit.py --binary /path/to/bin/cairn \
  --panel trials/source-fit/recovery-2026-10-24/FROZEN.json \
  --expected-panel-sha256 7924fd78fb240c5910778486e043f6b0d08c1c38c5daa7a5844e3e83c6f99430 \
  --output /path/to/private/new-run
```

The manifest is still `proposed_not_authorized`. Preparing it does not authorize
the command above. Before a run, confirm the selected native subscription route,
model/effort, and the stated per-run and total ceilings. Keep the old panel's
result and every prior admitted run in the record; this new panel starts a
separate denominator.

The existing runner supplies these admission and stop checks:

- It accepts only the declared Claude subscription route (`claude.ai`) and
  checks login/subscription status before each arm. It removes Anthropic API
  credentials and base URL, the OpenRouter key, and alternate cloud-provider
  variables from the child environment; it has no OpenRouter fallback. It does
  not query account utilization or provider quota headroom. A native quota
  error stops the run as an error; there is no pre-admission provider quota
  measurement.
- Before each arm it requires `reported_tokens + next_run_reserve` to fit the
  panel ceiling. The reserve is at least 32,000 reported tokens, or 2,000 over
  the largest observed run, whichever is larger. It also enforces the arm
  count, per-run wall time, campaign wall time and output-token limits.
- Missing or malformed usage, a native error, an unavailable subscription
  route, a model change, or a crossed limit stops admission. Keep the attempted
  arm as failed, unscored or unknown; do not silently retry it in the same
  denominator. The reserve reduces predictable overruns but is not a provider
  hard cap on one native response or a live quota pre-check.
- Keep output directories private and outside the repository. Retain sanitized
  source-delivery metadata and selected answer/usage locally; do not retain or
  commit raw native streams, credentials or private sessions.

Provider task acceptance requires all planned arms to have an explicit outcome
or a retained stopped/unrun status. In particular: the buried-source answer
must use the live 512 KiB value and identify 2048 KiB as offline history; a
partial span with unread middle does not count as complete-source support. The
Cairn Atlas answer must exclude Trail's 90-day setting. The export answer must
prefer `/v2/export` and label `/v1/export` historical. Conflicting required
thresholds must remain unresolved. The lexical recovery case must work without
semantic service. The local-only phrase must not appear in hosted or direct
context. The model must successfully recover a complete source through the
allowed Cairn tools when context is insufficient. An independent reviewer must
score final answers against these source and task checks before any savings or
retrieval-change claim is made.

No fresh provider run is authorized or performed by this record. Subscription
behavior, model-initiated whole-source recovery, independent task acceptance,
paired cost, and any retrieval-change benefit remain unproved.
