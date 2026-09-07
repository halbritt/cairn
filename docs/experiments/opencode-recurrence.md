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
It allows any directory name and environment-passing implementation. Preflight
requires the check to fail on the original broken snapshot and pass on the
historical repaired snapshot. Missing confinement or a skipped check is not a
pass. The evaluator runs in a delegated user cgroup scope.

The three fixed-order arms use the same model, task, source snapshot, tool policy
and time budget:

| Arm | Supplemental input |
| --- | --- |
| `repo_only` | Empty Cairn scope; ordinary repository instructions and files remain available. |
| `native_excerpt` | Earlier repository text supplied directly with source locators. |
| `cairn_h0` | The same earlier text captured as evidence-backed B records, retrieved and rendered through Cairn. |

The supplied experience predates the repair: a paragraph in commit
`3a9550d0b27449bc2655c825dd1ab3af8c7cf946` explains that Go's active cache growth
was not an unreaped leak; D0013.C2 describes the supervisor's existing lifecycle
ownership. Neither source contains the later implementation. Cairn captures these
sources now, under explicit recurrence semantics and a pin to the trial's base
revision. It does not pretend the new memory records existed during the incident.

The local model runs under an exact GPU fleet lease. OpenCode has a fresh private
home and configuration for each arm. A Bubblewrap filesystem excludes the real
home, trial controller, database, and later repair. The model can change only its
trial workspace and cache; the task further limits candidate changes to the two
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
citations enter through the operator CLI as testimony, with stable retry IDs.
They do not impersonate a Striatum acceptance service or prove causal influence.
An evaluator that fails before executing the behavioral condition also leaves
task outcome unknown; compilation or infrastructure errors need separate diagnosis.

The compiler receives 32,000 units of available memory input room, leaving a
3,200-unit optional allowance under the current 10% policy. The model has a
65,536-token configured context; memory room is separate from the task, tool
schemas, output and accumulating tool results. The controller checks all three
selections before model launch, including the exact versions and body digests of
both intended H0 records. It checks the actual launch receipt again afterward.
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

For harness calibration, `--arm repo_only` runs just that arm and labels the
report `harness_calibration`. It cannot produce a memory comparison. The optional
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
