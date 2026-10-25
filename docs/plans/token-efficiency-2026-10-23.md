# Cairn token and turn efficiency: evidence and remediation plan

Date: 2026-10-23 (measurement window ends 2026-10-24 00:00 UTC).
Status: investigation and execution backlog; no production optimization deployed.

## Outcome and scope

Cairn has demonstrable interaction overhead. Short notice handling repeatedly
starts frontier-model work to read routing context, read a source, acknowledge
it, and report success. Memory presentation also spends substantial space on
control and provenance rather than source text. Selected capture introduces a
separate model cost which the current retained capture status cannot quantify.

The optimization unit is **tokens and model responses per useful, correctly
completed workflow**, including sender, recipient, coordinator and auxiliary
models. Provider response, native turn, durable delivery handling and accepted
task outcome are different units. This investigation establishes costs and
mechanisms; it does not establish that every notice or retrieved note was useless.

Owner authorization covers investigation, bounded instrumentation/experiments,
planning and actual Plane backlog creation. Full remediation is future work.
The owner explicitly selected the existing CAIRN project on the work instance
after inspection found no Cairn project in plane.harm.org's `harm` workspace.
The destination is workspace `proximal`, project
`afa5d2cc-95d4-4377-8cea-769f6b479c84`, workspace UUID
`3753821c-0d30-4f2d-955e-1f4972cc5159`.

## Evidence boundary and reproducibility

The [aggregate baseline](../verification/token-efficiency-baseline-2026-10-23.json)
contains no source bodies, conversation text, credentials or individual native
session identities. The read-only [analyzer](../../scripts/audit_model_overhead.py)
and [accounting tests](../../scripts/test_audit_model_overhead.py) are retained.
Private sample manifest, per-turn metrics and intermediate summaries are under
`~/.cache/cairn-token-audit-20261023/`; do not attach those files to public issues.
The original native logs remain in their installed account homes.

Source inspected: `426c560db174f3389998b974d915b70a58cc6691`.
Installed CLI and local API both report clean
`37ad2df3604a86d4c0c42dba85f3dfcdc637f494`, protocol 2. At inspection, these
core event, MCP and relay files had no diff from that API revision; lifecycle
Python had advanced separately. Deployed coordination matched source SHA-256
`c50cc32315a7dc802f817e0641c567ba8a0d30ff131f6b273ff271c41fa012e3`.
All five Codex/Claude inbox bindings were enabled and pinned engine
`cad93847f313b20e7a734ae41d694c3b015403a1216287209b0e8332d50f3e1c`
and bridge `d93d44ca3a7dbcd09886adea9c4a6ef4e948e3c5b739f2f170e959ace6c8d943`.
Repository HEAD, installed binary and loaded native session are not interchangeable
deployment claims. Recheck pins before implementing any ticket.

Sample: 2026-10-21 00:00 through 2026-10-24 00:00 UTC, 40 JSONL files, eight
most recently modified eligible top-level files per account home at selection,
then frozen in a private manifest. Homes: `.codex`, `.codex-harm`, `.codex-3`,
`.claude`, `.claude-harm`. Excludes `subagents` directories, remote-host native
logs, and nonpersistent selector subprocess logs. This is a convenience sample
of active and long-running retained sessions, not representative random sampling.
It covers ordinary collaboration, idle wakeups, active work, bursts, requests,
responses, memory calls, compactions and recovery-related traffic. No new live
provider experiment was launched and no production database was used for tests.

Observed Codex versions: 0.163.0 and 0.164.0; models include GPT-6 Astra,
GPT-6.1 Sol and GPT-6 Luna. Claude logs span 2.1.303–2.1.310 with gaps;
models include Opus 5.5, Fable 5.1 and Sonnet 5.5. These are mixed historical
configurations, not controlled arm comparisons. Model/effort, account, hook pin
and request-shape epochs must be fixed in future paired trials.

Accounting:

- Codex: deduplicate `token_usage_record` by response ID and use its `usage`.
  Do not also sum `event_msg.token_count`, thread totals or turn totals.
- Claude: repeated streaming blocks share message ID; keep the last observed
  usage for that message. Input is input + cache creation + cache read. Thinking
  is a subset of output, not an additional cost. Missing thinking stays unknown.
- Whole-response usage is provider-reported telemetry retained by the harness,
  not a provider billing export or a causal attribution to Cairn alone.
- Native `task_complete`/`turn_duration` establishes observed turn end. A joined
  local journal with `committed` acknowledgment/completion establishes that the
  helper retained a successful API result. Neither proves useful task completion.
- Text attribution uses tiktoken 0.12.0 `o200k_base` only as a disclosed proxy.
  It is not a verified tokenizer for these deployed models. Images/base64 are
  excluded from text counting. Raw tool-result logs can contain image data and
  host truncation metadata; counting their JSON bytes as input tokens is invalid.
