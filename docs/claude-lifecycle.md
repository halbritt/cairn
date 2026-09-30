# Ambient memory in Claude Code

If a whole optional pull succeeds but its rendered context is too large, the hook
derives the bounded excerpt from that validated body and labels it
`span_origin: whole_pull`. The receipt retains the cost of the whole pull; no
second pull or fresh receipt is needed. Instructions, mandatory selections and
competing positions still require complete delivery.

The session state records `last_recall.duration_ms` for recall calls, including
empty results and handled failures after recall starts. This measures search and
optional expansion with a monotonic clock. It excludes process startup,
session-lock acquisition and status persistence, and is not a measurement of the
model's response time. Input validation and lock failures before recall starts
do not create a recall observation.

A handled failure replaces an earlier success with `outcome: failed`, zero
delivered bytes/records and a fixed `error_type` category. Available stage
diagnostics are retained; prompt text, note bodies and exception messages are
not copied into the failure observation. Prior delivery and hint state for the
current project/workstream binding is preserved because no recall context was
returned. A changed binding still clears state from the previous binding. The original failure still
reaches the hook caller. If saving the failure status also fails, the hook reports
both failures; the saved status can then remain stale. This is the latest
observation only, not durable per-invocation history or proof of host ingestion.

The engine config supports optional `preview_model` and `recall_model` fields.
Preview admission selects `preview_model`, then `recall_model`, then the existing
`model`; recall body applicability selects `recall_model`, then `model`.
Capture keeps `model`. With no overrides, selection is unchanged. All four
lifecycle installers accept `--preview-model` and `--recall-model`; omitting
either on reinstall retains its installed value. Invalid overrides fail clearly.
These settings only choose a selector, not a recommended production model.

## Optional recall through the task agent

`recall_mode` selects `ambient` (the default when absent) or the opt-in
`agent_tools` path. All four lifecycle installers accept
`--recall-mode agent_tools`; omitting the option preserves an installed value,
and `--recall-mode ambient` explicitly restores the normal recall path. Unknown
values fail before retrieval. This option does not change capture selection.

In `agent_tools` mode, the hook retains the ordinary required-context search and
whole-context budget check. When optional recall is eligible, it supplies up to
three unverified candidate previews from that same lexical result, in server
order, with their complete handles and source-span/conflict metadata. Candidate
presentation omits the redundant CLI `pull_command`; native tools use the
unchanged `pull_arguments`. All other entry fields are retained. Candidate
entries occupy at most 2,000 additional UTF-8 bytes and half the room left after
required context, the cue and search metadata. Entries that do not fit are omitted
whole, together with later entries. Search status, server omission reasons and
`candidate_search.returned_entries` (the count before hook packing) remain visible,
so an empty list is not mistaken for proof that no guidance exists.

The task agent inspects those candidates first, uses its native `cairn_pull` for
promising notes, and checks applicability against the task and current source
before using them. Hook and native tools must use profiles authorized to expand
the same receipt; exposing a handle grants no new access. The hook makes no
optional pulls, additional searches or hosted selector calls. It gives no record
a new seen-state credit. `last_recall.candidate_previews` counts offered entries;
`last_recall.outcome` remains `delegated`. Neither establishes that a note was
read, applicable or used. Taskless startup and wake deferral are unchanged.

The cue allows at most two further searches per task when the supplied candidates
do not fit, coverage is incomplete or a handle expires, and four pull/span calls
total. Use a semantic rephrase for a vocabulary miss when room permits; repeating
discovery merely to obtain an already supplied handle adds delay. The aggregate budget is the
configured `context_bytes`, counting the supplied lifecycle context plus all
native search/pull result text and envelopes. The hook reports
`remaining_memory_bytes` after conservatively subtracting the complete emitted
context, including this field. Use it instead of estimating the hook's size;
subtract later memory responses from it. It deducts this emitted block only;
account separately for earlier retained hook blocks and memory calls. Outer
harness framing is not measured.
With a search tool that exposes
`memory_budget_bytes`, set that field to the remaining memory allowance. The
existing `available_tokens` field supplies the separate available input-context
room used by optional-memory policy. It is not the model's maximum context
window. If free input room is unknown and both facade and API support the memory
cap, omit `available_tokens` and supply the remaining allowance as
`memory_budget_bytes`. This uses the configured policy default, not measured free
context. Honor known smaller free room. With older tools or unknown cap support,
use only `available_tokens`;
keep that within the remaining memory allowance. Do not send an unsupported
field or increase the declared context room just to obtain more candidates.

