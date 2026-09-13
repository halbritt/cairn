# OpenCode historical cache repair trial

This opt-in trial tests whether supplemental Cairn context helps a real OpenCode
process repair a historical Striatum defect. It is a prerequisite experiment for
host integration; it does not alter a live Striatum graph or bypass its sealed
input contract.

The task is the Go build-cache lifecycle repaired by Striatum commit
`56840cf694ecf451980abbd694fd3c8c01cbc7d7`. Each model workspace starts from that
commit's parent. The later fix and held-out behavioral test are absent from the
model's filesystem. The workspace has only a new snapshot commit, so its Git
history cannot reveal the repair.

The held-out check invokes the real LLM supervisor and its confined runtime. It
observes whether the runtime gets an existing cache directory inside its lane
workspace and whether that directory disappears when the workspace is reclaimed.
Criteria v2 uses the dispatch workspace, which can contain a cache beside the
runtime working directory. It also checks that inherited supervisor metadata
does not override ordinary runtime environment values when no invocation
override was supplied. This is the regression reproduced in calibration L.
It allows any directory name and environment-passing implementation. Preflight
requires the check to fail on the original broken snapshot and pass on the
historical repaired snapshot. Missing confinement or a skipped check is not a
pass. The evaluator runs in a delegated user cgroup scope.

The three fixed-order arms use the same model, task, source snapshot, tool policy
and time budget:

| Arm | Supplemental input |
| --- | --- |
| `repo_only` | Empty Cairn scope; ordinary repository instructions and files remain available. |
| `native_excerpt` | The selected reviewed lesson supplied directly with its source locator. |
| `cairn_h0` | The same lesson captured as an evidence-backed B record, retrieved and rendered through Cairn. |

The original campaign supplied experience predating the repair: a paragraph in commit
`3a9550d0b27449bc2655c825dd1ab3af8c7cf946` explains that Go's active cache growth
was not an unreaped leak; D0013.C2 describes the supervisor's existing lifecycle
ownership. Neither source contained the later implementation. Those trials and
their earlier criteria remain unchanged in their frozen artifacts and reports.

The current scenario is explicitly `post_review_recurrence`. Its single lesson
is an operator-reviewed finding from calibration L, with the candidate digest,
diagnostic results and committed review locator pinned in `scenario.json`.
It explains the environment regression and skipped-test limitation. Cairn admits
this selected lesson through separate collector and operator promotion channels
in a disposable store. Codex performs these reviews and promotions under the
user's delegated operator authority. It is applied to fresh historical snapshots as a recurrence
experiment; it was not available at the historical repair date. The controller
pins the lesson to that base revision and verifies exact selection. Comparing
these fresh runs tests a known reviewed lesson on the same repair, not transfer
to an unseen task. Operational memory is unchanged.

The local model runs under an exact GPU fleet lease. The optional hosted
calibration uses the separately bounded route described below. OpenCode has a fresh private
home and configuration for each arm. A Bubblewrap filesystem excludes the real
home, trial controller, database, and later repair. The model can change only its
trial workspace and cache; the task further limits candidate changes to the three
production files and new tests in their packages. The mount boundary is not a
network sandbox. The task and OpenCode permissions prohibit network tools.

Each arm has a 300-second process limit and a 20-step configuration. The report
keeps process outcomes, selected record identities, candidate patch digests,
changed paths, observed event kinds and behavioral-gate results. It also keeps
tool/status counters and the token counts reported by completed OpenCode steps.
Those counters can be incomplete at timeout and are not an independent tokenizer.
An exact selected UUID in a text event is labelled citation testimony; it does
not establish influence or correctness. Candidate patches remain private trial
artifacts. Completion and handled interruption remove native OpenCode homes
and their raw session records. Abrupt controller death still requires inspection
and cleanup of its private trial directory. Native excerpts are a controlled context baseline,
not a complete evaluation of native history search or native memory facilities.

The controller captures the selected gate metadata as explicit local evidence
and attaches a task assessment to the actual run receipt. A failed necessary
condition means the bounded repair task failed. Passing that one condition leaves
full task acceptance unknown. No observed model activity leaves task outcome
unknown and is classified as a binding problem. Assessments and optional UUID
citations enter through the receipt-owning observer with stable retry IDs.
Assessments carry an instrumented witness for the host's bounded gate method;
model-authored UUID citations remain testimony. Earlier trials used operator
testimony, as recorded in their frozen reports. Neither path supplies a Striatum
acceptance verdict or proves causal influence.
An evaluator that fails before executing the behavioral condition also leaves
task outcome unknown; compilation or infrastructure errors need separate diagnosis.

