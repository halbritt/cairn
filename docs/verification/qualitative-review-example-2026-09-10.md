# Qualitative review example — 2026-09-10

The [worked example](../use-outcome-loop.md#record-a-qualitative-review) now shows
how an ordinary authenticated agent can save and read back a selected account of
memory's possible contribution while leaving task acceptance unknown. It uses the
existing assessment API. This is a documentation improvement, with executable
verification; it is not another demonstrated memory-value case.

The Cairn coding agent authored the example after the attempted native OpenCode
authoring run failed to produce a candidate. The two results remain separate.

## What the example establishes

The reviewer starts with an owned receipt and reads its current assessment
history. They choose a narrative covering observations, possible contribution,
alternative explanations, costs and uncertainty. A saved JSON request retains its
request ID for transport retries and uses the last reviewed assessment version.
The ordinary profile remains testimony. Unknown task acceptance does not exclude
a plausible qualitative contribution, and a source pointer in prose does not
become captured evidence.

The literal two shell blocks were extracted from the document and executed with
an existing installed CLI against a newly initialized disposable PostgreSQL
cluster and hosted-profile API. The synthetic reviewer began with empty history,
saved a Unicode narrative, read it back exactly, and appended a separately
reviewed second assessment. Both stayed unknown/testimony. Retrying the first
saved request returned version 1, including after version 2 existed. A stale
new request returned `VERSION_CONFLICT` and preserved both earlier versions.
The client had an invalid database path; only the temporary API accessed its
owned database. The API and PostgreSQL processes stopped after the check.

The example gives the reader executable steps for the assessment workflow
previously described only in prose. It does not establish
that users will adopt the example, or that such reviews reduce task costs. There
is no new score, API, migration, runtime dependency or installed binary change.
The existing full integration suite was not repeated for this documentation-only
change; the actual example and its conflict/retry path were exercised directly.

## Native attempt retained as a setup failure

One OpenCode 1.18.21 session used the existing bounded OpenRouter relay with
`deepseek/deepseek-v4-flash-0731`, ordinary hosted Cairn MCP access and a read-only
snapshot of four selected source files. The limit was 12 requests, 8,192 output
tokens per request and 300 seconds of native execution. No write, shell,
delegation or memory-mutation tools were enabled.

The process timed out after 300.147 seconds. All five attempted source reads
failed permission checks. Six Cairn searches and seven full note pulls completed,
but the model produced only interim commentary and no candidate. The controller's
`final_text_present: true` means text events existed; it does not establish final
output. The selected review manifest explicitly records `candidate_present: false`.

Ten relay requests began: nine completed with reported usage and the final one
ended with a broken pipe after the native timeout. Reported cost totals
$0.007250116 for the nine completed requests. That excludes unreported final
request usage, local computation and controller/review work, so it is not total
cost. Retrieval contact and relevant guidance do not establish task benefit.

The six actual retrieval receipts were linked to observed run
`01d12586-8d71-4652-b5d9-fad27176675a`, attempt
`14821772-44ea-4f08-aa41-82a4c8aee966`. Assessment v1 records unknown task
acceptance with a binding/adapter obstacle. Its observer is
`observer:cairn-hosted`; the service labels that channel instrumented. The
narrative remains the coding agent's source-and-trace judgment, not an automatic
proof of its explanation or human acceptance. No raw model output was committed.

## Corrected preparation for future tasks

OpenCode's pinned [read implementation](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/tool/read.ts)
checks paths relative to its worktree. Its [project implementation](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/project/project.ts)
uses `/` for a global project without VCS. The initial selected-source directory
had no Git metadata, and its absolute `/work/*` allow rule did not match reads.
Simply substituting repository-relative names also failed in that setup.

A separate local scripted provider reproduced the denials. After initializing
Git metadata in the scratch snapshot and using exact project-relative rules,
the actual native client read all four selected files and still denied the
unselected `core/authority.go`. The original absolute rule continued to fail.
These checks use no answering model. They correct this scratch preparation;
installed permissions remain unchanged. Source bytes retained their original
manifest hashes. No second model authoring run was launched merely to replace
the negative result after the documentation had already been completed locally.

## Evidence and limits

The [selected manifest](qualitative-review-example-2026-09-10.json) retains
identities, counts, receipts and hashes. Private source snapshots, raw native
output, controller files and executable-check logs live under
`/tmp/cairn-qualitative-example-20260910`; these scratch files are not durable
repository evidence bodies.

Both the root author and native session pulled assessment-history procedure
`25adf0b0-7246-4f63-8687-7ccf2770f12a` v1. It reminded the author to read narrative
history and preserve receipt ownership; current source and the owner's explicit
value clarification supplied overlapping guidance. This is an account of use,
not an incremental or net-benefit claim. U1/U4/U5 and E4/D2 remain open.

The implementation decision used Pincite packet `pkt-259a02c3ad4cf930`; its
validated decision receipt and 18 nonmaterial generic Python obligations are
identified in the manifest. The decision preserves the existing API and declines
to add another evaluator or rerun a completed local authoring task.
