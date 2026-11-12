# Semantic transition fixtures and Release runbook

S-05 / CAIRN-328 supplies a disposable Python → Rust → the same retained Python
transition and this mode-preserving runbook. The finite deliverable follows the
2026-11-12 campaign closure decision and uses the retained real-worker evidence
through case 8. The remaining scenarios and
measurement limits below do not commission another sampler or receipt series.
The [semantic contract](plans/hybrid-runtime-contracts/semantic.md#packaging-index-transition-and-rollback)
defines the population and writer-fence requirements. Release owns installed
configuration, activation and retention; production changes still require the
owner's grant.

## Run the disposable harness

The default matrix runs ten cases and 32 test entries using deterministic Go
protocol workers, the actual Go indexer/API and disposable PostgreSQL with
pgvector. Its artificial worker identities supply no native-model qualification.

```sh
mise run host:exec --timeout 15m --weight 1 -- \
  python3 -I -B scripts/check_semantic_transition.py \
  --artifact-dir "$HOME/.cache/semantic-transition-$(date +%Y%m%dT%H%M%S)"
```

To exercise the competing-writer round trip with already accepted immutable
Python and Rust artifacts, use a fresh output directory:

```sh
mise run host:exec --timeout 25m --weight 1 -- \
  python3 -I -B scripts/check_semantic_transition.py --real-case8 \
  --artifact-dir /absolute/new-case8-artifacts \
  --python-selection /absolute/accepted-python/qualified-selection \
  --rust-fixtures /absolute/accepted-rust/observed/fixtures
```

The host entry point calls the unchanged
`scripts/test_hybrid_runtime_operations.py` wrapper. It preserves normal host
tools and uses a private network/PID namespace, protected home and owned private
PostgreSQL cluster. Fixture database cleanup cannot select a production database.
Accepted package/launcher bytes are checked before execution; the Python stage
is read-only and the Rust copy retains its original private layout. Do not
regenerate selections or substitute aliases to make a run pass.

Retain `execution-inputs.json`, `isolation.json`, `result.json`,
`artifacts/report.json`, `artifacts/tests.jsonl`, source inventories, executable
digests, per-direction phase reports and worker/effect observations. A successful
wrapper exit alone is insufficient: selected test membership, zero failures or
skips, source hashes, native identity/environment, every traced root's full
runtime sample and nonempty effect-detector positive controls must all pass.
`--case8-controls` runs the same Go API/SQL sequence with deterministic protocol
workers and is explicitly non-model evidence.

## Evidence and remaining limits

| Scenario | Bounded evidence supplied |
| --- | --- |
| Forward and reverse rebuild | Actual fixed Python → Rust → original Python launchers, three collections, exact rank/embed identity and source/history preservation. |
| Mid-rebuild restart | Real owned API/worker stop and join, retained leased jobs, same-generation restart and complete recovery. |
| Worker death and earlier deadline | Actual owned PID faults and preserved production deadlines; failure/retry populations remain visible. The fault is not an observation inside inference. |
| Edit during embedding | A validated old result held at publication cannot publish a superseded version/body hash. |
| Delete/forget during embedding | Real store mutations prevent publication and exposure of excluded sources; this does not qualify physical data purge. |
| Expired lease and retry backlog | Retained lease identity, attempts and retry state; controlled timestamp advancement and replacement publication. This is controlled contention, not a natural-load measurement. |
| Partial versus unavailable | Actual API/compiler exposure and recovery, startup unavailability and cold query-worker restart. Case 7's early-startup observation is restricted to that case and is not a full runtime sample. |
| Competing writers and inactive definitions | Both switch directions, two real old owners sharing a queue and a separately refused inactive restartable definition; complete closed-fixture inventory, joined fence and exact rebuild. |
| Unembeddable population | Deterministic refusal coverage; real Python/Rust failure-set equivalence is not established by this transition work. Unresolved records still prevent a complete rebuild. |
| Default selection and Release handoff | Default/explicit selection has deterministic coverage; real round trips include whole-API restart. The configuration and rollback procedure below preserves mode. Installed activation and production availability remain separate. |

Case 8 registers eight sealed physical configuration/start definitions before
execution: two old owners, an inactive alternate and a selected next owner for
each direction. Every start goes through that exact registry. Unknown, omitted,
duplicate or changed definitions refuse the fence. Collection configuration
does not partition the shared queue. The inventory covers this owned fixture,
not installed services, timers, containers or manually launched host writers.

Both old owners produce real validated document results held by a private
PostgreSQL advisory-lock trigger immediately before document insertion. The
fixture observes their blocked database calls, distinct live leases and full
worker samples. It refuses both active owners, the remaining collection's owner
after the first is joined, and the inactive restartable alternate separately.
Only disabling all definitions, joining API owners/calls and reaping their two
sampled direct worker children allows the next generation. Deeper descendant
evidence relies on the sampler and namespace teardown. Controlled
`application_name` tags attribute the
fixture audit; they are not authenticated host process identities.

One disposable database serves both directions. Three ordinary source records
start the forward transition; three more ordinary `core.Store.Create` calls
after its final fence provide work for the reverse owners. The reverse
denominator is six, and the original three source/history/producer rows remain
exact across the entire round trip. After a joined fence, fixture-only writes
may make retained `lease_until`/`retry_at` timestamps due. Full OLD/NEW audit rows
must preserve lease IDs, attempts and all other job fields. Jobs are never
cleared or reinserted to obtain completion. Normal selected-generation rebuilds
must publish each current source/model/version/body hash exactly once, with no
old-owner publication after the old generation's joined fence.

## Completion gate

Use a consistent database-wide snapshot of every active record's current
version with `payload_deleted_by IS NULL`, across every collection. Query scope
is not the rebuild denominator. For each source require:

- Exact record ID, current version and SHA-256 of the original UTF-8 body.
- The selected embedding derivation identity in `semantic_document`.
- Between 1 and 256 passages with ordinals exactly `0..n-1`.
- Positive in-bounds spans whose original byte slices are valid UTF-8.
- Exactly 384 finite vector components and a finite, nonzero norm.

The read-only, repeatable-read checker in
`semantic/transition_acceptance_test.go` retains unresolved IDs/reasons and queue
attempts, retry times and lease state. Its negative fixtures cover missing
passages, wrong model/body/version, invalid spans, ordinal gaps, dimensions,
nonfinite values and zero vectors. Empty queues, ready state, document counts
and successful queries alone cannot establish completion. Keep blank,
token-empty, unembeddable, failed and backed-off sources in the denominator;
never delete or re-scope them to obtain a pass. Partial dense coverage does not
promise lexical fallback for every missing vector.

An earlier caller deadline can leave a lease until expiry because retry
scheduling uses the expired context. `OpenIndex` seeds missing current-version/
model documents; it does not automatically repair a wrong body hash or missing
passages in an existing document. Report such a concrete derived-state defect
and stop the full-completion claim; this harness supplies no installed repair.

## Release cutover and rollback

1. Retain accepted package/ABI evidence and the exact original Python closure:
   interpreter/venv, worker sources, prepared model/config, native libraries,
   fixed launcher bytes and pre-cutover rank/embed identities. Record the
   database identity without credentials and preserve operational source data.
2. Inventory every active or restartable writer sharing that database/queue,
   including alternate collections, inactive units, socket/timer activation,
   containers, supervisors and manually launched indexers. Record configuration
   and launcher digests, mode/model, process/child ownership, restart policy and
   stop owner. Unknown writers are inventory gaps.
3. Disable restart/activation, stop old owners and join indexing, active calls
   and both worker lanes. Retain exit/reaping evidence before changing
   generation. An empty queue, expired lease or idle child does not prove this
   fence. Apply the same exclusion on rollback.
4. Prepare the reviewed before/after service configuration. Keep the existing
   mode and replace only its absolute immutable fixed-launcher path:

   | Existing mode | `cairn serve` selection, for either Python or Rust |
   | --- | --- |
   | rank | `--semantic-command /absolute/immutable/launchers/ADDRESS/rank` |
   | stream | `--semantic-stream-command /absolute/immutable/launchers/ADDRESS/stream`, preserving the existing idle timeout |
   | embed | `--embedding-command /absolute/immutable/launchers/ADDRESS/embed` |

   With no semantic option, the default remains semantics disabled. Fixed
   launchers accept zero arguments; do not supply them through the separate
   `RustWorkerConfig.executable` route that appends asset arguments. Do not
   change mode, defaults, timeouts, environment, thread caps, model, tolerances
   or select a mutable `current` symlink as part of this path-only delta.
5. After the joined fence and the required production grant, Release applies
   its configuration and restarts the combined API. Record whole-API outage
   independently from semantic unavailability, partial coverage, worker startup,
   cold query-lane behavior and rebuild completion. Apply the full population
   gate; retain every unresolved source and lease/retry state.
6. To reverse, disable/stop/join Rust owners, select the exact retained Python
   configuration, verify both original identities and complete the same
   database-wide reverse rebuild. A fresh dependency installation or previous
   Rust generation is not the Python rollback closure. Retain that closure and
   operational source data until Release's retirement gates permit otherwise.

The retained [S-04 package](semantic-package.md#qualification-decision-on-2026-11-12),
[S-04R Python closure](semantic-python-closure.md#accepted-campaign-target) and
[HC-01 comparison](semantic-package-comparison.md#hc-01-acceptance-on-2026-11-12)
are accepted for the campaign's tested `x86_64-unknown-linux-gnu` target. The
maintained Linux host roster is in the infrastructure repository, and the
selected retrieval-quality fixture is `core/testdata/retrieval-quality.json`.
These are established inputs rather than new owner blockers. Acceptance of the
tested package does not observe installation on every roster host. Release
verifies destination ABI, selected bytes, configuration and rollback before its
authorized rollout; production activation and HC-02 remain held for R-07.

## Observation limits

This is local software evidence. It grants no production activation, fleet
capacity, numerical parity, latency, zero-outage or instant-rollback guarantee.
Earlier numerical evidence does not automatically transfer to different
package/runtime/thread-cap identities. Maximum-byte synthetic boundaries,
failure-set equivalence and other unmeasured behavior remain explicit limits;
they do not require an open-ended transition campaign.

Sampler exceptions retain their complete traceback, context, child identity and
source/command binding immediately in numbered `sampler-error-*.json` records.
The before-cleanup snapshot and final partial observations separate sampler,
cleanup and diagnostic-write failures. A timeout cannot replace the primary
error; partial observations cannot pass the full-root identity/effects gates.
A case 8 sampler exception stops that case for diagnosis, without a retry
series. The earlier sampler failure's underlying cause remains unknown.

The retained diagnostic limitations are:

- R-1: The five-second sampler join can fail on a slow or hung sampler.
- R-2: Killing the direct strace child can leave tracees until wrapper PID
  namespace teardown; this is not independent descendant termination proof.
- R-3: Positive process-group cleanup, failing kill/reap wait, stderr-write,
  `SystemExit` and before-cleanup-write branches remain untested.
- R-4: Unsafe PID refusal precedes the before-cleanup snapshot; primary sampler
  errors otherwise retain their own diagnostic records.
- R-5: Cross-thread snapshot copies rely on the installed GIL-enabled Python;
  free-threaded Python is unqualified.
- R-6: Sampling stops on its first exception; later cleanup/write errors remain
  separate. A monitor-only exception has no numbered sampler-error file.

Full argv is retained but only argv[0] is compared. The case 7 early-startup
exception measures its own image/environment, not initialized Python/native
runtime mappings. Fixture testimony and parent start ticks are not independent
host measurements. Missed sampling windows fail closed; observed timing does
not establish reliability. Mapped runtime identity binds path/inode without a
device-qualified comparison. Worker stdout capture retains its existing prefix
limit. Cache-cold behavior, inference-frame interruption and full installed-host
inventory are not established by these runs.