The reviewed recurrence also applies
[`repair_contracts_test.go.txt`](../../trials/opencode-recurrence/repair_contracts_test.go.txt)
after each model run, separately from the frozen necessary-condition gate. It
checks cache writability during the invocation and explicit environment overrides
while preserving an unrelated inherited value. Both pass on the historical fix
under delegated cgroups. They do not cover cross-UID execution, every preparation
failure or full task acceptance. These results are supplemental review evidence;
they do not rewrite the controller's original receipt assessment.

Review of the first v2 baseline found a count-marker variant of the earlier
environment bug. The additional
[`inherited_metadata_test.go.txt`](../../trials/opencode-recurrence/inherited_metadata_test.go.txt)
supplies both inherited count and value markers with no invocation override.
The baseline fails and the historical fix passes. This is an exploratory
post-run check, derived after seeing the baseline; apply it unchanged to every
comparison candidate and keep it separate from the original gate. The lesson,
model inputs and original assessments remain unchanged during the comparison.

The compiler receives 32,000 units of available memory input room, leaving a
3,200-unit optional allowance under the current 10% policy. The model has a
65,536-token configured context; memory room is separate from the task, tool
schemas, output and accumulating tool results. The controller checks all three
selections before model launch, including the exact versions and body digests of
every intended H0 record. It checks the actual launch receipt again afterward.
The host now compiles its own package under the exact request ID used by the
wrapper and compares the returned receipt and seal, preserving receipt ownership.
These checks matter: the first completed attempt used only 12,000 units of room
and silently omitted both notes. That attempt is invalid for memory comparison.

A process exit of zero is insufficient: a model can finish without implementing
the repair. A failed sandbox launch or lost fleet lease is a binding failure,
not a model or memory outcome. Any reported improvement remains limited to this
one repair, model, budget and fixed order; it does not establish general causal
benefit, statistical superiority, task acceptance by Striatum, or a reason to
enable grooming.

To prepare an independent trial directory:

```sh
python3 -B scripts/trial-opencode-recurrence.py prepare \
  --source /home/halbritt/git/striatum-next --output /tmp/cairn-recurrence-new
```

Then use a currently routable exact model and an installed OpenCode binary:

```sh
/path/to/gpu-fleet/bin/gpu-fleet-run --model EXACT_MODEL --max-context 65536 \
  --job cairn-recurrence-new --timeout 1100 -- \
  python3 -B scripts/trial-opencode-recurrence.py run \
  --trial /tmp/cairn-recurrence-new --cairn /path/to/cairn \
  --opencode /path/to/opencode
```

The commands create only a disposable PostgreSQL cluster and isolated trial
workspaces. They refuse to reuse a trial store, so a retry requires a new trial
and fresh run identities. Preserve failed attempts when interpreting a later run.

The controller provisions a distinct hosted observer for each arm. Setup and
promotion remain operator work in the disposable store; execution, host events,
gate observations and citations use the owning observer without database access.
The wrapper receives a deliberately unusable DSN. The API token remains outside
the existing model sandbox.

Each `ARM-host/` directory journals one real wrapper attempt. A pipe holds that
process before it can prepare its child; only confirmed `spawn` releases it.
`identity.json`, `intent.json` and process metadata retain IDs and digests, while
`spawn.pending.json` and `terminal.pending.json` retain exact unconfirmed API
requests. Confirmed requests and responses stay beside them. Writes are private,
exclusive and synced. Reusing the directory or launch intent refuses execution.

A nonempty returned patch supplies the host's corresponding result reference.
Exit zero without a patch records `no_result`; even a completed host attempt does
not accept the task. The held-out evaluator still owns the bounded assessment.
The report includes `host_attempt` and the host-controller source hash.

After an ambiguous response, restore access to that trial's API and replay the
exact pending request under its original observer. Confirm pending spawn before
pending terminal; preserve the response. Do not rerun the arm as transport
recovery. A postprocessing error leaves a terminal request based on observed
process completion without inventing a corresponding patch. If the controller
was killed before observing termination, inspect the original process and receipt
first: intent or a saved PID alone does not establish termination or authorize
killing a possibly reused PID. This is retained recovery evidence, not an
automatic recovery daemon.

The [observed model failure review](../verification/observed-model-failure-2026-09-08.md)
exercises this path with a real model timeout, an independently reproduced
candidate defect, and a generated failure proposal converted to an unpromoted
local lesson. Its reviewer diagnostic does not replace the model candidate.

The [host-controller verification](../verification/opencode-host-observation-2026-09-08.md)
includes real OpenCode against a synthetic local endpoint. It establishes the
controller's authenticated ingress path, not model usefulness or native Striatum
admission.

`--arm` runs one arm. The reviewed recurrence scenario labels it as part of that
experiment; a single arm cannot establish a memory comparison. Older scenarios
without an explicit experiment kind default to `harness_calibration`. The optional
`--disable-thinking` sends `chat_template_kwargs.enable_thinking=false` through
the custom provider. This is a per-request model setting; the default leaves it
unspecified. The installed OpenCode binary can be checked against a local fixture:

