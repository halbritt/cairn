# Cairn

Cairn carries useful, scoped memory between agent runs. It stores ordinary notes,
evidence-backed claims, and authorized instructions in PostgreSQL, then compiles
a bounded context package before a task starts.

**Status: usable local alpha.** Remember/search, manual authority and evidence,
context compilation, process wrapping, historical recompilation, authenticated
local access, bounded index/pull, evidence-attached review, and outcome joins work.
A [real OpenCode repair trial](docs/verification/opencode-recurrence-2026-09-07.md)
compared three context conditions; none produced a repair within its budget.
Runtime mediation and measured memory benefit remain unestablished.

The [roadmap](docs/roadmap.md) tracks accepted requirements, remaining gaps and
acceptance evidence.

## Get started

Requires Go 1.25+, PostgreSQL 16+ client/server binaries, Bash, Python 3, and
`flock`. Run as an ordinary user. The local store uses its own private Unix
socket and opens no TCP listener.

```sh
make build
bash scripts/local-store.sh start
bin/cairn remember 'When changing Cairn storage, run make test-integration.'
bin/cairn search 'changing storage'
bin/cairn run --prompt 'Inspect the supplied note' -- /bin/cat
bin/cairn report "$PWD"
bin/cairn use-report "$PWD"
```

The example wraps `cat`, so you can inspect the exact input without a model or
provider credentials. The repository defaults to the current directory. Notes
created by `remember` apply to all tasks and runs in that repository; empty scope
bindings are never treated as wildcards.

The default database and run artifacts live under `~/.local/share/cairn`.
`CAIRN_HOME` changes that directory. `CAIRN_DATABASE_URL` selects an existing
**dedicated Cairn database** for the CLI; it does not redirect the local-store
script. Use `CAIRN_PG_BIN` to select PostgreSQL binaries when necessary.

```sh
bash scripts/local-store.sh status
bash scripts/local-store.sh backup
bash scripts/local-store.sh stop
```

`start` also applies checksummed forward migrations. Back up before upgrading an
existing store. The database retains captured evidence bytes and semantic
packages, so a PostgreSQL backup covers those. Run-directory copies require
separate retention accounting. No backup rotation, timer, or automatic deletion
is enabled.

## Use an agent harness

For a remote model, explicitly allow a note to be delivered outside the local
machine when you create it:

```sh
bin/cairn remember --shareable 'Run make test-integration for storage changes.'
bin/cairn run --destination hosted --carrier argv \
  --query 'storage tests' --prompt 'Review the storage changes' -- \
  /path/to/opencode run -m provider/model
```

`run` appends the compiled context and task to the command for `argv`, or writes
it to the command's stdin for `stdin`. It executes argv directly, without a
shell. Use `--dir` for the process working directory and `--repo` for memory
scope when they differ. Only shareable memory reaches the hosted package.
The command's own filesystem/network access is outside this memory filter.

The `--tokens` value is the actual input room you reserve for memory after
allowing for the task prompt, tool schemas, session history, and output. Default:
32,000. The compiler limits optional memory to 10% of that room, capped at 6,000,
and counts UTF-8 bytes as a conservative token upper bound. Required instructions
must fit completely. Unsupported mandatory runtime enforcement blocks launch.

Process output streams to stdout/stderr. The final Cairn receipt envelope goes
to stderr. Cairn records process exit, timing, and output digests, without keeping
raw model outputs. Task and query text are transient: new semantic v2/v3 packages
retain only a SHA-256 query digest. Digests are not anonymization, and old v1
receipts retain their original query text unless their containing package is excluded through explicit [record forgetting](docs/deletion.md).
Per-run `context.txt` and `outcome.json` live in the owner-only
run directory named in the receipt. New context files are [registered for controlled deletion](docs/managed-context.md). A failed DB write retains
`outcome.pending.json` for `cairn recover-run RECEIPT_UUID` recovery. Do not retry a
process blindly: the same compile request UUID cannot launch twice.

The wrapper records supplied context as `available`. The separate OpenCode probe
established model-request contact for its tested fixture. Neither observation
proves obedience, internal tool coverage, compaction behavior, or task acceptance.
User native-memory files are left in place and their influence is declared
unisolated.

## Inspect memory and outcomes

```sh
bin/cairn list "$PWD"
bin/cairn get RECORD_UUID
bin/cairn search --purpose planning 'placement or planning question'
bin/cairn impact RECORD_UUID
bin/cairn replay RECEIPT_UUID
bin/cairn explain RECEIPT_UUID
bin/cairn preview-retract RECORD_UUID
bin/cairn docket "$PWD"
bin/cairn report "$PWD"
```

