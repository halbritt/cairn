# Hybrid runtime shared contracts

Campaign: CAIRN-301. Setup gate: X-01 / CAIRN-302. Revision: `shared/4`,
recording the `coordination/v7` deadline documentation correction on 2026-11-06.
The earlier `shared/3` accepted the downstream `semantic/4` and `release/4`
criteria pair; `shared/2` closed X-01 for setup on the same date. The overall
lead owns this index. Domain leads own their area documents and independent
reviewers assess the criteria against source before new implementation
consumes them.

X-01 is **closed for specification and coordination setup**. The overall lead
has reconciled the reviewed area contracts, consumer agreements and dependency
map below. Teams may implement ready work under their assigned ownership and
individual predecessor gates. This does not accept a runtime implementation or
authorize a production cutover; the later review and release gates still apply.

The [implementation plan](../hybrid-runtime-implementation.md) supplies the
full delivery sequence. Its tables remain authoritative for dependencies;
domain splits must retain those gates. Source and operational observations
have separate identities and timestamps. Installed version strings alone
cannot certify running clients or model coverage.

## Five contracts

| Contract | Accountable owner | Consumers | Required specification and fixtures |
| --- | --- | --- | --- |
| Lifecycle command inputs, streams and exits | Foundation, with Coordination contributing the full inbox and watcher command grammar | Coordination, Release, native harness adapters | Admission, recall/capture, native-session and ledger commands; required-context refusal versus optional degradation; input/output bounds, exact exit behavior; ordinary explicit retries and already issued live-session commands. |
| State schemas, locking and persistence order | Foundation for shared file primitives, ledgers and native-session state; Coordination for the sole journal transition algorithm | Both lifecycle teams and Release rollback | Existing schemas, modes, atomic replacement, lock scope, crash boundaries, write-before-send, stable request IDs, completion/reply references and pending-only automatic recovery. No duplicate journal transition algorithm. |
| Process identity and wake envelopes | Foundation for peer/process primitives; Coordination for wake adapters and supervision | Coordination, Release and supported harnesses | Ownership and stale-process rejection, bounded framing, active-attempt gates, busy/refusal/cancellation, process-tree shutdown and real process-boundary fixtures. |
| Semantic protocol and derivation identity | Semantic | Go supervisors, Release packaging and cutover | All three modes, original UTF-8 spans, tokenizer/pooling behavior, exact discrete score parity, vector tolerance, deterministic identity, fixed executables, offline packaging and database-wide writer exclusion. |
| Installation, pins, drain and rollback | Release | Foundation bindings, Coordination native state, Semantic package and installed clients | Immutable `/2` versus `/3` selections, actual drain, staged activation, unrelated-config preservation, races/crashes, supported live refresh, reverse lifecycle selection and semantic reverse rebuild. |

The area specifications are `foundation.md`, `coordination.md`, `semantic.md`
and `release.md` in this directory. Their independently accepted revisions and
commits are recorded below. A subsequent boundary change must name every
consumer, updated fixture and review before dependent code lands.

## Shared-file allocation

