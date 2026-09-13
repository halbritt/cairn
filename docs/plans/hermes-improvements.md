# Hermes memory improvements

Owner authorization, 2026-09-13: complete these five improvements in order,
then deploy. Slack is the only deployed messaging gateway. Baseline code is
`db530a1`; deployment documentation is `c28e6a0`.

1. Reduce capture latency. Measure the selector separately from lookup/startup,
   compare a faster model on the same synthetic decisions, corrections,
   exclusions and checkpoints, and remove provably unnecessary selection work.
   Target: no selector invocation for a confirmed unchanged/acknowledgment-only
   continuation, and lower measured selector latency without losing those cases.
2. Give Slack threads explicit project/workstream context, independent of the
   gateway's terminal cwd. Keep context through restart, isolate thread choices,
   allow clearing/changing them, and make bare continuation use that choice.
3. Provide a compact on-demand memory status view: recalled records, last capture
   outcome (saved, no change, failed/pending), current workstream and retry path.
   Keep routine chat quiet and do not retain raw dialogue in status files.
4. Add bounded semantic fallback after lexical relevance misses. Preserve precise
   file/error retrieval, required context, labelled worker unavailability and the
   combined 12000-byte delivered-context ceiling. Similarity alone must not
   establish applicability; verify candidates before injecting them.
5. Keep current checkpoints concise: replace obsolete progress, preserve still-open
   work and relevant decisions, retain older wording in record history, and keep
   reusable guidance separate. Verify same-record updates and completion handling.

Preserve the ordinary hosted profile, store-owned identity, existing task model,
native Hermes memory and unrelated plugins/settings. Reuse the shared lifecycle
engine; no production databases for tests or raw-session capture. Native checks
cover CLI and the real Slack/GatewayRunner path with captured outbound transport.
Deploy only after focused, native, shared lifecycle and repository checks pass;
verify installed hashes, gateway restart and bounded ordinary retrieval/status.
Do not equate these checks with full design acceptance or long-term task benefit.

## Task 1 evidence

The synthetic benchmark covers decision/checkpoint separation, same-ID owner
correction, lookup, exclusion, unfinished work and courtesy, twice per model.
All cases passed. Baseline selector median across all cases was 3.874s; Haiku
was 7.303s and slower on this host. A low-effort comparison did not establish a
consistent benefit, so the deployed selector/model settings will stay unchanged.
The new exact courtesy-pair shortcut took 0.413–0.467ms, versus baseline
3.481–3.530s. It only skips a continuation when its prior dialogue fingerprint
was confirmed, or when the entire supplied dialogue consists of courtesy pairs.
Changed/unconfirmed earlier work, substantive replies and corrections still select.
The measurement is whole `capture()` with lookup/write boundaries replaced for
synthetic model comparison; it performs no database access. Native verification
will separately cover the complete installed path. `claude --version` startup
was 6.7–10.0ms (five observations), which is a lower-bound probe, not a full
selector startup profile. No persistent selector process or concurrency was added.

## Task 2 evidence

`/cairn context <directory> [workstream]` (Slack `!cairn`) sets explicit
conversation context; `context` shows it and `context clear` restores automatic
project selection. A profile-level commands plugin makes this available before
an agent is created. Gateway keys come from the native pre-command event and
are hashed into atomic per-conversation control files. CLI controls belong to
that CLI process. Explicit choices persist across gateway reset/restart without
changing the terminal cwd or the shared collection identity.

The provider supplies chosen project/workstream fields to the shared engine;
those fields reset stale capture/retrieval state on change. An explicitly bound
workstream cannot be silently changed by selection. Once context has been chosen,
capture uses the current turn plus previously selected checkpoint context, so
older conversation text is not reassigned to another project. A pending failed
capture must be retried before changing its context.

47 focused lifecycle tests pass. Native Hermes verifies the cold gateway command
and persistence through restart. Native profile callbacks retrieved the bound
project while their actual terminal cwd was an unrelated directory. Existing
CLI, gateway, compaction, correction, interruption and timeout checks still pass.

## Task 3 evidence

Status records engine-confirmed recall/capture metadata and elapsed duration.
Failed capture remains pending; a later recall cannot overwrite that failure with
an older successful capture. Local status storage errors warn without converting
a confirmed shared-memory operation into a failed write. Opt-out clears pending
capture; provider shutdown preserves failed retry metadata.

49 focused lifecycle tests pass. The native Hermes fixture verifies Slack status
without a model call, CLI status registration, restart recovery from the exact
bounded native-history snapshot, and refusal of changed history. Existing gateway
and CLI lifecycle checks pass. No raw conversation is added to control files.
A pending retry can be explicitly discarded when original history is unavailable.

## Task 4 evidence

Hermes enables one semantic search only after optional lexical relevance misses
on a substantive prompt without precise file/quoted/error hints. One complete
candidate is pulled and checked by the existing tool-free selector, with an
eight-second deadline. Similarity does not authorize injection. Both searches'
required context and the complete candidate share the 12000-byte ceiling; an
oversized mandatory set refuses delivery. Other harnesses retain lexical defaults.

52 focused tests pass. A disposable native Hermes/API fixture with the installed
embedding worker verifies a paraphrased backend question, full-body delivery,
unrelated rejection and the context ceiling. Its relevance response is synthetic.
Separately, the installed real selector passed paraphrase, unrelated and embedded
instruction cases twice each (3.15–7.17 seconds). Status labels semantic discovery,
verification, rejection, timeout/failure and worker unavailability. These bounded
cases establish behavior, not general relevance accuracy or long-term benefit.

## Task 5 evidence

Checkpoint selection now returns a complete current replacement under 3000 UTF-8
bytes. It removes obsolete progress and completed next steps while retaining
open work, constraints and references. An explicit completion can revise only a
supplied existing topic; the same record gains a short final result labelled
`Status: complete`. Null selection remains no change. Durable guidance stays
separate, and ordinary compare-and-swap revision retains previous versions.

53 focused tests pass. The real selector passed partial completion (security scan
still open), full completion and continuing already completed work without a new
write. Final checkpoint bodies were 408 and 472 bytes in those synthetic cases.
The disposable native fixture confirmed the same record ID, incremented version,
removal of stale next steps and exact earlier body through Cairn history.
No existing collection was bulk rewritten. Selection remains fallible.

During final review, precise recall hints were consolidated so expired diagnostic
hints cannot disable semantic fallback indefinitely; current file/quoted/error
hints still keep lexical retrieval. The boundary test covers expired diagnostics.