The explicit memory budget bounds one search receipt and its expansions. It
does not combine multiple receipts or count lifecycle context already supplied;
the task agent must subtract all such deliveries before its next search.
Required instructions stay whole: insufficient room means stop optional
inspection and report the limitation, rather than truncate them. The aggregate
task and call limits are instructions to the agent, **not enforcement by the host**. Native receipts each
have their own budgets; a new receipt does not reset this requested task allowance.
A pilot must measure aggregate exposure and actual compliance across tool calls
and turns. It must also measure task-model/tool work: `last_recall.duration_ms`
covers only the hook and does not establish total task delay or usefulness.

The complete mandatory package is reserved before the cue. If the cue cannot
fit, it is omitted with outcome `delegation_omitted` and rejection
`delegation_context_budget`; mandatory context is still delivered whole. An
oversized mandatory package refuses as before. Taskless fresh starts and wake
notifications keep their existing optional deferral. Eligible resume, compact
and explicit-workstream starts receive the cue without optional selector calls.

Before any pilot, verify that the actual harness exposes and permits the native
tools with the intended authenticated scope and destination. The source cue
cannot certify tool availability or agent compliance. Unavailable tools or room
must be reported honestly; there is no automatic model-selector fallback. This
mode implements delegated inspection, not measured successful retrieval or task
improvement. Installation remains subject to the owner's production authorization.

Cairn's optional Claude Code hooks retrieve memory before a task and preserve
selected context before compaction or exit. Install them once at user scope;
normal sessions in other projects use the same shared collection.

| Event | Behavior |
| --- | --- |
| `SessionStart` | Retrieve required instructions. Defer optional recall on fresh startup or clear without a prompt or explicit workstream; on resume or after compaction, prefer this session's checkpoint. |
| `UserPromptSubmit` | Search using project, task keywords, quoted phrases and file/error hints; omit weak matches and unchanged bodies already delivered. |
| `PostToolUse` / `PostToolUseFailure` | Retain recent Read/Edit/Write filenames and diagnostic identifiers for the next search; omit tool output. |
| `PreCompact` | Select and save a checkpoint before either manual or automatic compaction. |
| `SessionEnd` | Select remaining useful context before normal exit, clear, or switching sessions. |

Retrieval delivers required selected context, optional previews with native pull
arguments, and one current optional body when it fits. The hook inspects up to six
unseen candidates, including candidates whose short preview misses relevant body
text. It tries later candidates when an earlier body is stale, irrelevant or too
large. A file association, quoted phrase or at least two nearby task terms must
support lexical body delivery; startup with task context or a resumed session
also permits project-labelled decisions and preferences. Fresh, prompt-free
startup without an explicit workstream performs the ordinary required-context
search but makes no optional pulls or model calls. Required context must still
fit whole within the configured budget. The next prompt uses normal recall.
A taskless `UserPromptSubmit` containing only the complete current Cairn wake
notice also defers optional recall. This recognizes the plain notice and the exact
`cairn-events` Claude channel envelope with matching UUID fields; it classifies
content and does not authenticate a sender or authorize an assignment. Extra
owner text, quoted notices, malformed or unknown renderings retain normal recall.
Explicit workstreams and resume/compact events retain their existing behavior.
The required-context search uses only the project name, without notice UUIDs or
recent file hints, and still delivers required instructions whole or refuses the
whole package when it exceeds budget. No optional semantic search, pull or
selector call runs for a deferred notice, and existing seen-state is retained.
The original host event is unchanged.

The notice does not contain the queued task body. After reading that source,
the agent still needs explicit task-conditioned retrieval through its normal
memory tools; a later actual prompt also uses normal hook recall. Notification
deferral does not itself supply useful task guidance or complete that goal.

Hints expire after 15 minutes. A delivered body is
suppressed at the same version until startup, resume or compaction resets the
context; required selected context is always retained. Empty or weak results add
no boilerplate. The bounded shortlist can still miss relevant notes, so explicit
search remains available. The agent can
pull other sources through its existing Cairn MCP tools. The hook and tools must
use the same authenticated principal for those handles. Session labels identify
host conversations; shared captures remain repository-scoped.