| Surface | Sole writing assignment | Handoff |
| --- | --- | --- |
| `cmd/cairn/main.go`, `cmd/cairn/commands.go` initial lifecycle registration | Foundation F-B1 / F-01 | Coordination implements separate handlers and supplies later registration requirements. Overall grants a later shared-file slot after Foundation lands. |
| Shared state-file and process-peer primitives | Foundation F-B1 | Coordination supplies behavior requirements and consumes the same implementation. Extract only with exercised consumers. |
| Journal execution, recovery and inspection | Coordination J-B1 / J-02, J-03 | Native-session integration consumes named journal operations/facts, with no copied transition logic. |
| J-04 shared peer-client addition and first wake consumer | Foundation F-B1 owns the shared client; Coordination J-B2 owns separate consumer files | Name nonoverlapping files and one combined candidate before coding. F-R1 and J-R2 review independently; integrate the exercised pair together, without an unused helper-only trunk commit. |
| F-04 compatibility caller, original-input plumbing and authenticated issued argv | Foundation F-B3 after the F-01 handoff | The [consumer decision](f04-consumer-decision.md#compatibility-closure-policy-and-caller-slot) includes `inbox_handoff`, cue rendering in `inbox_cue_text`/`inbox_context`, and checked compact-config selection. Coordination acknowledges the narrow delta before overlapping frontend rewrites; command/journal/watcher transition ownership stays with Coordination. |
| `scripts/install-semantic.sh` | Semantic S-B2 / S-04 | Release supplies host/architecture requirements and reviews staging/activation compatibility. A later writer requires explicit transfer. |
| `Makefile` initial Cargo and required real-worker targets | Semantic S-B2 | Other teams submit target requirements. After these targets land, Overall explicitly transfers ownership to Release for R-05 and final test wiring. |
| Service configuration, lifecycle installation and final activation | Release | Semantic supplies the binary/model/native-library manifest and fixed launchers. No production switch before R-06/R-07 gates. |
| This index, campaign dependency reconciliation and overall plan corrections | Overall | Domain leads edit only their assigned area contracts. |

Ownership covers concurrent writes, not unilateral interface changes. Each
builder still owns review fixes, local checks, queued integration, pushing main
and removal of their temporary branch/worktree. Contract-only commits can land
after their own review while X-01 remains open; they do not release dependent
runtime work.

## Decisions that all consumers must preserve

1. **The semantic fence covers the shared database queue.** `OpenIndex` and
   `Step` in `semantic/index.go` seed and claim `cairn.semantic_job` without a
   collection predicate. Publishing a derivation replaces that record's old
   document. Stop and join every writer sharing that queue before changing
   model generations. Include inactive but restartable alternate service
   definitions in the cutover inventory. Keep the current schema and test a
   reverse rebuild; neither uninterrupted dense coverage nor instant rollback
   is promised.
2. **Readiness and coverage are distinct.** Rebuild completion compares every
   eligible active current source's version and body hash with the selected
   derivation and passage rows. A ready query or empty queue is insufficient.
   Report partial coverage and observed fallback behavior accurately; do not
   infer per-record lexical fallback merely because the index is ready.
3. **A `/2` pin authenticates its Python bundle.** It cannot authenticate a Go
   executable. Preserve the legacy executor or explicitly drain and repin a
   reviewed `/3` descriptor. Retain immutable rollback artifacts and compatible
   state. New state versions require their own decision, tests and review.
4. **Foundation state support precedes memory consumers.** The
   [F-04 consumer decision](f04-consumer-decision.md) selects real binding
   activation through an independently pinned compatibility caller. F-04
   precedes F-02/F-03 and J-02 without waiting for J-05. Remaining grant
   operations land with their memory consumers; J-05 delivers native
   orchestration and its compiled journal adapter together. HC-03 includes
   full memory grant behavior; HC-04 accepts native-session transition policy.
   Domain contracts must agree on the exact adapter, failure order and
   compatibility receipt before dependent implementation.
5. **Explicit retries differ from automatic recovery.** Preserve ordinary
   unchanged explicit commands that retry through the authenticated API,
   including locally committed/refused slots. Ordinary watcher replay sends
   pending entries, with refused outcomes final for that recovery path. Preserve
   the existing `finish_reply` exception: when completion committed but its
   exact result reference is missing and no response exists, recovery retries
   that same completion to recover the reference before creating its reply.
   This is not permission to replay arbitrary settled commands. Existing
   live held/failed deliveries are never test material or replay mechanisms.
6. **The installed interface is the compatibility surface.** Preserve the full
   inbox grammar, issued commands and multi-binding watcher `--config-dir`.
   Semantic flags select one absolute executable without argv; fixed launchers
   select mode and pinned assets. Keep host-required adapters and frozen trials.

## X-01 closure checklist

- Current source/schema baseline and installation inventory are recorded,
  including managed hosts, supported operations, models, pins and live-client
  gaps. Observations identify their scope and time.
- Actual required models, efforts and subscription selectors are verified.
  Quota observations are fresh enough for allocation; percentages are not
  summed across overlapping windows or treated as reservations. Auth/quota
  failure stops the affected assignment without OpenRouter fallback.
- All five contracts have named owners, consumers, fixtures, exact revisions
  and independent Opus 5.5 Max criteria review. Blocking findings are resolved
  and changed criteria are re-reviewed.
- Foundation and Coordination agree on the callable journal/native-session
  boundary, without an F-04/J-05 cycle or duplicate journal state machine.
- Semantic and Release agree on package manifests, supported architectures,
  fixed launchers, the database-wide cutover fence and reverse rebuild.
- CAIRN-276 characterization is retained. CAIRN-277/278 are revised or
  explicitly superseded into the Go scope with evidence/history preserved;
  no duplicate Python rewrite is scheduled or falsely completed.
- Domain items have stable plan-key IDs and the campaign parent. Required
  dependencies are checked against the implementation tables. Native blocking
  edges are read back when available; otherwise explicitly unverified edges
  are enforced in the item bodies and campaign map.
- Shared-file allocations, initial six-builder cap and landing order are
  acknowledged. Independent existing-behavior work is separated from code
  that consumes pending contracts.

## Acceptance record

The following area criteria have landed with independent review. Required
cross-team deltas and consumer agreements are closed for specification scope.

| Area | Accepted revision and landed commit | Consumer agreement |
| --- | --- | --- |
| Foundation | `foundation/v4`, `8b7686496e78224cfedbccdde4ffde34c0f900c0` | Release/Coordination mirror the short launcher, full issued-command scope and sequenced caller slot; Foundation accepted the complete final Release delta. |
| Coordination | `coordination/v7`, `98a5efefaa8181cae5b9423096befb9ed1d955ca` | J-R1 `38677b6b-d500-485e-abf1-07d1b1ab38a5` v1 and J-R2 `c7acc0aa-8d6a-4498-8b55-864c9a36c960` v1 passed the deadline documentation correction; v6's consumer boundaries remain unchanged. |
| Semantic | `semantic/4`, `8d4d42b226110f50ec1ac48e56ba021d6a00ea46` | S-R1 `1189989e-fd48-4d75-b7ed-c20222fa509e` v1 and R-R1 `eab98590-d76e-4ca6-a4c3-90f33abf87b4` v1 accept the exact criteria. Release's consumer agreement is `f2f75fbc-12e6-46df-ba98-ff91147cf32e` v1. |
| Release | `release/4`, `4137d2c95d2964e4d48852620606ffce721626a7` | R-R1 `686e3997-6ace-4eb6-b059-c1211ea93b48` v1 accepts the paired criteria. Semantic's consumer agreement is `f1e9923e-acd1-4762-ad83-432d8ba3dce3` v1. |

The reviewed Coordination candidate `24aa8ef5f09734cbac4350c62f1266abeb2c78c9`
landed with blob `411ab0c30966a645eb4b0922dd93331bf389fec1` unchanged.
It records the existing explicit inbox CLI's 30-second lock wait and
30-second send timeout, and watcher/hook flushing's zero-second lock attempt and
10-second send timeout. The production `coordination.py` SHA-256 remains
`92716c54be54a9f2e0fbe4dbd5edc77aecb02d6f7da3422708828376e63b0116`.
This documentation correction changes no runtime policy, F-01/F-04 consumer
boundary or release gate and establishes no implementation acceptance.

For Coordination history, `coordination/v6` landed at
`c9a1908bacac65806f233d040bd0656c8b43f97e`. Both independent reviewers accepted
its compact/L delta, selected-config root, caller/thread custody and source
error policy; Release found no conflict. Those agreements carry forward
unchanged in v7.

The accepted Semantic candidate `49b4652c83d2b8a6e5761230db3273ed75063ef4`
and Release candidate `564a53216e9f3b78849910d1d1703e4b1eefc93e` landed with
their reviewed blobs unchanged: `a44b916cf7e48ed579c95f7b4d60fd1914788874`
and `e2ed2fe07344de428d5aaf02d714cf3a3a0f111f`, respectively. This downstream
amendment accepts the conditional maximum-document evidence criteria below;
it does not accept an experiment, harness, numerical result or runtime.

For X-01 history, `semantic/3` landed at
`ac3dee7a45592312782e3d8724fa9193f77d2a31`, with Release's agreement on
rollback, S-04R, native-package/ABI, fixed-launcher and database-wide fence
criteria. `release/3` landed at `a02da07c372c6418ff329da267473888bf97a781`,
with independent review and Foundation's whole-contract agreement on the
receipt/L contract and updated item bodies; Semantic confirmed the rollback
dependency. Those requirements remain in the accepted pair.

Root's final reconciliation checked current contract blobs, exact review and
consumer receipts, native parents, stable plan-key identities and body/map
dependencies. CAIRN-276 retains characterization; CAIRN-277/278 explicitly
supersede their Python implementation scopes into J-02/J-03 without discarding
history. Native blocking relations remain unavailable and unverified; the
recorded body/map edges remain mandatory. Current source/installation inventory
and subscription observations identify their scope and gaps. They do not claim
completed runtime qualification.

The initial six builders may proceed on ready work. Leads direct their existing
teams through implementation, exact-candidate independent review and complete
trunk integration. The four reserved builder roles may be activated within the
planned ten-builder maximum only when their named predecessors are accepted,
owned files do not overlap, and fresh subscription checks permit their routes.
No extra team or routing fallback is authorized. Shared CLI, Makefile and
installer writer transfers remain explicit.

Release setup criteria `release/2` were independently accepted at candidate
`58a83eb91d1693b06a7c44c5d725bb3c318ea9a2` and landed as
`aa0c26fa1a8ab8faeb93c183c112f21dcbfa7ab9` with an identical contract blob.
R-02 qualifies available F-01/F-04 staging, pins and state controls. R-06
qualifies complete watcher/config-dir/shared-user transitions after native
lifecycle prerequisites; the original release dependency edges remain.
Unresolved delivery-free `reply_possible` drain disposition and admission/writer
visibility are explicit gates before dependent settlement/barrier code review
or R-06 qualification, not claims of completed implementation. Default
post-retirement rollback retains an executable, offline pinned `/2` Python and
Python semantic closure; retirement removes active requirements, not that
rollback material.

Semantic S-04 also requires R-01's nonempty supported host/CPU inventory.
The separately classified S-04R / CAIRN-355 repair qualifies a telemetry-disabled
retained Python semantic launcher/rollback closure. It depends on X-01, S-01
and R-01, and gates S-05 rollback acceptance and R-06 qualification. Semantic
owns its implementation and S-R1 review; Release independently reviews rollback
compatibility. Keep the frozen numerical oracle, installed venv/model cache and
current production selection unchanged during characterization. Prefer a new
immutable launcher selection; if source changes alter derivation identity,
record that transition explicitly. Prove the selected closure's numerical
behavior and absence of telemetry-store writes or connection attempts through
isolated process observation. Rust-only evidence does not qualify Python.

The retained `"!" * 65536` 90-second Python observation used a direct external
supervisor; it was not an observed real-Go or production timeout. Its full
inherited environment was not recorded. The single at-most-180-second oracle
attempt is consumed and remains unqualified; its recorded environment and
output remain separate immutable evidence. Overall decision
`0ba29009-f422-4a11-a94b-018f1d3139b0` v1 permits a conditional route only for
that sealed case. The ordered steps in the
[Semantic contract](semantic.md#conditional-maximum-document-reference) and
the [Release evidence requirements](release.md#maximum-document-semantic-evidence)
govern the following summary.

Construction amendment `b8bde736-20e4-4957-88f3-2acc7109e4fb` v1 changes only
W's existing RW bind host-source operand to one fresh, empty, owned `0700`
scratch directory outside the frozen artifact root. Preserve the `--bind`
flag, guest `/sandbox` destination and all other prefix arguments, guest paths,
namespace/settings, environment and mount flags, apart from declared read-only
tool/input additions. This is a declared derivation of W, not byte-identical
reproduction. Record original/derived argv and the exact diff, verify backing
ownership/mode/emptiness without symlink substitution or overlap/alias, and
preserve every old artifact and directory. New logs, results and receipts stay
outside the frozen root; needed old evidence is mounted read-only. Retain
before/after seal and ownership/mode checks and cleanup evidence; remove only
the newly owned scratch after workers and descendants are joined. This
exception grants no new attempt, budget reset or numerical equivalence.

Against that derived construction, pin and match the complete 294-text
baseline: check exact int64 token IDs and lengths before inference, then exact
raw CLS digests. Keep the frozen sources/assets, interpreter/provider, batch 1,
ORT settings and tensors. B1 then permits exactly two additional passage
executions, A = `"!" * 288` and B = `"!" * 256`, capturing raw CLS and normalized
output from each same execution. Preserve the specified row hashes, per-input
tensors, A-to-rows-0..254/B-to-row-255 mapping, and actual-worker loaded
ORT/NumPy/OpenBLAS mappings and hashes. Before inference, prove network and
mapped OS-home/cache isolation, including `getpwuid` and `expanduser` with
HOME absent in the relevant namespace. New raw outputs and maps are new
references, never reconstructed historical evidence. Corpus-bounded proof and
batch-1/stateless mapping do not prove 255-repeat determinism or general source
equivalence; contradictions defeat the disposition. The concrete O-4 through
O-6 source-equivalence arguments remain input-specific and independently
reviewed.

After the complete B1 proof passes, including the 294-text match, both
normalized row hashes and actual-worker maps, and the exact S-01b
isolation/recording harness candidate is independently accepted, B2 permits one `operation=document`,
`"!" * 65536` request through the selected installed pre-repair fixed Python
launcher and unchanged Go `CommandEmbedder` with its actual frame, sanitized
environment and 90-second timer. This is not S-04R's staged launcher. Normal
startup/identity exchange permits no extra model/document probe. Repeat the
network and HOME-absent OS-home/cache proof in the actual Go child's namespace;
B1 proof does not transfer. Bind installed launcher/source/interpreter and Go
identities, actual child mappings, frame/environment evidence and read-only
runtime before/after digests. Keep isolation overhead separate from Go's timer.
Preserve the outcome, cancellation and joined cleanup, and seal each B1
reference set and B2 record with digest manifests. Compare any B2 reply with
the sealed oracle and escalate differences while retaining both references.

Every attempt, including preflight, is recorded. Failed proof or a failed,
uncertain or interrupted attempt ends that experiment with its gap retained;
further execution requires a new Overall decision. No new 180-second oracle,
deadline inflation, automatic or manual retry, production action, source-oracle
repair, backend, tolerance or role change is authorized by this route. The criteria
reviews and mutual agreements above close specification scope only; actual
experiment evidence receives separate independent review before numerical
coverage is counted. Experimental outputs never establish index coverage.
Until a valid Python real-Go document reply exists, a Rust reply within 90
seconds requires Overall's population disposition with Semantic/Release review;
blocked, failed or unmeasured Python runs never establish equal populations.
All 256 Rust vectors and spans, bit-identical raw-output gates, pairwise
`<=1e-6` comparison, exact integer scores/order and unchanged real-Go deadlines
remain mandatory. No coverage, runtime, R-05/R-06 or deployment gate is passed
by this criteria acceptance.

J-01 / CAIRN-332 and CAIRN-276 are Done with characterization landed at
`cff2061eaaef83956a2ef4e8ddf48a249fd7ddc7`. The
[characterization record](../../verification/2026-11-06-j01-inbox-characterization.md)
retains J-R1 O-2 and the v6 C1/C3 J-F1 cells as post-F-04 obligations carried
by J-02; their later verification is not claimed here.

X-01 closes setup only. S-01 evidence, the remaining coordination obligations,
each implementation's tests and review, remaining release-policy gates,
actual runtime/ABI qualification and production acceptance remain with their
downstream items.
No runtime implementation, production switch, burn-in or retirement has been
accepted by this record.