- Claude request `keep`/`tail` references and retained blobs expose repetition,
  but most reconstructed requests lack some blobs. Only 19 of 4,407 requests
  reconstructed completely. Partial totals cannot establish complete context
  allocation or exact marginal token savings.
- The sample includes mixed owner/channel work. Long wake-associated turns are
  not all coordination overhead. Boundary-crossing turns are censored; zero
  observed usage does not prove a free turn. Hashes and IDs are counted locally,
  and duplicate usage IDs across files must be checked when repeating the audit.

Reproduction with an existing private manifest:

```sh
uv run --with tiktoken==0.12.0 python scripts/audit_model_overhead.py \
  --since 2026-10-21T00:00:00Z --until 2026-10-24T00:00:00Z \
  --manifest "$HOME/.cache/cairn-token-audit-20261023/sample-manifest.json" \
  --coordination-state "$HOME/.local/share/cairn/coordination/state" \
  --output "$HOME/.cache/cairn-token-audit-20261023/recheck.json"
uv run --with tiktoken==0.12.0 python -m unittest discover \
  -s scripts -p test_audit_model_overhead.py
```

Omit `--manifest` and use `--write-manifest` for a new bounded sample. Historical
files can be appended or pruned; a frozen path list does not make a filesystem
snapshot. Future trials need explicit stable snapshot bounds and request IDs.

## Actual architecture and model-work map

| Surface/path | Responsibility and model cost boundary | Source entry points |
| --- | --- | --- |
| CLI / MCP / native tools | Authenticate to API; expose memory/results to agent. CLI command scaffolding and printed JSON cost tokens only when supplied to a model. MCP schemas may be eagerly loaded or deferred by the harness. | `cmd/cairn/agent.go`, `mcpapi/server.go`, `mcpapi/retrieval.go`, `internal/indexview` |
| Local API and PostgreSQL | Authoritative records, receipts, event fanout, leases, result handling, attribution, restoration fencing. No generative model needed for these transactions. | `localapi/server.go`, `core/agent_events.go`, `core/agent_sessions.go` |
| HTTPS proxy / joining-host relay | Central HTTPS terminates at the deployed proxy; joining relay forwards local Unix requests to one verified origin. No model call, no result cache, no automatic request replay. Network failure can indirectly cause agent recovery work. | `localapi/relay.go`, `docs/multi-machine.md` |
| Native coordination | Register/resume identity; watcher heartbeats; checks queue; prompts eligible idle sessions; native hooks own delivery and renew leases; journal wrapper sends selected settlement commands. Emitted identity/control/wake text and model commands/results cost tokens. | `integrations/lifecycle/coordination.py`, `claude_channel.py`, `codex_queue.py` |
| Native inbox recall bridge | Checks admission, required context, exact source, live lease and binding. Already inlines eligible request source; notice/response uses required-only memory and separate source-read instructions. No hosted selector in this route. | `integrations/lifecycle/inbox_recall.py` |
| Ambient memory | Builds lexical/semantic query, ranks/filters under store rules, eagerly inspects candidates, renders bounded context. `agent_tools` delegates relevance judgment to task model. Other modes can invoke a selector. | `integrations/lifecycle/memory.py`, `core/compile.go`, `semantic/` |
| Selected capture | Separate tool-free Claude request on changed bounded dialogue; previous checkpoint and candidate notes supplied; selected writes retained. Nonpersistent subprocess means ordinary main-session usage misses its tokens. | `memory.py: capture`, `select_json`, `record_capture_status` |
| Scheduling and pools | DB-time scheduled publication; fresh-worker supervisor starts request workers and controls process cleanup. Existing-session notices can wake existing models; notices do not launch fresh pool workers. | `docs/event-scheduling.md`, `docs/agent-wakeups.md`, `docs/worker-pools.md` |
| Other harnesses / SDK | OpenCode request-copy injection and tool cue; Hermes pre-turn hooks/queues; Agy invocation/idle hooks; SDK memory brokers and spawn proxy compose these boundaries. Installed capability is not proof of equivalent accounting. | `integrations/opencode/`, `integrations/hermes/`, `docs/sdk-memory-dispatch.md`, `docs/sdk-spawn-proxy.md` |

On the observed host, local API, presence watcher, scheduler and seven worker
services were running. The API launches a local embedding worker; the API's
loopback listener is published by a Tailscale HTTPS proxy. Eight coordination
bindings exist: three Codex, two Claude, Agy, Hermes and OpenCode. The live session
directory snapshot contained Proximal Codex/Claude sessions only. Archon relay
deployment is documented, but its live usage was not observed in this audit.
Do not extrapolate local frequencies to every machine/subscription.