Planning, placement, capability, and security retrieval excludes Class A notes.
Consequential Class B reads require resolvable supporting evidence and valid
authority. Advisory reads can include degraded B with its evidence state visible. A revoked
parent grant invalidates its descendants at the next compile. Class C
instructions are issued through a separate authorized path. Disputed optional
material is omitted whole; binding conflicts refuse compilation.

`replay` is labelled historical and verifies the retained canonical package and
BLAKE3 seal. It cannot authorize a fresh delivery. `impact` reports exposure, with
pagination/truncation metadata; it does not claim causal influence. The report
keeps exit-zero observations separate from unknown task outcomes.

The docket surfaces attribution contradictions, evidence-unavailable B records,
launches lacking outcomes, and matching A notes blocked from consequential use. An unfinished run may still be executing. A
manual inspection is required before declaring it abandoned.

The [use/outcome loop](docs/use-outcome-loop.md) documents joined observations,
versioned task assessments and completed-task delegate findings.
[Authenticated local access](docs/local-api.md) gives agents and host observers
scoped Unix-socket access without the operator CLI or database credentials.

## Authority and structured commands

The CLI is trusted local operator administration, identified by its OS UID.
Embedded orchestrators supply a `core.Channel` after authenticating their caller;
request JSON cannot choose identity, witness, or operator status. Agents must
not receive database credentials or the operator CLI. This is a light local
trust model, not isolation against hostile processes sharing the operator's OS
account.

Initialize the root grant once:

```sh
printf '%s\n' '{"request_id":"1a8dd335-e71b-44f5-a0b3-65e391c22259","reason":"Install the local Cairn operator root"}' | bin/cairn bootstrap
bin/cairn grants
```

The same request UUID safely retries the bootstrap. A different request cannot
install another root. Promotion requires a live grant, deliberately captured
evidence, and an actor absent from the record's producing/editing history.
Attributing work to another agent cannot defeat that check. Grant chains have at
most two delegated levels. Direct instruction authoring requires the `issue`
capability and a policy key.

JSON request commands include `create`, `edit`, `compile`, `capture-evidence`,
`grant`, `revoke-grant`, `promote`, `issue`, `correct`, `retract`, `dispute`,
`resolve`, `forget`, and `usage`. See [request examples](docs/commands.md).
Use new request UUIDs for new intent and reuse them for transport retries.
Mutation retries return the original result; use `get` for current state.

Ordinary edits retain previous versions and require `expected_version`. They
cannot change scope or sensitivity. Corrections replace active B content with
new evidence references and an audit link. C changes use retraction followed by
new issuance. Retraction requires a caller-owned `preview-retract` token, valid
for one hour and invalidated by content/version changes or new exposure. The
preview covers retained versions, known versioned dependents and their runs
(at most 1,000 versions and 1,000 uses). See [relations and demotion](docs/relations-and-demotion.md). Explicit conflict resolution retains the original members and
reasoned audit history.

## Verify changes

```sh
make test-integration
make test-lifecycle
make check
```

The integration target starts and removes its own temporary PostgreSQL cluster
and uses Go's race detector. It covers migration from the original schema,
concurrent edits/retries, grant revocation races, evidence and destination gates,
conflicts, package seals, process launch/timeout, and duplicate-launch refusal.
The lifecycle target checks startup, catalog-backed backup/restore, replay,
recompilation, restored-handle invalidation, and restart. It also checks
[external recovery expectations](docs/recovery-inspection.md) against a real
dump taken before later revocation and forgetting.
Neither target touches the running local or host PostgreSQL instance.

To repeat the real OpenCode ingress probe without provider credentials:

```sh
CAIRN_OPENCODE_BINARY=/path/to/opencode make test-integration
```

`make test` explicitly skips DB tests unless `CAIRN_TEST_DATABASE_URL` is set.
Only point that variable at a disposable database. Initial dependency downloads
need an enabled Go module proxy; dependencies and CI actions are pinned.

## Remaining work

The [decision record](docs/decisions/0002-local-memory-loop.md) settles the
operating defaults. Remaining implementation includes real host lane integration, broader intent
matching, evidence lifecycle jobs,
redaction/deletion effects and retention, richer conflict delivery, host-level context budgets, real-history usefulness trials, and Striatum orchestration wiring.
No automatic grooming, learned ranking, or automatic promotion is running.
Do not retain secrets until redaction and recovery obligations are implemented.

The [initial design evaluation](docs/design-review.md) is historical; the
[implementation status](docs/implementation-status.md) records current evidence.
