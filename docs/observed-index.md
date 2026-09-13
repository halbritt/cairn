# Run a harness with an observed compact index

An observer can now supply bounded memory previews to a harness, let its ordinary
Cairn tools pull selected bodies and evidence, and retain the process outcome on
the original receipt. Both fresh and retained indexes use the existing process
wrapper. This supplies a tool route for the initial index; it does not observe
internal compaction or establish that the model used memory well.

For the owner's configured hosted profiles and native OpenCode tools:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-observer.token" run \
  --index --expansion-reader agent:cairn-hosted \
  --pull-tool cairn_pull --search-tool cairn_search \
  --destination hosted --carrier stdin \
  --repo "$HOME/git/cairn" --dir "$HOME/git/cairn" \
  --task storage-review --run storage-review-1 \
  --query 'storage tests' --tokens 32000 \
  --task-class review --binding opencode/process-h0 --capability unknown \
  --prompt 'Review the storage changes and explain what needs testing.' \
  -- opencode run --format json
```

Use actual task/run labels. `--expansion-reader` names an already provisioned
ordinary API principal, not a credential or caller identity. Its profile must
have the same repository and destination as the observer. The harness's tools
keep that ordinary profile; do not give them observer credentials. Tool names
must refer to tools already installed and permitted in the harness. Cairn checks
the declaration's syntax; the native harness controls tool permissions.

For a saved specification, replace `--prompt` with `--prompt-file task.md` and
keep the explicit `--query`. The file's bytes, including trailing newlines, go
to the child; they do not implicitly become the retrieval query. Both fresh and
retained runs support this. The [saved-task contract](authenticated-runner.md#read-a-saved-task)
describes file validation, path resolution and initial delivery limits.

Use stdin for OpenCode 1.18.21 to preserve literal JSON. Other commands may use
`--carrier argv`, which appends one literal argument. The child receives the
same index presentation as explicit `agent start`: full mandatory `selected`
context, bounded previews, source identities and complete `pull_arguments`.
The observer records process completion and delivery/output digests. Ordinary
pulls record actual source exposure; these observations do not prove compliance,
model-selected use or task acceptance.

## Reader designation and retained execution

The observer's `index` request accepts optional `expansion_reader`. It is immutable
for that index and part of compile request identity. A retry with a different
reader returns `IDEMPOTENCY_CONFLICT`. The authenticated API requires a configured
ordinary peer with matching repository and destination. The trusted core embedding
has no credential registry: its host must supply authenticated channels and choose
the recipient. Core expansion still requires an ordinary recipient channel with
the matching principal and repository. Role changes do not inherit the designation.

The designation authorizes body, body-span and supporting-evidence pulls only.
Receipt inspection, run binding, launch, delivery and outcome ownership stay with
the observer. All pulls, including successful transport retries, recheck current
eligibility, mandatory context, destination, payload availability and expiry.
Owner and reader share the original four credits and byte budget. A new principal
or request UUID does not create a fresh allowance.

`expansion_reader`, handles, expiry and remaining budget are operational metadata
outside the semantic seal. The seal still identifies selected semantic content;
it is not proof of reader authorization. No new tool or credential is installed.

To retain an index before dispatch, send `index` JSON under the observer profile
with exact scope, query, context pins, budget and `expansion_reader`. Load it with:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-observer.token" run-index \
  < retained-receipt-and-seal.json
```

The request contains `receipt_id` and `seal`. `POST /v1/run-index`,
`core.Store.RunIndex` and `localapi.Client.RunIndex` return the original package,
handles, reader and current remaining budget. They do not recompile, refresh the
15-minute expiry, bind a run or claim a launch. New optional notes leave the
retained index unchanged; changed indexed sources or mandatory context refuse.

Execute it by adding `--receipt-id UUID --seal EXPECTED_SEAL` to the run command
and supplying the same reader, query, kinds, budget and run context as capture.
Omit `--index` only for the existing body-package route. Optional `--semantic`,
`--browse` and explicit `--offset` declarations must also match the retained
index. No retained request can authorize a second launch of the same receipt.

## Budgets and limits

For observed index runs, `--tokens` bounds the initial memory presentation,
including tool guidance, using Cairn's UTF-8 byte upper bound. The task remains
separate from the compiler budget; the combined initial input must also fit
131,071 bytes. Oversized presentation or invalid tool declarations refuse before
binding. No truncation or automatic fallback to body compilation occurs.

The core expansion byte budget remains separate. Combined accounting across
initial input, expansions, later searches, system prompts and other model turns
is still open. Expired indexes refuse loading and launch claims; the runner also
checks the returned expiry immediately before starting the process. Pulls must
refresh when eligibility or expiration changes during the task.

The registered `context.txt` retains the canonical semantic package, as for body
runs. It does not contain the task, generated pull UUIDs or the live presentation.
The delivery digest covers the exact combined input forwarded to the child.
Native later searches use their own real session scope; their association with
an observed attempt is a separate host operation. Naming tools does not attest
that a particular conversation or model consumed them.

Migration 031 adds nullable reader metadata. Upgrade the API and CLI before using
this mode. Old API decoders reject the new request field and have no `run-index`
operation; old expansion readers do not recognize the designation. Ordinary
indexes without a designated reader and existing body execution keep their
contracts. No semantic format or native tool schema changes are required.