Semantic discovery uses a local embedding model (BGE passages in inspected
receipts), so it has CPU/model inference cost but no demonstrated hosted-token
charge. No automatic generative grooming or classifier cost is assumed merely
because a subsystem exists in the design. Governed authority and mandatory
instructions remain outside optimization by omission.

## Quantitative baseline

### Delivery generation versus model handling

Across retained inbox files modified within the window: 2,630 contexts, comprising
1,832 notices, 395 responses and 403 requests. Counts include Agy and unsampled
sessions; modification time is not an authoritative delivery timestamp. Notice
metadata median: 2,568 bytes; response: 2,570; request: 3,320. Two additional
deliveries repeated the same source version to the same recipient; this does not
establish an error or semantic redundancy. Different sources can still repeat
the same information, which this identity check does not assess.

The earlier latest-100 `codex-two` sample was a valid observation at that instant,
but is not the denominator for this audit. The latest set changes during use.
Generated context size is not model ingestion. Native logs independently show
inbox metadata in tool-result text, and request source injection in developer
context. Full tool-result size is an upper bound when output contains unrelated
blocks or host truncation. It must not be summed with provider input as another
cost category.

Joined native-turn evidence: 666 notices, 154 responses and 121 requests.
Retained journals showed 818 committed `ack` operations and 119 committed
`complete` operations, all with handled disposition; four had no committed
settlement in this snapshot. All 119 completed requests also had a committed
response journal. This establishes bounded transport workflow closure, not
correct execution of those requests' substantive tasks.

| Wake-associated category | Native turns | Model responses | Median responses | p95 responses |
| --- | ---: | ---: | ---: | ---: |
| Codex notice | 503 | 2,813 | 4 | 12 |
| Codex response | 149 | 593 | 4 | 6 |
| Codex request | 89 | 2,524 | 16 | 83 |
| Claude notice | 162 | 1,436 | 4 | 17 |
| Claude response | 5 | 24 | 5 | 6 |
| Claude request | 31 | 309 | 7 | 30 |

One Claude prompt joined both notices and responses over 394 model responses;
another notice-associated turn reached 424 responses. They demonstrate why
labeling an entire wake-associated turn as wasted bookkeeping is unsound.
The main-session sample totals 23,962 Codex model responses and 4,660 Claude
responses across 2,887 native turns. These are sample activity, not a Cairn bill.

### Narrow, durably acknowledged notice workflows

Filter: exactly one joined notice, committed handled acknowledgment, observed
native completion, at most five tools and 120 seconds. This selects bounded
handling, not an independently graded “no useful work” population.

| Metric | Codex (427 workflows) | Claude (118 workflows) |
| --- | ---: | ---: |
| Median / p95 model responses | 4 / 5 | 3 / 6 |
| Median / p95 tools | 3 / 4 | 3 / 5 |
| Median / p95 duration seconds | 20.0 / 62.6 | 26.7 / 61.8 |
| Summed input tokens, including cached | 234,631,962 | 199,756,966 |
| Cached input subset | 232,053,504 | 198,881,794 |
| Input less cached | 2,578,458 | 875,172 |
| Output tokens, including reasoning | 274,435 | 250,776 |
| Reported reasoning subset | 47,550 | 38,888 |

For Codex, 1,547 model responses serviced these 427 notices; for Claude, 448
serviced 118. The large cached totals reflect repeated processing of long
conversations. They are not 434 million newly inserted Cairn tokens. Cache-read,
cache-write, reasoning and speed rules differ by model/provider. Neither these
totals nor the proxy tokenizer predicts percentage subscription/quota savings.

Retrospective 60-second grouping of wake start times finds 528 singleton groups,
61 pairs and five triples among 665 notice wake turns: 71 fewer starts in that
pure grouping counterfactual. Wake start time is not arrival time; this is a
candidate workload for batching tests, not a forecast that 71 wakes are safely
removable. Start with immediate batching of already-waiting items before adding
delay. A 60-second opt-in deferral experiment can use groups of up to three as
the observed initial fixture size, with a separately tested overflow path.

### Memory, discovery and long context

A source-text extraction from 139 retained Codex developer recall blocks found
641,473 UTF-8 bytes total and 71,377 bytes in delivered source bodies (11.1%).
The other 88.9% includes necessary instructions, provenance, previews, handles,
search diagnostics and framing; it is not all discardable. There were 72 partial
body deliveries and 20 whole bodies; 23 repeat record/version exposures, and 15
query projections marked truncated. These are exposure/size measures, not
relevance grades. A repeated source may be necessary after compaction or in a
different task, and a truncated query may still retrieve useful context.

