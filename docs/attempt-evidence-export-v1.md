# Closed partial work evidence, version 1

`export_attempt_evidence(prepared, output, *, allowed_paths, required_paths,
verify_attempt)` exports bounded available work from a known closed attempt.
It does not grade the task or decide cell validity. Existing `closure()` and
`export_work_product()` retain their success-only behavior and schemas.

`verify_attempt` is a callable supplied by trusted root runner code. This module
does not load actor modules, import a callback named by JSON, or execute a
workspace script to obtain closure. The callable contract is an integration
boundary: callable status, schema agreement and digests do not authenticate
runtime observations. Root's actual adapter remains unimplemented here.

The callback receives the prepared request/occurrence binding and returns
exactly these fields:

| Field | Required observation/declaration |
| --- | --- |
| `schema` | `cairn.work-product-attempt-terminal/1` |
| `binding` | Exactly the prepared request ID, occurrence ID, request digest and supervisor source digest. |
| `workspace_sha256` | Exactly the prepared workspace digest. |
| `actual_wait_status` | Known terminal POSIX raw wait status, not a synthetic timeout exit. |
| `actual_returncode` | Integer consistent with that raw wait: exit code or negative signal. |
| `containment_status` | `released`, independently established by the owning supervisor. |
| `failure_evidence_status` | `closed`, independently observed failure-evidence worker return/closure; a marker or success-only PREPARED does not establish it. |
| `stop_cause` | One fixed observation label below. |
| `observations` | Unique bounded list of fixed selected observation labels below. |
| `proof_sha256` | Lowercase 64-hex reference supplied by the owner; the reference is not itself authentication. |

Stopped/continued waits, unknown/mismatched raw wait and returncode, unknown
release/persistence, another request/workspace and a failed callback refuse
before export. Known nonzero and signalled exits remain evidence. A budget label
does not infer actor fault or provider admission. Root's validity policy owns
B3/D1 and decides whether available work can be graded.

Stop observations are `completed`, `wall_deadline`, `native_budget_limit`,
`output_limit`, `timing_limit`, `resource_limit`, `provider_admission_stop`,
`observer_stop` and `unknown`. Selected observations are
`input_delivery_incomplete`, `frame_reader_refused`, `terminal_result_missing`,
`permission_denial_reported`, `action_observation_unknown`,
`hook_observation_unknown`, `capture_fault`, `parser_fault`, `sink_fault`,
`observer_interrupted`, `native_budget_reported` and `resource_limit_observed`.
These are observations only, never VALID/FAIL/UNKNOWN grading decisions.

`required_paths` must be unique safe relative paths selected by `allowed_paths`.
The exporter reuses the existing prepared-manifest, Git lineage/config/object,
path, no-indirection/special-file, changed-path, count, size and snapshot guards.
It inspects all selected files and patches and rechecks workspace state before
creating output. A failed inspection cannot become a missing-artifact claim.
The trusted adapter must establish workspace quiescence; hash checks are not a
snapshot sealed against an interfering host UID.
The new result's exact serialized metadata, files and patches must also fit the
unchanged 64 MiB total ceiling; the extra presence maps cannot bypass that cap.

The selected `cairn.work-product-attempt-evidence/1` result has
`status=evidence_exported`, available file/patch hashes, added/deleted paths,
the selected terminal observation and its digest, and explicit
`required_presence`/`required_sha256` maps. Missing required hashes are null;
no empty file or completed test is invented. `task_acceptance` remains false.
An empty or absent test log is not a claim that a check ran. The result contains
no validity field and does not authenticate itself when read later.

Output uses the existing exclusive, fsynced per-file writes. This is not atomic
batch publication. A later write/fsync error can leave a consumed partial
target, including an incomplete or unconfirmed manifest; no returned success
receipt follows that error and existing targets refuse. Do not infer closure,
successful persistence or replay permission from retained files. The caller
receives fixed refusal codes without exception text; no recovery or retry is
implemented.

Tests use disposable Git repositories and directly waited local child fixtures,
not native actors or providers. Synthetic trusted-callback containment and
persistence declarations exercise this offline API but do not attest runtime
admission. The actual owner adapter, B3/D1 policy, bounded failure-action/denial
projection and grading remain root-owned integration work. Default budgets,
permission profiles and accepted reader source pins are unchanged.