Implicit filename hints require a path or an existing workspace file. URLs and
email addresses are not file hints; ambiguous bare dotted names remain ordinary
search terms. Explicitly quoted names remain exact text anchors even when the
file does not exist. A missing path whose first component contains a dot must
use `./` or another explicit relative/absolute prefix to become a file hint.
These rules prevent a domain such as `claude.ai` in a quota notification from
being treated as an observed repository filename. They do not establish the
current task behind a generic continuation message.

The query keeps project, file, quoted-phrase, workstream and recent-error anchors
first, then eligible distinct task words in their original order, within 4,000
UTF-8 bytes. Whole words that do not fit are omitted. The former alphabetical
48-word cutoff could discard task identifiers even when the complete query fit.
Stopword filtering and the 128-byte word limit remain in effect. Long prompts can
still lose late unquoted words; including more vocabulary does not establish
relevance or interpret a correction or negation. The same query construction is
used for related-note searches during capture; capture admission and write checks
are separate.

The hooks use the existing authenticated CLI and hosted profile. Installing them
explicitly enables lifecycle reads and selected writes independently of whether
the model chooses a memory tool. Ordinary MCP calls retain their own permissions.
No database or MCP server upgrade is needed.

## Install

From the Cairn checkout, with `cairn`, `claude` and Python 3.11+ installed:

```sh
python3 scripts/install-claude-hooks.py
```

The installer copies the hook into `~/.local/share/cairn/claude-hooks` and merges
its six command hooks into Claude's user `settings.json`. It preserves existing
hooks, permissions and unrelated settings, and keeps the first settings backup
as `settings.json.before-cairn-lifecycle`. Start a fresh Claude session afterward.
Rerunning the same command updates this installation without duplicating hooks.

`--settings` selects another Claude profile. `--destination` selects a separate
hook installation when profiles need different models or connection settings.
The default settings location honors `CLAUDE_CONFIG_DIR`. The extraction model is
the selected profile's configured model at installation; rerun the installer
after changing that setting. If no model is configured, Claude selects its default.
`--socket`, `--token-file` and `--repo` select an existing ordinary hosted profile
and collection. The installer never provisions credentials or changes policy.

To disable hooks for one launch, set `CAIRN_LIFECYCLE_DISABLED=1`. A
`.cairn-no-memory` file in the working directory or nearest Git root disables both retrieval and
capture there. To uninstall, remove this installation's commands from the six
hook lists; leave other hooks intact. Do not restore the entire backup over newer
settings changes.

## Selected capture

Capture makes a separate, tool-free Claude request using the existing provider
credentials and configured model. It reads only a bounded excerpt of top-level
user/assistant text from the host transcript, omitting tool payloads, reasoning
blocks and sidechains. It supplies the previous checkpoint and up to three relevant complete durable notes so the selector can retain useful context and revise superseded guidance. It saves only the selected
note, not the excerpt, transcript, or raw model response.

The selector follows the Cairn skill's criteria: meaningful owner corrections,
decisions and reasons, verified reusable fixes, and unfinished-work checkpoints.
It must skip lookup-only exchanges, routine progress, excluded content, secrets,
and private Council content. A null checkpoint and empty memory list write nothing. Durable decisions, preferences, lessons and procedures are saved separately from unfinished-work checkpoints. Supplied existing notes are revised by record ID and current version; identical bodies are skipped. New notes use stable project/topic titles. A matching topic outside the supplied candidate set requires explicit reconciliation rather than a blind overwrite. Bounded lexical discovery can still miss semantically equivalent notes with different wording. Selection remains
fallible model judgment; this is not a secret-redaction guarantee or an
independent verification of the reported work.

Unfinished work uses an ordinary shareable note named `Handoff: PROJECT / TOPIC`. The selector receives up to two relevant complete workstream notes and must reuse a matching topic across sessions. A retrieved or saved workstream title is retained in session metadata for resume. Fresh agents discover it through ordinary task search. Project identity alone does not join unrelated tasks. Existing session-UUID checkpoints remain readable as migration context. Revisions use the current version and
preserve store-owned metadata. Concurrent capture hooks for the same session
cannot run together. Explicit handoffs through the `handoff` skill use the same searchable naming convention. Matching remains fallible: bounded lexical discovery can miss a workstream, and concurrent first creation is not a uniqueness guarantee.