The audit's own unsolicited recall initially surfaced a partial motorcycle
navigation backup note for this Cairn investigation. That is one observed
cross-project relevance failure, not a measured population precision rate.
Shared collection scope is not working-project identity. Large accumulated notes
and partial excerpts create inspection/re-fetch work when the caveat or needed
passage is elsewhere. First-fitting whole allocation helps fit, not relevance.
Prior CAIRN-131 already addressed some presentation waste; do not recreate it.

Claude logs retain 4,308,431 bytes of Cairn-containing text in direct request
`tail` references in this sample. Reconstructed retained-message occurrences are
much larger, but incomplete blobs and mixed-content messages prevent exact
allocation. Loaded Cairn tool schemas in observed request shapes ranged from
none to four tools, up to 3,173 proxy tokens. Deferred tools are not charged as
if all schemas were always loaded. Measure per harness and after discovery.

Codex logged 201 compactions and Claude seven. No counterfactual establishes
how many Cairn caused. Compaction removes some retained detail and can trigger
required-context refresh and source recovery. Budgeting an individual search
or hook output does not bound all subsequent token reuse in the conversation.

### Auxiliary models and missing accounting

Enabled Codex/Claude bound recall uses `agent_tools`: no preview/body selector
in that route. Capture still invokes `select_json` using installed Claude models
(Codex capture configuration: Sonnet 5; Claude homes: Fable 5.1). This is a
configured route, not evidence of every model that actually ran.

Seven-day **last-state snapshots**, not a historical ledger, showed 48 capture
selector calls across 48 session states: 46 saved something and two selected
nothing. Source inspection shows capture calls `select_json` without an
observation object; `last_capture` retains outcome, record IDs and call count,
not provider token usage. The selector runs with no session persistence. Its
cost therefore cannot be recovered from the sampled main-session logs. Changing
capture cadence without an outcome gate could lose valuable handoffs/corrections.

The hosted profile's `use-report` request returned `AUTHORITY_DENIED`. No observer
or operator credential was substituted. Its `event-stats` covers that principal,
not every UUID session: those counts must not be treated as global traffic.
Future observability work must expose bounded authorized joins, not grant broad
operator access to a hosted model.

## Behavioral guarantees to preserve

| Guarantee | Existing owner/enforcement | Required preservation check |
| --- | --- | --- |
| Durable publication/fanout | PostgreSQL transaction and collection insertion lock; subscriptions snapshot at publication | Crash before/after commit, concurrent subscribe/publish, lost response with identical retry ID |
| Stable intent and exact source | Caller/operation/request idempotency; immutable event record/version reference | Changed intent refuses; exact historical source checked against current access/forgetting |
| Handling versus success | `CompleteEvent` can atomically retain a result and handling; acknowledgment is testimony | Reading/prose/transport receipt never auto-marks work successful |
| Ownership and leases | Store-owned principal, session/execution fencing, delivery lease; native/manual/wake exclusion | Cross-session handles refused, expiry/restore/resume tested; one attempt cannot settle another |
| Ordering and fairness | Claim eligible oldest position with row locks/skip-locked; not global serial completion | Preserve documented eligibility/order; batch doesn't promise total completion order; no starvation |
| Crash recovery | Helper journals immutable command intent before send; replays same IDs; native failure holds | Crash at each step, uncertain publication, process death; no external-effect exactly-once claim |
| Reply lineage | Reply cites that attempt's committed result; publisher UUID and causation preserved | Complete succeeds/reply fails, retry, group deadline, privacy change, source forgetting |
| Memory eligibility | Current scope/destination/authority/evidence checks; whole mandatory/conflict groups; hashes/versioned handles | No hidden source exposure, stale cache acceptance, dropped caveat or silent truncation |
| Context and diagnostics | Bounded hook/tool output; retained receipts and independent observations | Compact presentation remains explainable; old clients/pins don't misinterpret omitted fields |
| Local-first boundary | PostgreSQL is authoritative; relay is replaceable transport; agents/orchestrators choose work | Helper hides mechanics without choosing task priority, semantic equivalence or permissions |

## Workflow traces and where work can be removed

1. **Ordinary notice:** sender chooses content/recipient → saves selected source →
   publishes → DB creates delivery → watcher/native boundary admits → model sees
   wake/control → reads context file → reads exact source → chooses handled/failed
   → journaled ack → reads result → native turn ends. A failed source read must
   stay visible. Inline eligible source plus one explicit settlement can remove
   context-file navigation and its associated decisions. Keep read-on-demand for
   large sources and diagnostics.
2. **Substantive request/reply:** sender resolves current recipient if needed →
   saves task-scoped assignment → publishes → admitted bridge already inlines
   source and checked memory when it fits → recipient performs authorized work →
   completion saves result/handling → reply publication references result → sender
   reads response and acknowledges. Charge both ends, ordinary task tools and
   any capture. A helper can sequence publication or finish/reply; it cannot
   choose recipient intent, declare task success or recover external side effects.
