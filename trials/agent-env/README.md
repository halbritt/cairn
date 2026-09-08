# Agent connection repair transfer experiment

This experiment repairs a current Cairn defect: explicit agent socket and token
paths still require HOME because defaults are resolved before flags are parsed.
The frozen task and inputs are in `scenario.json`.

The three conditions use the same source, model, task, tool permissions and
budget: no memory; a previously reviewed lesson supplied directly; and that
same lesson available through Cairn's native search/pull commands. Only the
retrieval condition receives a search instruction. Each condition runs once;
there is no retry-until-pass policy or statistical superiority claim.

The lesson is the exact reviewer-authored A record from the failed environment
override repair described in
[the observed-failure report](../../docs/verification/observed-model-failure-2026-09-08.md).
Its original record/version, supporting evidence, source receipt, dump hash and
body hash are pinned. The selected nonsensitive lesson is explicitly copied as
shareable into an owned disposable trial repository; its original remains local
and unpromoted. It describes `exec.Cmd.Env` ordering, a different mechanism from
CLI defaults. Whether it helps this repair is an open hypothesis.

The evaluator owns `agent_connection_gate_test.go.txt`. Models cannot access it,
the reviewer reference, trial controller, history, operational memory or real
home. Candidate production changes are restricted to `cmd/cairn/agent.go`, with
new command tests allowed. Evaluation reconstructs a clean source snapshot and
applies only allowed changes before adding the hidden gate. Code executes inside
a filesystem sandbox; network access remains available for the model endpoint.
This is not a network confinement or hostile-code security claim.

Acceptance requires a candidate within scope, process exit zero, formatted
changed Go files, all Go tests passing without a test DSN, and an observed pass
for the hidden gate. Database tests therefore skip at this stage. Final source
integration requires separate review and appropriate repository checks. Final
answer formatting, citations and retrieval contact do not determine repair
acceptance. Search and pull observations are recorded separately and linked by
an external host to the actual run.

Calibration before model execution distinguished the baseline from a private
reviewer reference across 11 cases. Baseline failed explicit paths without HOME,
empty explicit token/socket, and invalid flags without HOME. Both implementations
passed ordinary HOME/CAIRN_HOME defaults, mixed explicit/default paths, invalid
explicit token refusal, and missing-default refusal. The reference passed all
11. Tokens are distinct across default/explicit files and the API response has
a random marker, so the gate checks the selected connection, not just exit zero.

Run with an existing configured trial model credential (the controller reads
it; the sandbox receives a temporary relay token):

```sh
bash scripts/trial-agent-env.sh --output /tmp/cairn-agent-env-preflight \
  --cairn /absolute/path/to/pinned/cairn \
  --opencode /absolute/path/to/pinned/opencode --preflight-only
```

Omitting `--preflight-only` authorizes the controller to make the three bounded
model runs. Output paths must be fresh. The wrapper owns a disposable PostgreSQL
cluster and removes it afterward; a private dump and reports remain in the output
directory. Selected candidate patches and raw model diagnostics remain private,
never committed as trial output. The controller pins binary hashes and refuses
changed inputs. Updating the task, gate or lesson defines a new experiment.