## Bounds and failures

- Lexical discovery requests 32,000 bytes of Cairn search room. Semantic
  discovery requests 64,000 per page and follows at most four ranked pages,
  inspecting at most 32 semantic previews in total. Search room is inspection
  capacity; the server's packing rules still determine how many previews fit.
  Query, scope and entity hints remain the same across pages. Pages are current
  reads, not a snapshot cursor; changed notes can shift positions. Delivered context text,
  including guidance and the optional body, is capped by the installation's
  `context_bytes`: 9,500 UTF-8 bytes for Claude Code, whose hooks keep only about
  10,000 characters of `additionalContext`, and 12,000 bytes by default elsewhere.
  An oversized optional body can use a current, checked `match_span` for class
  A/B notes when that scored passage fits. A lexical hit without `match_span`
  can instead use `summary_span` as the start of an excerpt of at most 1,536
  bytes. Both routes verify the current version, source hash and returned bytes.
  Nontext byte spans (including a cut through a UTF-8 character) are refused;
  the preview still permits a manual pull. An excerpt is labelled `partial_span`
  and does not mark the whole note as seen. Class C and competing positions still
  require a whole-body pull. Other oversized bodies are skipped in favor of a
  later candidate when one fits. Bounded preview entries retain their pull
  handles when they fit.
  With the installed `semantic_fallback` default, the tested sorted hook query
  feeds lexical and semantic discovery. The hook inspects up to ten lexical
  previews and the semantic pages within the total bound above. When previews exceed pull credits, a bounded
  tool-free call chooses which current bodies to read; each receipt retains its
  own four-credit limit. The selector may rank up to eight choices, including
  alternatives beyond a channel's credits. The host processes those choices in
  ranked order and checks actual remaining credits just before each candidate
  read. A duplicate skipped after a successful read, or a byte-budget refusal,
  does not reserve credit that would hide a later ranked choice. A failed read
  can still be retried through another selected receipt for the same note.
  Only content admitted to the body selector suppresses later duplicate views.
  A complete body covers that record version; an excerpt covers only its checked
  byte range. Distinct passages and later versions remain eligible. An identical
  excerpt from another receipt may require another successful read before it
  can be recognized and omitted; that read still consumes credit. A view rejected
  by the selector-input budget does not suppress a later smaller view. When a
  complete body fits delivery context but exceeds remaining selector input, the
  hook tries its checked hinted excerpt from the already-paid response. It
  rechecks both budgets, spends no additional credit and keeps complete-body
  delivery when both budgets permit it. If the excerpt is unavailable, unsafe,
  or still too large, the candidate records one `rejected.selector_input_budget`;
  that counter does not distinguish these fallback failures.
  Alias channels share one receipt allowance. `preview_receipt_budget_dropped`
  counts choices skipped because their receipt has no credits at read time;
  `preview_admitted` excludes those choices but can include skipped duplicates
  and failed attempts. These omissions also record `rejected.receipt_exhausted`.
  Malformed, repeated or out-of-range indices and
  verdicts exceeding eight choices are still refused as a whole. A separate
  body-based model decision checks applicability before injection. Shared words, a file association,
  or a project label alone cannot inject an optional body. An unavailable model
  omits optional guidance, while required context remains. The semantic route
  also runs after an unhelpful exact-file result. Setting `semantic_fallback`
  explicitly to `false` keeps the earlier lexical-only route. The hook records
  preview and body shortlist sizes, source extents, pull and selector durations,
  aggregate selector-input bytes, provider-reported costs, and omission reasons
  in recall status. `receipt_attempts` counts candidate attempts; `receipt_pull_calls`
  counts full-body and span CLI pull attempts, including failures and refusals.
  Successful pulls consume receipt credits even when later context or source
  validation rejects their contents. A byte-cost refusal alone does not mark
  the receipt exhausted, so a later smaller candidate can still be inspected.
  Each attempted preview/body selector also records `preview_process` or
  `model_process`: requested model, process-spawn duration, first stdout/stderr
  byte times when observed, total process time including cleanup, exit status,
  and output byte counts. These contain no command arguments, prompts or output
  text. Spawn time measures `Popen`, not CLI initialization; first output is not
  provider time-to-first-token. Successful JSON replies may add explicitly
  labelled CLI-reported duration, API duration, cost and supported usage counts.
  Missing or invalid values remain absent, including cost after a timeout.
  If the enclosing deadline expires before launch, process measurements remain
  absent; an empty observation does not mean a zero-duration call.
  A completed process is not a valid applicability verdict. Preview and body
  selectors must return the CLI's `structured_output` field. Their prompts permit
  only the schema-generated `StructuredOutput` response tool; ordinary tools
  and MCP remain disabled. This response tool was verified in Claude Code
  2.1.285; its name is a CLI compatibility dependency. JSON written as response
  text is not accepted as a substitute for that field. See the
  [CLI output contract](https://code.claude.com/docs/en/headless).
  An attempted preview selector failure reports `discovery: verification_unavailable`. Invalid verdicts
  retain the aggregate rejection in `rejected` and a fixed `validation_error`
  code in that selector's process observation: `reported_error`,
  `structured_output_missing`, `structured_output_type`, `indices_missing`,
  `indices_type`, `too_many_indices`, `index_missing`, `index_type`,
  `index_out_of_range`, or `duplicate_indices`. The first failed check is recorded;
  invalid field values and response text are not. Preview selection uses `indices`;
  body selection uses `index` and accepts -1 as a valid negative decision.
  Valid verdicts have no `validation_error`; their applicability remains a separate
  decision. These codes describe failed checks, not why a provider produced them.
  Session state keeps the latest recall; the existing evaluation
  observer preserves these fields per invocation. This does not add production
  history to `use-report` or establish the cause of a slow provider call.
  Both selector inputs together remain capped at 24,000 UTF-8
  bytes; candidates that would exceed it are omitted whole. Preview admission
  has a five-second allowance; the body decision's eight-second allowance
  starts after candidate pulls. Neither extends the 11-second recall deadline.
  Preview selection remains fallible and can miss relevant later-ranked notes. Reinstall
  hooks to apply the new default; no running copy is changed.
  Mandatory context is never truncated. Retrieval runs on lifecycle events, not
  on every model or tool request; it does not enforce the whole conversation's
  context budget. Recall has an 11-second local deadline and at most six lexical
  candidate inspections.
- Capture examines at most the final 2 MiB of transcript bytes and supplies at
  most 24,000 bytes of serialized dialogue. A clipped boundary message is labelled.
  Older context can be missing; the previous saved checkpoint helps continuity.
- Selection has 35 seconds; individual Cairn commands have 5 seconds. Capture
  hooks have a 150-second host timeout to cover bounded candidate reads and up to four selected writes. Capture hashes the selected dialogue and selector contract before any memory/model call. An unchanged excerpt after a confirmed save or null selection skips extraction entirely. Host compaction summaries, local-command wrappers and duplicate transcript UUIDs do not count as new work. Failed writes leave the digest unchanged so another event can retry. A changed model/selector contract also invalidates the digest. Nested lifecycle hooks and transcript persistence
  are disabled for that call. Temporary working directories are removed on exit.
- Checkpoints are at most 6,000 body bytes plus their title. Identical selected
  bodies do not create revisions. Request IDs remain stable for an identical
  selected write; a changed model selection is new intent.
- Missing services, timeouts, malformed selections and refused writes report a
  labelled hook failure without preventing the user's task or claiming a save.
  Use native tools or an explicit handoff when capture fails. A stale optional
  body leaves a labelled index requiring a fresh search.

There is no continuous transcript watcher, background groomer, or guarantee of a
checkpoint after an abrupt process kill. Capture can miss tool work that has not
yet been summarized in dialogue. Session locks and bounded metadata files remain in the installed `state` directory. They retain note IDs/versions, recent filenames, diagnostic identifiers, workstream title and capture digest, without transcript or file bodies. If transcript truncation changes the bounded excerpt, an additional selection can occur even without new work.

Claude's [hook reference](https://code.claude.com/docs/en/hooks) defines these
lifecycle events. The installed behavior is checked separately in the
[native verification report](verification/claude-lifecycle-2026-09-13.md).