3. **Memory-heavy task:** bootstrap/skill/schema discovery → automatic recall →
   task-model search/pull → inspect partial/whole source → current-source checks →
   selected write/edit → optional capture. Poor fit/relevance causes new searches
   and whole re-reads. The remedy must preserve useful source context while
   reducing wrappers and irrelevant material, rather than merely shrinking caps.
4. **Long session / recovery:** cumulative history grows → compaction/resume →
   required-only refresh and identity → renewed source access if needed. Delivery
   failures may create diagnostics, notices and subsequent recovery turns. A
   durable minimal checkpoint must distinguish already handled messages from
   outstanding work and avoid either replay or loss of the task.

## Remediation sequence and hypotheses

The companion Plane backlog carries execution-sized slices and dependencies.
Every change is additive/opt-in first, with old full output supported until
consumer fixtures pass. A token win cannot compensate for a correctness failure.

### A. Measure complete workflows and auxiliary capture (first)

Add privacy-preserving IDs and counters joining initiating event, sender calls,
native admission, provider responses, tools, settlement, reply, capture and task
assessment. Reuse existing recall observations and journals. Provider usage
missingness is explicit. Record elapsed phases and bounded refusal codes, not raw
prompts. Separate generated, emitted, host-retained and provider-request evidence.
Include relay failures and Agy/Hermes/OpenCode/remote hosts where telemetry exists;
unsupported joins get coverage counters. Capture needs its own selected metrics.

Alternatives: byte-only dashboards are cheaper but cannot establish token/turn
cost; broad raw request logging increases privacy and retention burden. Prefer
metadata joins and local-only opt-in diagnostic payload inspection. No numerical
savings claim: this supplies the denominator and prevents false optimizations.
Effort medium; no production semantic change; rollback disables observations.

### B. Compact receiver surface, starting with notices/responses

Proposed model envelope: a session-scoped opaque handle, kind, sender, exact source
identity/version, source text when it fits, and a concise explicit settlement
interface. Route UUIDs, token paths, executable paths, fixed retry IDs and journal
state remain inside the helper. The handle is a lookup key, not a new authority
credential; current authenticated profile, execution, lease, destination and
attempt are checked on every use. Bound lifetime and lookup capacity; wrong-session
or expired handles refuse. Retain full diagnostics through a permitted inspect
operation and bounded useful error output.

Before: context file read → history read → ack command → full JSON confirmation.
After hypothesis: admitted exact source → `settle(handle, handled)` → concise
confirmed state. Oversized source uses one scoped read handle and reports why.
No acknowledgment happens merely because the helper fetched text.

An isolated metadata projection over 100 contexts measured median 1,064.5 proxy
tokens before and 70 after (median paired difference 994). It preserved sender
and exact source reference but did **not** implement handles, source delivery or
recovery. This is a representation opportunity, not measured end-to-end savings.
Target the bounded notice workflow's median 4/3 responses toward two: one response
to inspect supplied content and explicitly settle, one to consume confirmation.
Allow necessary additional reasoning/errors and large-source access. Verify with
paired workflows before accepting that target. Expected latency falls with removed
round trips; risk is hiding necessary state or replaying a stale handle. Effort
medium; pilot one account, disable compact capability to roll back.

### C. Sequence sender publication and request finish/reply

Expose one journaled sender action for selected source + explicit destination +
publication, retaining independent underlying idempotency identities. Preserve
task-scoped one-off assignments; reusable guidance remains separately chosen.
If source save succeeds but publish fails, return that partial state and retry
publication without another note. Fresh resolution is still required when its
contract applies; an API helper cannot infer ambiguous human targeting.

A recipient finish/reply operation should compose existing complete and publish
with a durable two-stage journal, preserving the committed result before retrying
reply. A store-level all-or-nothing transaction is an alternative, but it changes
visibility and response-deadline behavior; prefer composition unless measured
failure evidence requires atomicity. Report `completion_committed/reply_pending`
accurately, with bounded diagnostics. Never present partial success as full closure.

Source-save/publish and complete/reply each can remove an intervening model
decision/tool-result exchange if currently sequential. They save no extra model
response when a caller already scripts the sequence safely in one tool call.
The sample's 119 successful complete+reply joins demonstrate frequency, not the
number of removable responses. Effort medium for each independent vertical slice.
Retain old commands, switch callers by capability, rollback callers without
abandoning journals.

### D. Reduce notice wakeups and batch handling under explicit policy

