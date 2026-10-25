# CAIRN-220 native wake join probe, 2026-10-25

This probe did not establish the watcher-to-Store join. It must not be used as
CAIRN-220 acceptance evidence.

## Attempt record

The first run stopped during outer sandbox command construction, before bwrap
started. The exact error was `NameError: name 'profile' is not defined`.
The 0600 private trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-initial-setup-failure.json`.

One targeted fix bound `profile` to the fixture's private Codex home. The single
retry started its disposable PostgreSQL cluster and local Cairn API, then
returned exit code 2 without creating the inside-runner boundary report. No
queue receipt, `item/started`, `UserPromptSubmit`, Store attempt, or local
Responses request was observed. The 0600 private trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-3564263e54004114bb8ab9b14129c22f.json`.
It preserves the outer exit code and stderr SHA-256, but not stderr text. The
exact bwrap diagnostic is therefore unavailable.

## Next-probe prerequisite

The exit-2 source remains unconfirmed because stderr was not retained. Static
review found a likely bootstrap path mismatch: bwrap mounts the worktree at
`/repo`, but the command invokes Python with the original host worktree path
for `check_codex_app_server_wake_join.py`, which is not mounted inside. A
separate provider-free inert bwrap preflight using `runtime_sandbox()`, the
same `/opt/codex-bin` read-only bind, and `/usr/bin/true` exited 0, so the
missing `/opt` directory is not established as a cause. Before another bounded
run, invoke the mounted path `/repo/scripts/check_codex_app_server_wake_join.py`
and capture bounded, path-redacted outer stderr in the owner-only 0600 trace
before any assertions or disposable cleanup. Verify the outer bootstrap
reaches the runner before relying on application-level diagnostics.

The intended trace boundary contains only fixed failure codes, process and
delivery identifiers, native queue receipt identifiers, turn identifiers,
and SHA-256 digests of exact wake text and provider request bodies. It must not
retain tokens, auth JSON, full prompts, Responses bodies, raw sessions, or
unredacted stderr. Keep the trace under
`~/.cache/cairn-220-native-wake-proof/` with mode 0600; do not copy it into the
repository.

No real provider, provider authentication, OpenRouter, production database,
held-work replay, or acceptance claim was used or established. The follow-up
below records the later bounded retry; CAIRN-220 remains unaccepted.

## Bounded follow-up

An inert bwrap preflight read the checker at the exact mounted path
`/repo/scripts/check_codex_app_server_wake_join.py`. It returned exit 0 with
empty stderr. The 0600 traces are
`~/.cache/cairn-220-native-wake-proof/preflight-path-retry-4ed24111fb6e44c3b753b44bef8a665a.json`
and, after the targeted import-path edit,
`~/.cache/cairn-220-native-wake-proof/preflight-path-retry-662fc1003a1f472581a49337cc6bbfed.json`.

The first full run reached the isolated Codex app-server, then stopped before
the watcher. Its 0600 trace
`~/.cache/cairn-220-native-wake-proof/run-full-join-retry-78ecc6b9f98a4a078057e8a5ac5f9b1e.json`
records `ModuleNotFoundError`, no queue/hook/Store observations, and empty
outer stderr. Static review localized this to the inner runner importing
`codex_queue` without `/repo/integrations/lifecycle` on `sys.path`. One
targeted fix added that path; missing-module diagnostics were constrained to a
fixed allow-list.

