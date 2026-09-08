# Reusable agent search and pull

The client now exposes authenticated `agent search`, `pull` and `pull-evidence`
commands. Search pairs each ordered index entry with a complete, quoted pull
command, removing the custom JSON construction and receipt/handle pairing used
by the earlier experimental helper. See the [command contract](../index-and-pull.md#agent-commands-without-request-json).

## Verification

The initial CLI tracer refused the unknown command; it passed after implementation.
Unit checks protect exact request fields, mandatory/context metadata, source seal
labeling, missing/duplicate version handles, shell argument round trips and whole
response budget refusal. `make test-integration` passed all packages with the
race detector and the real CLI/Unix API probes. The new API probe exercises
mandatory C preservation, local/hosted filtering, full body and evidence pulls,
repeated-command credits, foreign-caller refusal and a token path containing
quotes and shell syntax. `make check` and all 14 Python tests passed.

## Actual OpenCode contact

Code and the new [scenario](../../trials/agent-search/scenario.json) were committed
at `46eda160e240985b16c2d514e2e5e577ca859525` before the paid call. The scenario
fixes two documentation sources by digest, a different host-integration question,
the model, request/output/process bounds and expected answer fields. A clean
binary was used in an owned disposable PostgreSQL/API environment. A free
preflight exercised the same mounted CLI and a generated pull command.

OpenCode invoked the native commands directly, with no argument-translating
memory helper. It made four searches and two successful body pulls, with no tool
errors. The host linked all four retrieval receipts to the actual observed run.
Reports contain eight source-exposure rows and one execution. The model had an
ordinary hosted agent token; the host observer token stayed outside its bwrap
mounts. The child environment was explicitly cleared and populated with the
allowlisted paths/settings. This is the existing local experiment boundary, not
a claim of hostile-process or network confinement.

The run took 58.894 seconds and used four model requests. The provider reported
$0.0016908744 total cost. These are observations from one run, not a speed or
cost improvement claim.

## Result and interpretation

The final response preceded its JSON code block with prose. The existing trial
parser therefore returned no object, and the prospective automated assessment
was **rejected**. The saved per-field checks mean the parser could not establish
those fields; they are not evidence that the factual answers were wrong.

A subsequent inspection of the sole JSON block found all six answers correct
and both cited source UUIDs present in actual full-body pulls. This content check
is retrospective. The original rejected assessment and unknown citation telemetry
remain intact; no model rerun, parser relaxation or historical outcome rewrite
was used to produce a pass.

This establishes native CLI contact and source/outcome correspondence on a real
model invocation. It does not establish a prospective accepted task, superiority
over direct context, coding benefit, learned-lesson transfer, global context
accounting or general multi-harness usefulness. Further work should use the
interface on substantive tasks rather than repeatedly optimize this question.

[Derived observations](agent-search-2026-09-08.json) retain hashes, counts, timing,
cost and the separate content check. Private raw output, source identities and
host journals remain under `/tmp/cairn-agent-search-model-20260908-a`; the owned
database/API/model/relay processes are stopped. Tokens, identities, model config
and model session home were removed. No operational memory was used or changed.

The opt-in reproduction command is:

```sh
bash scripts/trial-agent-search.sh --output /tmp/NEW_PRIVATE_TRIAL_DIRECTORY \
  --cairn /path/to/cairn --opencode /path/to/opencode
```

It requires the existing configured bounded provider route. Add
`--preflight-only` to verify the native CLI path without model requests. An output
directory must be new; the wrapper owns and removes its temporary database.

## Local deployment

The same clean `46eda16` binary passed
[CI](https://github.com/halbritt/cairn/actions/runs/34262660086) and is installed
at `~/.local/bin/cairn`. SHA-256:
`dbcee2e64c675ef4f13a433440822befd515892bcef15218d725eec4405a48c4`.
The running API uses those bytes. Schema 029 and retained memory-version digest
are unchanged. A native local-agent search through the live API returned `READY`
with an unusable client database address. This added one ordinary retrieval
receipt, no memory records or executions. Existing observer status was preserved.
Local proof: `/tmp/cairn-agent-search-install-verification.json`.