Prefer batching notices already waiting at an eligible boundary, retaining each
delivery, exact source, outcome and retry identity. Use a bounded batch handle and
per-item explicit dispositions; one failed source or expired lease must not hide
the rest. Preserve per-recipient eligible ordering and admission exclusion.
Batch acknowledgment is not blanket task completion. Large batches need byte and
lease bounds plus an explicit remainder, with fairness to urgent requests.

Then trial opt-in urgency and deferral: requests, unknown policy and explicitly
urgent notices retain immediate eligibility; opted-in routine notices may wait up
to 60 seconds from first eligibility, never a sliding reset on each arrival.
At a busy host, safety deferral remains visible and can exceed that bound; report
it separately rather than claiming a hard delivery SLA. Deadline expiry, bounded
queue age, forced flush and starvation alerts must be tested. A three-item pilot
batch reflects the observed 60-second wake-start clusters; overflow drains in
bounded continuations and must not starve behind newer items.

Only sender/orchestrator-supplied supersession keys and policy may coalesce
replaceable status. Same text, semantic similarity, record kind, or a transport
receipt does not authorize dropping an actionable notice. Retain superseded
history and a truthful disposition; no model classifier is needed by default.
Model summarization is deferred because it shifts tokens, can lose caveats and
requires a task-quality comparison. Expected benefit is fewer wake/handling
responses, not additive to B's savings for the same notice. Effort large; separate
policy from batch admission/settlement. Default remains existing immediate
behavior. Rollback policy for new work while finishing admitted batches.

### E. Reduce memory presentation and improve useful-source selection

Extend CAIRN-131 rather than repeat its already-shipped compaction. Measure the
remaining control/provenance/preview/body split at each actual facade. Keep a
minimal actionable view with current eligibility, extent, exact source, required
context, uncertainty and complete pull arguments; move verbose diagnostics to a
receipt inspector. Unknown omitted fields remain unknown, not false/zero. Full
canonical receipts stay intact. Reuse versioned capability negotiation and exact
serialized-budget checks, including JSON escaping and refusal output.

Separately investigate oversized mixed notes and context-poor excerpts. Compare
bounded complete-source access, task/project disambiguation, useful passage plus
necessary qualification, and selected author-maintained note splits. Do not
automatically split, merge, reclassify or overwrite history. Search labels and
scope are not security or proof of project relevance. Keep lexical exact-string
queries, semantic fallback reporting, explicit paging and fresh handle rules.

Baseline: source bodies occupy 11.1% of these emitted blocks. No target treats
the other 88.9% as removable. Acceptance target is more relevant source content
or fewer reads at the same current delivery ceiling, with no lost mandatory or
competing context. Select actual minimal field sets from reader fixtures and
compare total workflow tokens, not envelope bytes alone. Repeated-source
suppression must be version-, extent-, eligibility- and surviving-context-aware;
a previous partial read does not establish whole-source knowledge. Effort medium
for presentation, experimental for source selection. Rollback rendering capability
or candidate selection independently; retain old receipts and source history.

### F. Gate capture and preserve recovery context

Instrument capture usage before changing admission. Existing unchanged-dialogue,
courtesy and Codex minimum-dialogue gates already remove some calls. Candidate
changes: avoid repeat capture of an already-confirmed dialogue snapshot, avoid
routine transport-only turns when no new selected knowledge exists, and checkpoint
substantive unresolved work at compaction/session end. Use deterministic observed
events where sufficient; a separate classifier is justified only by measured net
benefit and retained correction/handoff recall. Do not bypass privacy exclusions
or convert “nothing selected” into an error/retry loop. Snapshot state is not a
historical counter. Expected savings remain unquantified until metering exists.

For long sessions, negotiate native context-lifecycle support: consolidate stable
identity/control instructions, retain a minimal outstanding-work/checkpoint view,
and refresh current mandatory memory after compaction. Do not assume Cairn can
delete host history or prove what the model remembers. Compare compact versus
full controls on resume, new corrections and partially handled deliveries; count
re-fetches and capture as well as initial bytes. CAIRN-12 is historical context,
not proof the cross-harness cumulative problem is solved. Effort medium/large;
per-harness canary, old injection path remains available.

### G. Bootstrap, failure paths and permanent verification

Measure static Cairn tool descriptions, skill instructions, repeated session
identity and discovery calls separately from unrelated harness instructions.
Use supported deferred-tool discovery or smaller capability-specific surfaces
where it reduces total discovery+use cost. Do not remove a necessary tool and
force a longer CLI workaround. Loaded schemas observed up to 3,173 proxy tokens
for four tools provide a concrete fixture; no global eager-schema assumption.