The single full retry reached the actual production watcher with the pinned
Codex 0.164.0 binary, fake Codex auth, `--unshare-net`, a local Responses stub,
and the disposable API/PostgreSQL cluster. The watcher exited 0 and retained a
pre-claim `codex-queue` wake marker with `status=uncertain` and no
`queued_submission_id`. The queue observer recorded no receipt or item; no
`UserPromptSubmit`, Store attempt, or local Responses request was observed. A
`SessionEnd` hook row appeared only during app-server cleanup, after the
boundary report had been written. The 0600 trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-retry-bef463c04fbe41e69dc022021e4c34c2.json`.

The retained session state supports only the fixed classification
`queue_outcome_uncertain_no_receipt`: an uncertain wake marker exists, but no
queue receipt or attempt was observed. Watcher stderr was retained only as a
SHA-256 digest, so the native RPC/`QueueError` cause is unknown. No replay,
settlement, or additional full run followed. The unverified harness was
discarded; this note records the verified preflight and failure boundaries,
not CAIRN-220 acceptance.

## Queue-stage diagnostic preparation

At commit `1140772`, the previous full-join harness is absent from the checkout
and no runnable copy was present in the private trace directory. The prior
uncertain delivery was not replayed. A new opt-in Python startup observer is
available at `scripts/fixtures/cairn220_queue_trace/sitecustomize.py`. With
that directory first on `PYTHONPATH`, `integrations/lifecycle` also on
`PYTHONPATH`, and `CAIRN220_QUEUE_TRACE` set to a host-mounted private path, it
records fixed stage transitions around production `codex_queue._open`,
`_admit`, `thread/queue/add`, and `thread/queue/start`. Each append is fsynced
to a mode-0600 trace before assertions. It keeps only allow-listed exception
class names and bounded integer native refusal codes; it omits exception text,
wake content, and tokens. The queue helper return values and decisions are
unchanged.

`python3 scripts/test_codex_queue_stage_trace.py` verifies the observer's
pre-call/refusal trace, 0600 file mode, and omission of synthetic refusal text.
That focused test and `make check` passed. This verifies the trace boundary
only. No new disposable PostgreSQL/API/Codex watcher run or delivery-to-Store
join was performed, so CAIRN-220 remains unaccepted and the queue failure's
native cause remains unknown.

## Reconstructed fixture attempt

A provider-free runner was added at
`scripts/check_codex_app_server_wake_join.py`. It has a separate preflight that
executes the exact mounted runner path under `bwrap --unshare-net`, creates the
observer trace at the exact mounted path with exclusive create, verifies owner
and mode 0600, checks the single fixed startup row, and removes that preflight
trace before the full attempt. Its fixture uses a fresh PostgreSQL cluster and
local API, synthetic agent identities and fake Codex auth, the pinned Codex
0.164 binary, and a loopback-only Responses stub. The observer is first on the
watcher's `PYTHONPATH`; the full trace is outside the disposable fixture root.

The first reconstructed full attempt exited during setup with a generic
`CalledProcessError` and no watcher boundary report. A setup-only repetition of
the PostgreSQL/API bootstrap passed. The one trace-driven full retry produced
the private 0600 bootstrap trace
`~/.cache/cairn-220-native-wake-proof/bootstrap-f69eab74388a4fbfbdafcd63f0a6e1ff.jsonl`.
It records `pg_start` with exit code 1 after `pg_initdb`; the bwrap runner,
Codex, watcher, queue, hook and Store were not reached. No raw PostgreSQL or
watcher stderr was retained. The failed disposable root was removed, and a
process check found no matching test server. No queue retry or replay occurred.

The exact current diagnostic boundary is therefore PostgreSQL startup, before
the watcher. This reconstructed full fixture has not established queue receipt,
native item start, `UserPromptSubmit`, or a Store attempt. The prior uncertain
delivery remains untouched, and CAIRN-220 remains unaccepted.

After the retry, the strict path tests, mounted-path/trace preflight, focused
observer test, and `make check` passed. The PostgreSQL startup failure-path
cleanup also checks for a partial `postmaster.pid` before removing its root;
that partial-start branch was not observed in this run.

## Bounded PostgreSQL startup classification and retry

The next inert PostgreSQL bootstrap preflight reproduced `pg_ctl start` exit 1.
Its owner-only trace contained the fixed category `socket_path_too_long` and
numeric exit code 1. The cause was the socket directory nested below the
UUID-named fixture path: PostgreSQL appends a fixed socket filename, exceeding
the Unix-domain socket path limit. The preflight and full setup now use a unique
short socket directory under the existing owner-only trace directory, and both
call the same `pg_ctl` command builder. The exact-command inert PostgreSQL
preflight then passed. Startup stderr and `postgres.log` are read only in
memory, classified against fixed categories, and discarded before cleanup.
Failed shutdown records a fixed cleanup failure and preserves the disposable
root if `postmaster.pid` remains.

One provider-free full fixture attempt after that preflight passed PostgreSQL
startup, database creation, migrations, and API process launch. It stopped at
the next fixed boundary, `api_ready/server_process_exit`: the API process
exited before its Unix socket appeared. The retry retained no server stderr.
Source review found no proven fixture configuration omission: `serve` reads
`CAIRN_HOME/identities.json`, the fixture writes that owner-only file with
fields accepted by `localapi.ValidateIdentities`, and the process receives
`CAIRN_HOME`, `CAIRN_DATABASE_URL`, and `CAIRN_TEST_DATABASE_URL`. Migrations
also succeeded with the same database URL. Since the process exited before the
socket appeared, the agent CLI envelope path was not reached. The API exit
cause remains unknown; no further API or watcher run was made.

The focused queue-stage trace suite passed (3 tests), `make check` passed, and
`git diff --check` passed. The disposable API and PostgreSQL processes were
cleaned up and the fixture root removed. No bwrap watcher, queue receipt,
native item start, `UserPromptSubmit`, Store attempt, or delivery-to-Store join
was observed. CAIRN-220 remains unaccepted.

## API socket startup preflight and bounded watcher retry

The API-only disposable preflight used the same compiled Cairn binary, migrated
PostgreSQL DSN, `CAIRN_HOME`, owner-only `identities.json`, and environment as
the watcher fixture. It initially recorded fixed status `STORE_ERROR`. Source
inspection showed the default API socket lived at `api/api.sock` under
`fixture-<uuid>-<mkdtemp suffix>`; a same-prefix disposable path measurement
returned 112 bytes, longer than Linux AF_UNIX's 107-byte pathname limit.
Cairn's API startup error wrapper sanitized
the bind message, so the startup preflight itself retained only the fixed
`STORE_ERROR` status; the length measurement and `listenPrivateUnix` source
inspection established the path cause. Passing `--socket` into the existing
short owner-only runtime socket directory fixed the fixture. The API-only
preflight then passed and confirmed its socket and version endpoint before
cleanup.

One provider-free full watcher run confirmed `queue/add`'s receipt, the exact
delivery ID, `UserPromptSubmit`, and a Store attempt for that delivery. The
watcher reached `thread/queue/start`, which refused with native code -32600.
The trace-driven retry records the fixed refusal class
`submission_not_found` and `receipt_matches_start=true`. A second app-server
connection was initialized before delivery and monitored for `item/started`,
keyed by the expected `clientUserMessageId`; it observed no matching
notification. The Store attempt and hook intent share a turn ID, but no native
item-start notification connects that turn to the confirmed queue receipt.
The exact queue-receipt → item-start → hook → Store join is therefore not
proven, and CAIRN-220 remains unaccepted.

The pinned Codex 0.164 app-server JSON schema confirms the queue/add and
queue/start parameter shapes, and defines `item/started` with `threadId`,
`turnId`, and an item whose user-message variant has `clientId`. The schema does
not document notification fanout or whether queue/add consumes a submission
before queue/start; it includes `thread/unsubscribe` but no `thread/subscribe`
request. The second initialized connection checked that the thread was loaded,
but the schema does not establish that this subscribes it to notifications.
The refusal is consistent with queue/add consuming a submission before
queue/start, but does not prove it. No delivery was replayed and no further full
watcher run was made. The separate notification trace retained only fixed
stages, booleans, and digests. The focused trace tests (5) and `make check`
passed.

## Pinned Codex 0.164 dispatch and observer source audit

The local `rust-v0.164.0` source tag resolves to
`6aaac694803004a63c6f393d153c96d22521a6b9`. In
`codex-rs/ext/queue/src/service.rs`, `enqueue()` persists the queued item,
emits the queue-changed event, and calls `wake_if_loaded()`. That idle lifecycle
path reaches `dispatch_if_idle()`, which starts the first queued user input and
deletes its queue row after `Started`. The pinned app-server integration test
`idle_queue_dispatch_preserves_client_id` observes `item/started` immediately
after `thread/queue/add` on the adding connection and confirms its user-message
`client_id` matches the supplied client ID. This proves that queue/add can
auto-dispatch while idle and retain the identifier in the native item.

In the same service, `start()` searches the queue for the supplied submission
ID and returns `InvalidRequest` with `queued submission not found: {id}` when
the row is absent. Therefore the observed `submission_not_found` is consistent
with auto-dispatch consuming the row before the explicit queue/start request;
it does not establish that this occurred in the Cairn run by itself.

The observer connection had only used `thread/loaded/list`, which is a metadata
query and does not subscribe it. Pinned `thread_processor.rs` shows that
`thread/resume` invokes `ensure_conversation_listener` for that request's
connection. `thread_lifecycle.rs` delegates to
`try_ensure_connection_subscribed`, which adds the connection to the thread's
subscriber set. The queue integration tests also confirm that a cold-resume
connection receives `item/started` for a queued item. The fixture now checks
loaded metadata and then calls `thread/resume` on the observer's own connection
before publishing the synthetic notice. A focused test verifies this order.

One bounded provider-free disposable run after this fixture change reached
`observer_subscription/before` after opening and initializing the separate
app-server connection. The item trace has no after marker. The outer runner
reported `FileNotFoundError` while reading the queue trace that the production
watcher normally creates; the watcher was not invoked, so this is not
classification of an app-server RPC refusal. The fixed item trace cannot tell
whether `thread/loaded/list`, `thread/resume`, or intervening setup failed; the
app-server/Python stderr was not retained. The watcher, queue/add, hook, Store
attempt, and local Responses request were not observed. Disposable PostgreSQL
and API cleanup completed. No second run was made. Thus the source-supported
subscription path is implemented in the fixture, but runtime notification
observation and the exact queue-receipt → item-start → hook → Store join remain
unverified. CAIRN-220 remains unaccepted.

The runner records separate fixed before/after/failure rows for the loaded
metadata request and resume subscription, plus watcher start. It checks whether
the queue-stage trace exists before reading it; a missing artifact is reported
with a fixed failure stage derived from the private observer trace. The initial
diagnostic change had not yet been exercised in a disposable run.

## Bounded observer-setup diagnosis

The next single fake-auth, `bwrap --unshare-net` disposable run added fixed
stage rows around the observer connection, initialize handshake, initialized
notification, loaded-thread query, and resume request. Its owner-only item trace
is `~/.cache/cairn-220-native-wake-proof/item-stage-92cb0ce3925447fe9ca716b76404bbca.jsonl`.
Connection setup, initialization, and `thread/loaded/list` all completed;
`thread/resume` was refused with the fixed exception class `QueueRefused`.
The watcher was not started, so no queue trace, hook, Store attempt, or local
Responses request exists for this run. Cleanup completed. The fixture does not
retain the native refusal message or code, so the exact server-side reason is
still unknown.

This localizes the prior boundary to the resume RPC rather than an earlier
observer setup step. The pinned source audit establishes that resume is the
subscription mechanism, but does not explain this refusal in the observed
runtime. No concrete correction is proved, so no retry or delivery replay was
made. Runtime notification observation, the queue receipt → item/started → hook
→ Store join, and CAIRN-220 acceptance remain unverified.

## Bounded resume-refusal classification

The observer trace now stores the integer JSON-RPC error code and a fixed
allowlisted reason class for source-known `thread/resume` refusal branches.
Classification uses only in-memory message matching and emits no raw text. The
separately requested, capped private-message trace field is described below.
Focused tests cover known source branches and verify that only the code and
enum reach the classification fields.

One new bounded fake-auth, `bwrap --unshare-net` disposable attempt again
completed observer connection, initialization, and `thread/loaded/list`, then
received `QueueRefused` from `thread/resume`. Its private 0600 item trace is
`~/.cache/cairn-220-native-wake-proof/item-stage-92404c3c8b29472d8c606a55b7790d35.jsonl`.
It records numeric code `-32600` and class `thread_resume_other`: none of the
source-known refusal patterns matched. The watcher was not started; no queue
trace, hook, Store attempt, or local Responses request exists. Cleanup
completed. The exact refusal remains unknown. No retry or replay was made.

## Private refusal detail and source-grounded observer setup correction

The next bounded fake-auth, `bwrap --unshare-net` disposable attempt retained
the `thread/resume` JSON-RPC error message only in its owner-only 0600 local
item trace, capped at 512 UTF-8 bytes. The trace is
`~/.cache/cairn-220-native-wake-proof/item-stage-1db3ecc7b7cf4c7dba4842dedc169a23.jsonl`.
The sanitized reason is that the just-started thread has no persisted rollout
yet. No raw refusal text or thread identifier is copied into this document,
source tree, or Plane. The watcher was not started; there is no queue trace,
hook, Store attempt, or local Responses request.

Pinned Codex 0.164 source maps a missing stored thread to this refusal in
`thread_store_resume_read_error`; `read_stored_thread_for_resume` is called on
the running-thread resume path before the app-server rejoins it. Separately,
`thread_start_inner` automatically attaches the `thread/start` request's own
connection through `ensure_conversation_listener`. The fixture now keeps that
initialized connection open and uses it as the item observer only when the
separate resume request matches the source-known missing-rollout refusal. Other
resume refusals remain failures. This avoids asking `thread/resume` to reload a
thread before its first user turn has produced a stored rollout.

Focused tests cover the private-message byte cap, fixed refusal classification,
and fallback selection.

## Corrected observer path: one bounded join attempt

One fresh fake-auth disposable run exercised the corrected same-connection
observer fallback under `bwrap --unshare-net`, with the pinned Codex 0.164.0
binary, isolated PostgreSQL/API, and loopback-only Responses stub. The private
0600 item trace is
`~/.cache/cairn-220-native-wake-proof/item-stage-33ac6fb190eb41cfaca35289c82dd2d5.jsonl`;
the private 0600 queue trace is
`~/.cache/cairn-220-native-wake-proof/run-queue-stage-33ac6fb190eb41cfaca35289c82dd2d5.jsonl`.

Observed join: `queue/add` confirmed its receipt and the delivery ID matched;
the same connection that `thread/start` had subscribed observed `item/started`
with matching client ID and thread; `UserPromptSubmit` occurred; and the Store
attempt's native turn hash matched the item notification. The fixture summary
therefore reports `exact_queue_hook_store_join=true`. The expected
missing-persisted-rollout resume refusal triggered the source-grounded fallback.
The explicit `queue/start` then returned fixed class `submission_not_found`
with the same receipt identifier, consistent with Codex's pinned idle
`queue/add` auto-dispatch behavior. The delivery remained leased with one
attempt; no settlement or completion is claimed. Exactly one local Responses
request reached the stub and no model-list request or external provider call
occurred.

This verifies the synthetic observer-to-Store join for this fixture run. It
does not establish accepted task outcome, production behavior, usefulness, or
full CAIRN-220 acceptance. The disposable root was removed; no delivery was
replayed.

## Scripted local continuation attempt

The fixture was extended to optionally script a local Responses continuation
through the existing native hook context. The script first invokes the exact
whole-version history read, checks that the tool result contains the selected
record and source body, then invokes that context's exact acknowledgement argv.
It reports handled only after `event-inspect` confirms that delivery is
`handled`, has one delivery attempt, and the script issued one acknowledgement
tool call. The mode is opt-in with `--scripted-handled`; the default fixture
still ends after the queue-to-Store join probe. Focused tests cover source
identity, acknowledgement argv, and refusal of incomplete results.

One fresh disposable run using the scripted path reached the correct
`queue/add` receipt and matching `item/started`, but did not observe
`UserPromptSubmit` or a Store attempt. Two Responses requests reached only the
loopback stub; the exact source read and acknowledgement were not observed.
The delivery remained leased with one attempt. The queue trace classified the
explicit `queue/start` refusal as `submission_not_found` for the matching
receipt, consistent with idle auto-dispatch, but does not explain the missing
hook/Store state. The run's owner-only trace ID is `bb5058b5fe6d4945b517a1035c7bc7a1`.
The retained trace does not identify which actor/hook boundary stopped the
continuation, so no further full run was made. The later opt-in and strict
failure-reporting edits were validated by focused tests and `make check` but
were not exercised by another disposable run. No source read, acknowledgement,
handled outcome, duplicate-effect claim, or CAIRN-220 acceptance is established
by this attempt.