```sh
python3 -B scripts/probe-opencode.py /path/to/opencode --disable-thinking \
  --output /tmp/cairn-thinking-probe.json
```

The fixture verifies the outgoing field and keeps only request metadata.
Add `--check-edit` to have the fixture request a read and an edit of a disposable
file through OpenCode's actual tools. The probe checks the resulting bytes. The
opt-in OpenCode integration suite runs both checks through the Cairn wrapper;
its ingress canary exists only in compiled memory, not in the task prompt.
OpenCode documents [model options](https://opencode.ai/docs/models/#configure-models),
and llama.cpp documents the
[chat template parameter](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).
Model support still depends on the actual serving template.

`--context-tokens 131072` permits a second calibration context size. Acquire a
fleet lease for at least that context and verify the live server supports it;
the default remains 65,536. The available room passed to Cairn stays 32,000.
All settings appear in the report. A change of context size, thinking setting or
tool policy creates a new experimental condition; preserve the prior result.


## Bounded hosted calibration

`--openrouter --arm repo_only --context-tokens 131072` selects the existing
OpenRouter `deepseek/deepseek-v4-flash-0731` binding for one calibration arm.
`--openrouter-model deepseek/deepseek-v4-pro-0813` selects the other explicitly
allowlisted binding. The selected model must already exist in the operator
configuration; a relay for one model refuses requests for the other.
It requires that model and the exact HTTPS API base in the operator's existing
OpenCode configuration, with an available environment-backed API key. The
controller reads the key; the sandbox receives only a disposable local relay
token. Hosted reports use `opencode-openrouter` and a null fleet lease.

The relay permits only chat completions for that model, at most 24 requests,
1 MiB of request bytes each, and 8,192 output tokens per request. Responses have
an 8 MiB byte limit, a 45-second socket timeout and a checked 120-second elapsed
limit; a pending socket operation may finish after that elapsed limit. The
300-second process budget and 20-step setting still apply. These are request
and transport bounds, not a verified account billing cap.

Flash requests force provider prices at most $0.20 per million input tokens and
$0.50 per million output tokens. Pro requests allow at most $1.50 and $4.00,
respectively. Both refuse per-request charges and require `data_collection=deny`.
These fields use OpenRouter's documented
[provider routing controls](https://openrouter.ai/docs/guides/routing/provider-selection).
Unavailable routing fails without relaxing those settings. The relay makes no
retries; any harness retry consumes another request slot. Different providers
may serve different calls. Reports retain their declared identities, HTTP status,
usage/cost when reported, known tool names, counts and digests. Unrecognized tool
names become `other`; argument schemas and tool descriptions are not retained. They retain no prompt or completion
content. Reported usage can be missing after interruption and is not an invoice.

The local test exercises real HTTP streaming, routing refusal, request exhaustion,
credential separation and upstream HTTP failures without a hosted request:

```sh
python3 -B -m unittest discover -s scripts -p test_trial_openrouter.py -v
```

A loopback relay protects the provider credential from the model filesystem and
environment. It does not confine the sandbox's network or create a production
credential service. Native session cleanup and all historical-trial limits above
still apply.

`--extended-budget` is an explicit hosted, single-arm calibration condition:
900 process seconds, 60 configured steps, at most 64 requests and a checked
300-second response limit. Context, output tokens per request, request/response
byte limits, provider controls, task and gate remain unchanged. The standard
profile remains the default. This changes the aggregate work budget as a group;
it does not isolate which individual limit caused an earlier failure.


For targeted diagnosis, `--retain-final` explicitly retains up to 8 KiB of the
last model text event in a private `ARM-last-explanation.txt` file. It excludes
reasoning and tool-output events. This is selected diagnostic evidence; it is
not automatically added to Cairn memory or committed. The report keeps its path,
size, digest and truncation flag. The last text event may precede a timeout and
is not necessarily a terminal answer or proof of completion. Default capture
remains disabled. Treat the explanation as local/private regardless of what the
model says about sharing. The operator owns its retention: remove it after the
diagnostic review and retain only the necessary metadata or a reviewed finding.
The controller does not automatically expire an opted-in explanation. Native
session homes are still removed at completion.


`--output-tokens 32768` is a separate hosted calibration condition for a model
that exhausts the default 8,192-token response allowance. Reasoning shares that
allowance. It permits 32 MiB of streamed response bytes and a checked 600-second
response duration while keeping the selected aggregate process budget. The
socket timeout remains 45 seconds. OpenCode can request less than the configured
allowance; the relay records the actual `requested_output_tokens` on every call.
The tested OpenCode requested 32,000 under this setting. Retain earlier outcomes
when changing response capacity; this setting is not evidence of a better repair.