Preserve CAIRN-160..188 recovery fixes, missing-ack detection and diagnostics.
Classify failed wake before model invocation separately from a model turn spent
recovering. Surface one actionable coded failure and exact recovery path; bound
reminders and retry the same intent, never restart an agent to bypass a hold.
Retest busy/joined owner input, transient provider failure, refusal before/after
grant, stalled drain, expired lease and relay uncertainty. Model-free transport
retries require established idempotence and cannot reinterpret application errors.

Implemented offline slice (CAIRN-226): the native turn-end reminder records a
revision hash over the exact rendered recovery instruction, attempt identity and
journal state. Repeated identical reminders for the same attempt are suppressed;
changed actionable instructions can be surfaced. Legacy attempt-only markers
remain conservatively suppressed for ordinary reminders because their old
instruction cannot be reconstructed. An explicit compact-source failure
settlement instruction supersedes that marker once, then uses the recorded
revision to suppress repeats. `scripts/test_inbox_recovery.py` covers identical
and changed instructions, legacy state, and the newly actionable failure path.
This bounds the reminder mechanism only; it does not measure model responses
or token savings. Exact model-work attribution,
the cross-failure acceptance matrix and effective deployment verification still
depend on CAIRN-214 and remain open.

Add local regression workloads and deploy inventory to every resulting slice.
Store changes require disposable PostgreSQL integration tests and `make check`.
No GitHub Actions. Verify actual ordinary-hook and separately pinned inbox routes,
not just repository HEAD. Complete an implementation with a measured accepted
behavioral slice, not a smaller printed example alone.

## Prioritization, overlap and validation contract

First: main/capture observability, compact notice handling and memory presentation.
The first is required for attribution; the latter two have measured high frequency
and visible representation/interaction cost. Next: sender and finish/reply helpers,
already-waiting notice batches, controlled deferral, capture gates. Later,
evidence-gated: context lifecycle/schema tuning, oversized-source retrieval and
semantic coalescing proposals. Reliability/authorization regressions block all
rollouts regardless of token savings.

Do not sum B, C, D and context-retention savings independently. Maintain a ledger
with disjoint workflow IDs and stage IDs; compare baseline → envelope only →
batch only → combined, then report interaction effects. Reduced schema or context
bytes and fewer model responses may remove the same repeated input. Include
any newly required read, classifier, summarizer, capture or recovery model call.

The paired acceptance panel must contain: single notice; actionable response;
small request with durable result/reply; long source; burst of three routine
notices plus urgent request; busy owner task; concurrent receivers; duplicate
send; completion committed/reply lost; source forgotten between admission/read;
lease expiry; process crash; relay uncertain response; stale execution; required
conflict group; semantic backend unavailable; wrong-project memory; corrected
note; compaction before/after settlement; unchanged capture; meaningful owner
correction and unfinished handoff. Freeze inputs, versions, model/effort, route,
task acceptance and elapsed/token limits before any provider admission.

Hard gates: same expected useful task outcomes, zero lost actionable items,
unauthorized disclosures, duplicate external effects or false completion; same
required-source/correction recovery; explicit handling of every failure case.
Budgets apply to fixtures, not a license to truncate needed work. Numerical
initial targets are the observed 4/3-response notice medians toward two, and a
lower paired metadata cost than the measured ~1,065-proxy-token envelope while
preserving all reader-required fields. Larger source-use targets must be set
from the metered paired baseline rather than invented as a blanket percentage.

Pilot one account per native harness and a disposable local/relay fixture; check
matched activity distributions, p50/p95 latency, unknown usage coverage and
operator intervention. Preserve reversible capabilities and settle old grants
under their original rules. Stop the affected provider operation on subscription
auth/quota failure; never fall back to OpenRouter. Do not silently resume the
earlier 72-run memory experiment paused for cost. A small new pre-registered
panel is separate work with explicit resource limits and a stop/defer result.

## Remaining measurement gaps and exit criteria

| Gap | Concrete exit criterion |
| --- | --- |
| Causal token savings and usefulness | Paired accepted tasks with same model/effort and exact sender/recipient/capture usage; retain failed and null-usage runs |
| Capture token cost/frequency | Durable per-invocation usage/unknown fields and outcome; join to triggering snapshot without raw dialogue |
| Complete Claude request allocation | Reconstruct and verify hashes/request boundaries, count missing blobs; no full-allocation claim while coverage is partial |
| Remote/other harnesses | One end-to-end source→handling→reply trace for each deployed path, or explicit unsupported coverage with owner-visible limits |
| Semantic redundancy and stale information | Selected messages independently assessed against contemporaneous task state; distinguish harmless status, changed decision and stale source |
| Compaction/re-fetch attribution | Controlled matched context-lifecycle runs with preserved mandatory refresh and task recovery; measure extra work after compaction |
| Failure cost | Link exact admission/refusal/settlement and model-response IDs; classify pre-model failures separately and verify no replay |
| Subscription usage | Provider dashboard/accounting evidence under applicable plan rules; token totals alone cannot establish quota savings |

