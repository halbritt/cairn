# Native repair recurrence

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

## Native execution still required

The preflight does not launch Striatum or OpenCode. The remaining executor must
use the existing Driver and LLM supervisor, rather than wrapping that supervisor
with another process owner. The native condition must acquire and admit a Cairn
observation, name its exact ECR in the produced packet and observe its delivery
at the real invocation. Direct context supplies the identical lesson body.

The model must produce its own Change Set under the sealed build output
contract. Retain packet/base pins, rendered prompt, actual provider requests,
process outcome, submission/admission result and independent repair assessment
separately. Fixture planning/check admission is not repair acceptance. Apply
the existing candidate scope and repair evaluator to the returned source;
require a completed process, formatting and passing checks for task acceptance.
A quota or infrastructure failure remains an inconclusive execution outcome.

Freeze and verify the executor, sandbox exposure, source materialization and
output packaging before the three model invocations. Do not tune limits,
reference files or gates after seeing model results, rerun until acceptance,
or infer a general memory benefit from one run per condition. Accepted native
contract adoption and production enablement remain separate work.
