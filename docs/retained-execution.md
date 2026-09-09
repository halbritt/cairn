# Execute an exact retained package

A host can compile memory before dispatch, retain its receipt and semantic seal,
then execute that exact package later. Newly optional notes do not change the
retained input. Changed eligibility or required context still refuses launch.

Use the same observer profile for capture and execution. The initial `compile`
request must include the exact repository/task/run scope, query, purpose
`context`, memory budget and run context. Its `context` object contains
`task_class`, `binding_id`, `capability_id`, and any applicable `revision` and
`workspace_sha256`. A package compiled without run context cannot establish the
matching context required for retained execution.

Given that receipt and seal:

```sh
cairn agent --token-file ~/.local/share/cairn/observer.token run \
  --receipt-id RECEIPT_UUID --seal blake3:EXPECTED_DIGEST \
  --repo /path/to/repository --task TASK_ID --run RUN_ID \
  --request-id BINDING_REQUEST_UUID \
  --query 'original retrieval query' --tokens 32000 \
  --task-class build --binding DECLARED_BINDING --capability DECLARED_CAPABILITY \
  --destination local --prompt 'The task to execute' -- COMMAND ARGS...
```

Replace the placeholders with the retained values and actual command. Supply
the same revision/workspace flags if they were present at capture. For hosted
delivery, use a provisioned hosted observer and `--destination hosted`.
`--request-id` identifies the binding request in this path; retain it for
inspection and exact request retries. It does not authorize a second launch.

Both receipt flags are required and nonempty when either is provided. In retained
mode, `--query` must equal the original query; omitting it means an empty query
and does not substitute the task prompt. The new task prompt is separate from
the memory selection. Scope, purpose, query, context and memory budget must match
before binding. A different host context needs a new compatible package.

The runner loads the retained package through the observer-only `run-package`
operation, verifies the receipt/seal and declared context, then uses its existing
binding, [launch freshness](launch-freshness.md), managed-context, process and
outcome path. It never recompiles or substitutes memory in retained mode. The
child receives the same deterministic rendering and the same receipt owns the
delivery and outcome. Process completion remains separate from task acceptance.

For a trusted host integrating directly, `runner.Request.Retained` contains a
`core.RunPackageRequest` with `receipt_id` and `seal`. `core.Store.RunPackage`
and `localapi.Client.RunPackage` provide the retained read. The Unix operation is
`POST /v1/run-package`; `cairn agent run-package` accepts the same JSON on stdin.
It checks observer role, receipt ownership, repository, authenticated destination,
semantic integrity and current eligibility. Loading is a read: it creates no
new receipt, binding or launch claim. Index packages require a tool route and
are refused by this body-execution path.

A wrong seal returns `INTEGRITY_FAILURE`; wrong owner/role/destination returns
`AUTHORITY_DENIED`; mismatched run declarations return `INVALID_REQUEST`.
Changed memory returns the existing freshness or policy refusal. No fallback
launch occurs. Eligibility is checked again at the launch claim so a successful
load does not authorize later stale delivery. Ambiguous claims still require
[run-status inspection](run-status.md), never automatic execution retries.

This is the Cairn execution component needed by the proposed native bridge.
Striatum's observation acquisition, admitted ECR input, adapter integration and
actual host correspondence remain required; this command alone does not prove
native Striatum delivery.

## Kind-filtered runs

Fresh `cairn run` and `cairn agent ... run` accept repeated `--kind`, using the
same optional selection labels as search. Required instructions still apply.
When executing a retained body package that carries `kinds`, supply the same
set with `--kind KIND` flags. Order and duplicate labels do not change intent;
omitting the filter or supplying a different set returns `INVALID_REQUEST`
before binding or launching. Retained execution does not recompile to satisfy a
different filter. Omitted kinds continue to match unfiltered retained packages.

The [retained-kind repair](verification/retained-kinds-2026-09-09.md) records the
original mismatch and verified behavior. Upgrade the CLI or embedded runner;
this repair requires no API or schema change.