## Review and engineering doctrine

The validated Pincite release is `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`,
corpus `corpus-2026-07-12-a11702cc9217`, doctrine
`doctrine-f6bbb5196a3f8bf9`. Packet `pkt-e685b461697fbefb`, content SHA-256
`e685b461697fbefb2e299e9041e01fe91c8fc08d66f359efca13d609d3c824d6`, retriever
`retriever-ec995ecdd083b2c8` applied evidence-before-
intervention, explicit invariants, semantic preservation, information hiding
and the no-change option. Repository contracts and owner authorization govern.
Evidence supports prioritizing bounded experiments and implementation tickets;
production acceptance, cross-harness preservation, causal improvement and task
usefulness remain material obligations for those tickets. No claim of an already
accepted production optimization is made. Generic obligations about changed-code
failure reproduction and concurrency implementation are nonmaterial to this
read-only audit but become material when their corresponding code changes occur.

The report and tickets were checked against source, deployed pins, actual retained
usage and existing issues using the docs guard. Tests cover cumulative-accounting
double count, streaming duplicates, missing reasoning, partial payloads, failed
acknowledgments, incomplete log lines and image exclusion. These tests validate the audit's narrow
accounting behavior; they do not validate proposed remediations.

## Plane execution backlog

[CAIRN-213 initiative](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/9d3a3c73-30b8-482e-bbf5-54ee74e69d40/). All children are unassigned Backlog work items; project has no existing labels. Native parent relationships and written dependencies were read back and verified. The installed CLI and available tools do not expose relation creation, so dependencies below are explicit links, not native blocking edges.

| Item | Scope | Blocked by |
| --- | --- | --- |
| [CAIRN-214](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/d40c7276-8a1b-4d32-b18c-771eec922aab/) | Join native model usage to complete Cairn workflows without retaining payloads | None |
| [CAIRN-215](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/a309a059-1ca2-4203-b366-25eca6db06fa/) | Retain auxiliary capture usage and outcomes under the triggering session | None |
| [CAIRN-216](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/97cf7b05-9313-4974-bba9-80b4b6933729/) | Deliver compact scoped notice and response envelopes with explicit settlement | CAIRN-214 |
| [CAIRN-217](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/b5dd7513-b53a-496c-bf1b-f20fee1026fe/) | Compose request completion and reply in one journaled agent operation | CAIRN-214 |
| [CAIRN-218](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/ab45e94e-deea-4bf2-bddd-cdf6130c4a09/) | Publish selected task-scoped sources through one retry-safe sender action | CAIRN-214 |
| [CAIRN-219](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/40436051-5386-4852-ad51-8b73b1cba7b5/) | Reduce residual recall framing while preserving whole-source access and diagnostics | CAIRN-214 |
| [CAIRN-220](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/901058ad-2448-47ef-83b5-a04d76d84919/) | Add explicit routine-notice urgency and bounded wake deferral policy | CAIRN-214 |
| [CAIRN-221](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/6e8fff37-e2a3-4cf0-9565-424a5621a339/) | Handle bounded already-waiting notices in one native turn with per-item outcomes | CAIRN-216 |
| [CAIRN-222](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/ee01fb33-3589-4a67-9f21-bd65818dd0b2/) | Avoid unnecessary capture calls without losing corrections or unfinished-work recovery | CAIRN-215, CAIRN-214 |
| [CAIRN-223](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/71eb904e-c721-4c08-a987-c2dc3e25905e/) | Test oversized-note and wrong-project recall remedies against complete-source task outcomes | CAIRN-214, CAIRN-219 |
| [CAIRN-224](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/fdb21808-ebf7-4117-8bd5-2e5da57db726/) | Preserve minimal coordination and memory state across native compaction and resume | CAIRN-214, CAIRN-216, CAIRN-219 |
| [CAIRN-225](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/195e63e0-1650-40da-bf1e-37f4d5229bb9/) | Measure and reduce Cairn bootstrap and tool discovery overhead per harness | CAIRN-214 |
| [CAIRN-226](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/e177f5e9-53d3-474d-a988-317bc72be697/) | Measure and bound model work caused by delivery failures and recovery reminders | CAIRN-214 |
| [CAIRN-227](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/6dccdad9-9c0c-4ebd-b613-f73090e4bdd2/) | Gate token-efficiency rollout on matched workflow quality and effective deployment evidence | CAIRN-214, CAIRN-215, CAIRN-216, CAIRN-217, CAIRN-218, CAIRN-219, CAIRN-220, CAIRN-221, CAIRN-222, CAIRN-223, CAIRN-224, CAIRN-225, CAIRN-226 |
