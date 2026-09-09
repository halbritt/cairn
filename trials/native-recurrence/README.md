# Native repair recurrence

The [original](../../docs/verification/native-permissions-2026-09-08.md) and
[corrected](../../docs/verification/native-corrected-recurrence-2026-09-08.md)
comparisons both completed without an admitted repair. They are retained
evidence; repeating this unchanged task/binding is retired from the active
usefulness sequence. The commands below remain available for explicit experiments.

This is a new prospective comparison using the previously repaired explicit
agent-connection defect. It tests reuse of a lesson about the same problem;
it does not test transfer to an unfamiliar problem. The earlier experiment in
`trials/agent-env` remains unchanged and inconclusive.

## Frozen comparison

`scenario.json` fixes the source revision, complete source file list, task,
model and executable hashes, lesson identity/version/body hash, existing hidden
oracle, reference repair, conditions and per-condition limits. The three
conditions are no added memory, direct lesson context and native Cairn context.
Each will receive one invocation, at most 32 provider requests, at most 8,192
output tokens per request and 600 seconds. Limits must be enforced by the
executor and relay before model execution. They are not enforced by this
preflight, which makes no model calls.

The complete repository at the frozen revision is the common workspace. It
includes Makefile, scripts, documentation and older experiments on different
tasks. Those existing sources are available equally in all conditions. The
revision predates this task's oracle, controller and reference repair. These
task-specific materials remain outside the model workspace, along with host
credentials and operational memory. The selected shareable A lesson is supplied
privately; its body is not committed here.

## Run the source and oracle preflight

Provide the installed binaries matching the scenario hashes and the retained
Cairn `pull` response for the pinned lesson. Use a new private output directory:

```sh
python3 -B scripts/trial_native_recurrence.py \
  --output /tmp/cairn-native-recurrence-preflight \
  --cairn "$HOME/.local/bin/cairn" \
  --opencode "$HOME/.npm-global/lib/node_modules/opencode-ai/bin/opencode.exe" \
  --lesson /path/to/private-cairn-pull-response.json
```

The command checks pins, archives the frozen source, records every file hash,
and runs `make check` and `make test` in the existing isolated filesystem
sandbox. It mounts no Cairn socket, token, live database, model configuration or
credentials. The same hidden Go oracle and evaluator used by the original
experiment must reject the baseline in the four previously recorded cases and
accept the reviewed reference in all eleven cases. Database tests skip because
no test DSN is supplied. The oracle uses real Unix HTTP connections and checks
authentication and environment preservation.

Output includes the source manifest, private lesson, logs, separate baseline and
reference evaluation trees, and `report.json`. Only `workspace/` is suitable as
the source input for a model; mounting the parent output directory would expose
the oracle and reference. A failed check exits nonzero and retains diagnostic
files. A passing report means source/oracle calibration, not experiment completion.

## Calibrate the native executor

The source preflight above does not launch Striatum or OpenCode. The separate
executor uses the opt-in `TestCairnNativeRepairExperiment` in the Striatum native
integration worktree. Its default is a model-free runtime that emits a harmless
Change Set while leaving the connection defect intact:

```sh
python3 -B scripts/trial_native_executor.py \
  --output /tmp/cairn-native-executor-fixture \
  --striatum "$HOME/git/striatum-next-wt/cairn-native-observation" \
  --cairn "$HOME/.local/bin/cairn" \
  --opencode "$HOME/.npm-global/lib/node_modules/opencode-ai/bin/opencode.exe" \
  --lesson /path/to/private-cairn-pull-response.json \
  --preflight /tmp/cairn-native-recurrence-preflight/report.json
```

Each condition uses a new private source repository and graph. The actual Driver
produces and admits its Work Graph; its build runs through the existing LLM
supervisor under delegated cgroup confinement. In the native condition the Driver
also produces and admits the acquired Cairn observation. The coding runtime
receives its selected context at launch through the existing native host.

The filesystem bridge expands the sealed Product into `source/`, verifies every
file against the frozen manifest and replaces itself with the coding harness.
It pins the Go toolchain and module-cache paths resolved by the Cairn controller;
the supervisor's default system Go may be too old for this source.
Both mounted views of sealed inputs are read-only; source scratch and declared
outputs are writable. A prompt checksum is retained in the private controller
directory before the harness starts, outside the model's filesystem mounts and
the supervisor's disposable workspace. The fixture checks read-only inputs and
runs `make check` and `make test` inside the actual native runtime sandbox.
Its output must pass Driver checks but fail the independent repair oracle.

Calibration first runs the pinned OpenCode with a local scripted provider through
the same filesystem bridge. It must write temporary scratch while failed write
attempts leave the sealed Product unchanged and an unrelated home file absent.
This makes no model requests. The model-run gate requires the resulting
permission evidence for the exact harness and current policy, in addition to
the three native fixture conditions. See the [original model results and
permission correction](../../docs/verification/native-permissions-2026-09-08.md).

## Execute the frozen model comparison

Use the same command with a fresh output directory, `--execute-model` and
`--fixture-report /tmp/cairn-native-executor-fixture/report.json`. Before model
contact the controller verifies the source preflight, all three completed
fixture conditions, exact prompt receipt, actual native delivery/outcome and
matching executor hashes. Code or source changes require a new fixture run.

The relay enforces the scenario's request and output limits. The declaration
allows one invocation of at most 600 seconds and reserves another 60 seconds
for dispatch preparation and completion. Driver redispatch is refused by this
experiment adapter. This is experiment configuration, not a production backend
or accepted native contract adoption.

Direct context supplies the identical lesson body through the ordinary packet
purpose. Striatum renders that purpose in both the objective and Work Graph
input, so the direct condition contains two copies; native context contains one,
and control contains none. The report records these exposure counts. This is a
known difference between delivery mechanisms, not an exactly dose-matched study.

The model must produce its own Change Set under the sealed build output
contract. Retain packet/base pins, rendered prompt, actual provider requests,
process outcome, submission/admission result and independent repair assessment
separately. Fixture planning/check admission is not repair acceptance. Apply
the existing candidate scope and repair evaluator to the returned source;
require a completed process, formatting and passing checks for task acceptance.
A quota or infrastructure failure remains an inconclusive execution outcome.

The executor, sandbox exposure, source materialization and output packaging must
pass the model-free calibration before the three model invocations. Do not tune limits,
reference files or gates after seeing model results, rerun until acceptance,
or infer a general memory benefit from one run per condition. Accepted native
contract adoption and production enablement remain separate work.
