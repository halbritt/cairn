# Inspect a development checkpoint

`scripts/development_checkpoint.py` packages selected references for an unfinished
development task. It verifies file digests and exact Cairn source versions, then
projects those observations alongside the checkpoint's declared owner, findings,
uncertain effects and next action. It does not create task state in Cairn, claim an
inbox, execute the entry point, reconcile an effect, or decide that work is complete.

The checkpoint is an ordinary JSON file. Existing handoffs and assignments retain
ownership, authorization and lifecycle. A prose handoff remains sufficient when
there is no need to inspect references mechanically.

## Commands

From a Cairn checkout, use the existing Python entry point:

```sh
python3 scripts/development_checkpoint.py pack spec.json --output checkpoint.json
python3 scripts/development_checkpoint.py inspect checkpoint.json
python3 scripts/development_checkpoint.py status checkpoint.json --text
```

`pack` accepts a declaration, hashes its selected references, and creates a new
file. It refuses an existing output file and refuses unavailable sources or a
supplied digest that disagrees with the source. It copies selected context and
references, not referenced file contents or Cairn note bodies. It does not inspect
the semantic validity of an evidence receipt during packing; `inspect` does that.

For Cairn references, pass `--token-file` with the existing authorized profile
token's path. Optional `--socket` selects its API socket. The checkpoint tool does
not open the token file: it invokes the installed `cairn agent ... history` with
the exact record ID and version. No new credentials are created. `--cairn` selects
an installed executable when it is not on PATH. These connection choices come
from the invocation, never from checkpoint contents.

Evidence references require the shared `development-evidence` executable from
`infra/shared/development`. `--evidence-tool` selects that executable explicitly.
Inspection calls its read-only `inspect` and `compare` operations. If the receipt
records a review base, pass the current established base with `--base REV` to
`inspect` or `status`; omitting it leaves that comparison unknown. A missing or
incompatible evidence tool is reported as unknown, not replaced by a weaker
comparison. The shared tool owns evidence schema validation and source identity.

Exit codes are 0 when all selected references are current, 1 when at least one
is stale, missing or unknown, and 2 for an invalid declaration/checkpoint or a
failed pack. Code 0 is a reference check, never application-task acceptance.
JSON is the default output; `status --text` gives a short terminal projection.

## Declaration format

`development-checkpoint/1` requires `project`, `goal`, `owner` (name and provenance),
`workspace`, `next_action`, and a nonempty `references` array. Unknown fields and
unsupported schema versions are refused. For example, place this declaration in
a task's artifact directory and adjust the paths to the actual checkout and files:

```json
{
  "schema": "development-checkpoint/1",
  "project": "example-cli",
  "goal": "Finish the input validation change",
  "owner": {"name": "assigned worker", "provenance": "owner's current task"},
  "workspace": "../example-cli",
  "next_action": "Inspect the failed input case and repair it",
  "references": [
    {"name": "requirements", "kind": "file", "path": "../example-cli/AGENTS.md"},
    {"name": "test-result", "kind": "evidence", "role": "tested", "path": "test-evidence.json"}
  ],
  "questions": ["Does the malformed input reach the fallback parser?"],
  "findings": [
    {"id": "F1", "summary": "Malformed input is accepted", "disposition": "open", "source": "test-result"}
  ]
}
```

Relative paths are resolved against the declaration's directory during packing.
Packed paths are absolute and inspected at those locations; moving the checkpoint
does not transfer its sources. The receiver must make them available or create a
new declaration for the authorized relocated sources. Sealed checkpoints contain
`packed_at` in timezone-qualified ISO 8601 form and a lowercase SHA-256 for each
reference. `declared_at` is optional and remains the caller's declaration.

Reference kinds:

- `file`: `name`, `kind`, `path`. Only an explicitly selected regular file is
  hashed; final symlinks and directories are unsupported. No directory traversal
  or automatic capture of environment/configuration files occurs.
- `cairn`: `name`, `kind`, `record_id` (UUID), `version` (positive integer). Packing
  binds the exact body digest without copying the body. Inspection checks payload
  availability and the digest. A newer current version or inactive record makes
  the selected context stale even if that historical body remains readable.
- `evidence`: `name`, `kind`, `path`, `role` (`tested` or `reviewed`). The receipt
  itself is hashed. If those bytes change, inspection does not substitute its new
  claims for the selected evidence. Otherwise, the shared evidence tool validates
  the receipt, checks artifacts and compares current repository inputs.

Every reference name is unique. The declaration may supply `sha256` to assert an
already known digest. Packing verifies it rather than silently replacing it.

Optional `entrypoint` contains `argv` (nonempty string array), `cwd`, and `versions`
(a string-to-string map). These are runnable instructions for a separately
authorized receiver, never commands executed by this tool. `questions` is a list
of unresolved questions. `findings` uses the example shape above; `source` names
one selected reference. These are reported findings, not independent judgments.

Optional `effects` entries require `operation_id`, `outcome` (`confirmed-success`,
`confirmed-failure`, or `unknown`), `reconciliation` (selected instructions or a
pointer), and `source` (a selected reference name). Preserve the original ID.
The view always labels the declared outcome and leaves the live outcome unknown;
checking a note's digest does not inspect the system that owns the effect.

## Reading the view

The JSON view separates `declared` context from `references` observed during this
invocation. Each reference retains its expected digest, observed state and time.
Cairn observations retain source writer/time and current version where available.
Related findings/effects show their source status so an unreadable source does
not disappear as an empty or completed task.

Evidence observations provide the recorded tested/reviewed candidate or dirty
snapshot, recorded outcome and timestamp, receipt digest, and current freshness.
The view does not turn a current `findings-open` result into a clean review or a
stale clean review into approval. `current_subject` is taken from the shared
tool's live comparison when available and consistent across selected receipts;
otherwise it is null and the terminal view says unknown. It is not inferred from
the checkpoint's saved candidate or from a model's report.

`reference_status` summarizes selected references, with unknown taking priority
over missing and stale; individual observations retain all problems. Task outcome
is always `unverified`. The inspector is a reference projection, not an acceptance
engine or an idempotency mechanism.

## Verification and limits

Run the focused host task:

```sh
mise run test:unit -- test_development_checkpoint -v
```

Tests use disposable files and controlled CLI responses at the external boundary.
They cover changed/missing files, exact source identity and digest checks, missing
or superseded Cairn versions, unavailable evidence, receipt changes between reads,
malformed input and command text that must stay inert. Native source and shared
tool integration should be checked separately against selected authorized inputs.

The caller selects material references; the inspector cannot prove the selection
is complete or secret-free. Keep credentials and raw sessions out of declarations
and selected artifacts. Digests prove byte identity, not correctness or authority.
Inspection is a sequence of read-only observations, not a transaction spanning
the filesystem, Git and Cairn; source writers can change state after it returns.
