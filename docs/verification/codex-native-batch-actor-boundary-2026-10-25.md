# Codex native notice batch actor boundary, 2026-10-25

The requested provider-free actor fixture was attempted with Codex CLI 0.164.0,
a freshly staged, disabled candidate, fake local Cairn identities, a disposable
PostgreSQL/API, and a loopback Responses endpoint configured with
`requires_openai_auth = false`. The staged pin closure matched its files and the
manifest remained disabled. The fixture process used a private temporary
`CODEX_HOME`; it did not change installed account configuration or contact a
provider.

Both bounded runs reached the Codex native `turn/completed` notification before
the fixture observed its terminal actor step. The first run also lacked the
first item's handle in the actor's local handle map; that single targeted fix
did not move the observed boundary. The second run stopped at the same
`finished` assertion. The disposable wrapper removed its temporary root on
failure, so no actor advance response classes survived. The observed result
does not prove successor continuation, three settlements, fourth-item pending
state, or a Store defect.

The unverified actor orchestration was discarded. The disposable helper probe
now accepts `--diagnostic-output ABSOLUTE_PATH` to retain advance response
classes outside its disposable root. The path must be new and its parent must
be an owner-only directory. The writer uses exclusive creation, mode `0600`,
and flushes each JSONL row before the probe checks the response. Rows include
only allowlisted keys, the known response schema, boolean `ok`, `next` type,
known status/remainder codes (unknown values become `other`), bounded batch
index/limit integers, and return code. The cue, source, handle, identities, and
stderr are omitted. Synthetic provider-free checks passed for sanitization,
file mode, exclusive creation, path privacy, and placement outside the
disposable root.

This retained diagnostic contract makes a later bounded actor run reviewable;
it does not complete CAIRN-221's actor-continuation or task-acceptance evidence.

## Experimental Codex actor boundary fixture

Three bounded provider-free native Codex attempts were made on 2026-10-25 with
Codex CLI 0.164.0, a new empty synthetic-auth profile, bwrap, a disposable
PostgreSQL/API, and a loopback Responses stub configured with
`requires_openai_auth = false`. None contacted a provider. Each used a fresh
owner-only trace file created with mode `0600` outside its disposable root
before app-server startup. The trace records only fixed response/result/stage
classes; it contains no prompt, source, handle, ID, auth material or raw tool
output.

The first attempt stopped before a scripted actor response because the fixture
looked up its session file through an undefined `state_path` name. Its retained
trace is `/home/halbritt/.cache/cairn-221-native-batch-actor/actor-c407b5dd-d7e0-4b3a-8c35-613ed9f58c70.jsonl` (owner `halbritt`, mode `0600`, 107 bytes). It records only the registration message. The disposable root was
`/tmp/cairn-postgres.batch221-actor-20261025-01`.

The second attempt followed one source-driven correction, resolving the session
file using the lifecycle helper's SHA-256 key over binding, NUL, and native
thread ID. A static check caught a missing `hashlib` import before that rerun.
It reached the first batch actor request, then the fixture handler recorded an
`AssertionError` before sending a scripted tool call. A read-only ordering
check confirmed the session context belongs to the first of four notices. The
native binding uses `carol.token` (principal `agent/carol`), while that fixture
version explicitly called the session claim API as `agent/bob`. This identity
mismatch explains the assertion. The retained trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/actor-0fce1ae2-54ab-48d1-bf3c-c7db8c2fdf60.jsonl` (owner `halbritt`, mode `0600`, 210 bytes). Its rows are `registration_message/none` and `unexpected/tool_error`.

The final attempt followed a parent-authorized, source-proven correction: the
fixture checked the binding token's principal before startup and claimed using
`agent/carol`. Its trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/actor-8cb26488-8558-41e7-9043-b856092a9da0.jsonl` (owner `halbritt`, mode `0600`, 674 bytes). The trace shows registration; a read tool call; a successful read result; a settle tool call with successful settlement; an advance tool call; then `unexpected/tool_error` at `advance_tool_result`.

Read-only state confirms the store transition: the first notice is handled once,
the second is leased once as batch index 2 under the same batch root, and the
third and fourth notices remain pending with zero attempts. Two compact
contexts exist. The advance command succeeded and leased the second notice, but
the fixture failed before returning the successor cue to Codex. The checker
serialized the outer tool result with `json.dumps` and searched that text for
unescaped JSON fragments, even though `custom_tool_call_output.output` wraps
the command's JSON as a nested string. That fixture parsing bug caused the
`advance_tool_result` assertion. A payload-free classifier was added and has
focused unit coverage for successor, batch-limit, and error shapes. There was
no further actor attempt.

**Result:** this fixture establishes neither actor continuation nor three
settlements. The first source read, handled settlement, and store advance to
notice two are verified fixture plumbing only. Codex did not receive the
successor cue, and no second or third notice was read or settled. The actor
fixture remains experimental; this evidence does not establish task acceptance,
paired usage or latency, or rollout acceptance. CAIRN-221 remains In Progress.
The trace writer tests cover allowlisting, fixed stage classes, exclusive
creation, private mode and external placement. The broader disposable
`make test-integration` suite passed before the standalone actor path was
enabled; it is separate from this experimental actor result.

## Post-classifier bounded attempts

The first post-classifier run was launched once through `make test-integration`
with Codex 0.164.0, fake local identities, a fresh disposable PostgreSQL/API,
and a loopback Responses endpoint with `requires_openai_auth = false`. A later
source audit corrected an earlier network-boundary description: the fixture's
nested bwrap confined its filesystem and IPC, but this launcher did not pass
`--unshare-net`; the Codex process shared the host network namespace. No
provider request was observed, but that run did not establish network isolation.
Its owner-only trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/actor-9b9201b2-bc7b-4428-939a-b9e667ee4963.jsonl`
(0600). It records registration, exact-source read, handled settlement, and an
advance tool call; the next fixed class is `tool_error` at
`advance_tool_result`. The integration command failed at the actor fixture
assertion and is not a pass.

The retained disposable Store was restarted only for read-only inspection. It
showed notice one `handled`, attempts 1, batch index 1; notice two `leased`,
attempts 1, batch index 2; notices three and four `pending`, attempts 0. Thus the
advance command committed the lease for notice two before Codex's tool result
was classified. The retained Codex SQLite logs did not contain a uniquely
identifiable advance tool-result object for local reclassification.

One targeted provider-free retry used a fresh disposable PostgreSQL/API and the
same pinned fixture with filesystem/IPC isolation but no separate network
namespace. Its private trace again reached
`advance_tool_result` with `tool_error`; it did not retain a parse-stage code.
That trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/actor-targeted-fa642378-55d8-4027-a30d-5c43742d5b50.jsonl`
(0600).
Source inspection found that the diagnostic write occurred after assertions in
the advance branch, so a failed classification could skip the parse-stage row.
The writer now persists one allowlisted parse-stage code (`is_error`,
`outer_json_invalid`, `exit_nonzero`, `inner_json_invalid`,
`schema_or_ok_invalid`, or `success`) immediately after parsing and before any
advance assertion. Focused tests verify that a failing advance class preserves
the mode-0600 stage row. This diagnostic change has not had another actor run.

No actor continuation, successor source read, second/third disposition, terminal
batch-limit result, same-turn completion, or accepted task outcome is established
by these attempts. The only established Store transition is the first handled
notice followed by one lease of notice two. `make test-integration` failed at
the native actor assertion; the isolated retry also failed at the same boundary.
CAIRN-221 remains In Progress.

## Network-isolated preflight and bounded attempt

The earlier network-boundary statement above applies to the first three
post-classifier attempts only. A later launcher preflight used an outer
`bwrap --unshare-net` namespace and a nested Codex bwrap that inherited it.
The parent and child network-namespace inode values matched; the child reached
the fixture handler on loopback, while a connection to a synthetic TEST-NET
address failed with `ENETUNREACH` (errno 101). The disposable API and PostgreSQL
Unix sockets were mounted at the same short paths inside the namespace. The API
socket directory was private and short enough for the platform Unix pathname
limit. No provider endpoint was involved.

One bounded Codex 0.164.0 batch actor invocation then reached only its
registration message. Its fresh owner-only trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/direct-netns-yjVaWJ/actor-direct-ff0b288c-4a21-40df-8628-137d9fc4f9a7.jsonl`
(mode `0600`); its sole row is `registration_message` at `registration`, with
no tool result. The exact token bytes were hashed against the synthetic
identity mapping before the disposable actor root was created; the API-only
version preflight passed inside the same network namespace. The invocation did
not reach notice publication, source read, settlement, or Store advance. No
hook metadata or Store state from this fresh invocation was retained, so its
post-registration stop cause cannot be classified further. The older retained
disposable cluster belongs to an earlier run and does not contain this run's
batch notices.

This network-isolated attempt establishes loopback reachability and synthetic
egress denial for the fixture boundary, but it does not establish batch actor
continuation. No actor retry followed this registration-only result. All
Codex-generated messages and tool behavior in this fixture remain scripted
synthetic Responses, not accepted task outcomes. CAIRN-221 remains In Progress.

## Durable pre-notice diagnostic, 2026-10-25

The direct runner now persists fixed hook entry/exit classes, four ordered
Store slots as booleans, preflight classes, fixture exception class, and actor
terminal class in its owner-only `0600` trace. It excludes hook turn IDs,
source, token values, output and raw errors. Each trace row is flushed and
synced before the fixture's assertion can unwind and before disposable cleanup.
Focused tests exercise sanitization, fixed classification, and file mode.

The first isolated direct attempt reached the registration response, then the
controller's `agents list` call failed before notice publication. Read-only
reproduction identified a controller argument-order error: it placed `list`
after global flags, yielding `INVALID_REQUEST`; the integration harness places
the subcommand before those flags. `fixture_api_argv()` now shares and tests
that ordering. No notice was published in this first attempt.

One targeted retry used a fresh disposable PostgreSQL/API, a new fake-auth
profile, Codex CLI 0.164.0 through its explicit Node entry and package root,
and the same outer `--unshare-net` boundary. The exact token bytes matched the
`agent/carol` identity row before actor-root creation, the mounted short API
socket authenticated, and the retry repeated the namespace preflight: its
nested and parent namespace inode values matched and differed from the host;
loopback reached the fixture handler; connection to synthetic TEST-NET failed
with `ENETUNREACH`. The trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/retry-XEdC9u/actor.jsonl`
(mode `0600`).

The retry again stopped after the registration response, before the actor task
turn. The fixture's next lookup found zero registered Codex agents and raised
`StopIteration`. The hook metadata file was absent, the read-only API agent
list contained zero rows, and read-only Store counts were zero for
`agent_event`, `agent_delivery`, and `agent_session`. The trace records four
`not_created` notice slots and actor terminal `not_started`; no source read,
settlement, or advance occurred. The revised trace writer now also records a
fixed fixture exception class on future failures. No further actor run was
made.

Source comparison with `scripts/check_codex_compact_native.py` identified a
fixture race in the registration-only stop boundary. The full-inbox fixture
waited for the registration turn's `turn/completed` notification and queried
the agent list immediately. The compact fixture instead waits for the
registration turn's wrapper-written Stop completion marker before inspecting
registered state. The metadata wrapper writes that marker only after the Stop
hook command returns. Since the latest trace had no hook metadata and the
fixture ended directly after the registration response, the supported
explanation is that the fixture advanced/cleaned up before the hook completion
boundary; it does not identify a Codex hook invocation failure.

The full-inbox fixture now waits for a matching successful Stop marker before
querying the native agent. Focused tests cover delayed completion, wrong turn,
and nonzero hook exit. This source-level correction has not been exercised by
another actor attempt; the retained trace remains evidence of the old race, not
of the corrected path. No batch continuation or accepted task outcome is
claimed. CAIRN-221 remains In Progress.

## One bounded attempt after the Stop-marker correction

One fresh fixture invocation used newly generated synthetic `agent/alice` and
`agent/carol` identities, a new disposable PostgreSQL/API, empty fake Codex
auth, Codex 0.164.0 and an outer `bwrap --unshare-net` namespace. The namespace
inode differed from the host, and a synthetic TEST-NET connection was denied.
The corrected batch fixture call stopped with `FileNotFoundError` before its
diagnostic writer created the fixed trace file. The runner catches that
exception around the child `check(...)` invocation; it does not arise while the
host runner reads a missing trace. The exact setup substage was not retained.

Read-only aggregate Store counts in that fresh database were zero for
`agent_session`, `agent_event`, and `agent_delivery`. No hook metadata or actor
turn was observed, and no fixed response/stage rows were produced. This is an
unclassified fixture setup failure, not evidence of native Codex behavior or a
registration failure. No retry followed. Batch continuation and accepted task
outcome remain unverified; CAIRN-221 remains In Progress.

## Corrected synthetic token and one final bounded run

The disposable runner was corrected to write the synthetic token without a
newline and hash those exact file bytes for the identity binding. One fresh run
then passed the token identity, API socket and authenticated API version
preflights; it also completed candidate/path checks, task loading, workspace
creation, coordination copy, hook installation, sandbox launcher build, and
local Responses server setup. `bwrap --unshare-net` isolation and denial of a
synthetic TEST-NET connection were confirmed.

The fixed trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/posttrace-ecb1a835-883f-4b93-9a3d-f256ca3c4f5c.jsonl`
(mode `0600`). Codex returned the scripted `registration_message`, and the
fixture then timed out at `registration_hook_wait` awaiting the matching Stop
completion marker. The trace has no hook metadata rows, four notice slots
`not_created`, and actor terminal `not_started`. Fresh disposable Store counts
for `agent_session`, `agent_event`, and `agent_delivery` were zero. This shows
that the Stop marker was not observed before timeout; it does not establish
why, or prove a Codex hook invocation failure. No further actor run followed.
Batch continuation and accepted task outcome remain unverified; CAIRN-221
remains In Progress.

## Pre-setup fixed trace and bounded retry result

The batch fixture now creates its owner-only mode-0600 trace before entering
fixture setup. It records allowlisted start/completion stages and, on a setup
exception, only its class and a filename role matched against known inputs; raw
exception text and paths are never retained. Focused tests verify that a
missing selected Codex path is attributed to `selected_codex` without recording
the path.

One fresh fake-auth attempt used a disposable database, new synthetic
identities, outer `bwrap --unshare-net`, and confirmed synthetic egress denial.
The durable trace
`/home/halbritt/.cache/cairn-221-native-batch-actor/pretrace-634b1a77-8a9a-476e-86c3-c9c14671cbeb.jsonl`
(mode `0600`) records successful Codex/candidate path checks and disposable DB
presence, then stops at `verify_token_identity` with `RuntimeError` and
`filename_role=unknown`. Source review identified the disposable runner's
input mismatch: it wrote a trailing newline to `carol.token`, while its
identity-map digest was computed over the token without that newline. The
fixture hashes the token file bytes, so the principal match correctly failed
before hook registration. This was not a Codex actor result.

The fresh database remained empty: `agent_session`, `agent_event`, and
`agent_delivery` counts were zero. No hooks, notices, or actor turn were
observed. No retry followed. Focused fixture/diagnostic tests (18) and
`make check` passed. CAIRN-221 remains In Progress.

The fixture did not retain the raw app-server notification stream. Its fixed
`registration_turn` completion row is nevertheless written only after the
verification loop observes `turn/completed` whose turn ID matches the
registration `turn/start` response, then passes the completion assertion.
Thus the registration turn did reach matching `turn/completed`; the following
Stop-marker wait timed out. A Stop completion marker was expected after that
turn, but no hook metadata was retained to establish whether Codex invoked the
Stop hook or why its marker was absent.

## Stop hook wrapper diagnostics

A read-only source audit used the pinned Codex CLI 0.164.0 source tag
`rust-v0.164.0` (commit `6aaac694803004a63c6f393d153c96d22521a6b9`). The fixture's
hook wrapper is reached only after Codex starts its configured command. The
wrapper previously wrote metadata only after reading and parsing its JSON
stdin, and wrote its exit row only after `subprocess.run` returned. Therefore
an absent metadata file could not distinguish no wrapper entry from wrapper
input or command failure.

The wrapper now flushes fixed diagnostic rows to its private mode-0600 metadata
file at entry, after payload validation, before command launch, and after
command completion or a bounded timeout/OS error. The external fixture trace
copies only allowlisted event/stage/error/exit classes; turn IDs, paths, input,
stdout/stderr, auth and raw exceptions are excluded. Tests cover entry and a
Stop command timeout and assert the external trace contains only those classes.

The fixture installs Stop as a command handler without an `async` field. In
pinned Codex 0.164.0, that field defaults to `false`; synchronous Stop handlers
are awaited by `run_turn_stop_hooks` before turn completion. Therefore a
matching `turn/completed` after this configuration is not explained by the
async-dispatch path. The prior run retained no hook metadata, so it remains
inconclusive about whether Codex discovered or launched the wrapper; the
following section records the first run with wrapper-stage diagnostics.

## One fresh run with wrapper-stage diagnostics

One fresh fixture run used new synthetic `agent/alice` and `agent/carol`
identities, a new disposable PostgreSQL/API, empty synthetic Codex auth, and
outer `bwrap --unshare-net`. The fixture proceeded only after confirming its
network namespace differed from the host and a TEST-NET connection failed with
`ENETUNREACH`. It passed token identity, API, hook registration, launcher, and
local Responses setup, then received the scripted registration message and
observed the matching registration `turn/completed`.

The run timed out at `registration_hook_wait`. Its owner-only trace is
`/home/halbritt/.cache/cairn-221-native-batch-actor/actor-stopdiag-1792944549-3887117.jsonl`
(mode `0600`). The fixed trace contains no `hook_probe` rows and no hook
metadata or Stop marker was present in the private Codex profile. All four
notice slots are `not_created`, the actor terminal is `not_started`, and
read-only fresh-DB counts are `agent_session=0`, `agent_event=0`, and
`agent_delivery=0`.

The fixture's installed Stop handler is synchronous: its generated command
entry omits `async`, and pinned Codex 0.164.0 defaults that field to `false`
and awaits synchronous Stop handlers before emitting turn completion. This run
observed no wrapper entry despite matching `turn/completed`. A separate
provider-free, no-turn app-server probe below confirms the same fixture profile
loads the trusted Stop handler at the actor workspace CWD. A separate direct
command probe found a wrapper dependency missing from the isolated profile; it
now verifies command viability after that staging fix. The failed actor run
still has no app-server log or runtime hook listing, so whether Codex dispatched
the command remains unknown. No hook invocation failure, batch continuation, or accepted task
outcome is inferred. No further actor attempt was made; CAIRN-221 remains In
Progress.

## Provider-free Stop registry probe

A separate no-turn probe recreated the isolated fixture profile, including the
empty synthetic auth file, loopback-only Responses endpoint configuration,
`hooks.json` wrapper command, and `trust_codex_hooks` registration step. It
then queried pinned Codex 0.164.0 `hooks/list` with the actor workspace as CWD
inside `bwrap --unshare-net`. The response contained six fixture hooks and one
Stop entry from the expected settings file with the exact wrapper command. The
Stop entry was enabled, trusted, and synchronous (`async=false`); app-server
reported zero discovery errors or warnings. There was no actor turn, Store
mutation, or loopback request. The private mode-0600 summary is
`/home/halbritt/.cache/cairn-221-native-batch-actor/hook-discovery-summary-20261025.json`.

This establishes registry discovery and configuration for the recreated
profile at the actor CWD. It does not show whether the failed actor turn
dispatched the handler. That runtime boundary remains unresolved; no actor
rerun followed.

### Pinned turn-stop path and fixture response

The fixture's registration response is a completed assistant message containing
one `output_text` item and no `custom_tool_call` (`scripts/check_codex_full_inbox_native.py`,
`response_stream` and the `msg_initial` branch). Pinned Codex 0.164.0
`core/src/stream_events_utils.rs` routes a non-tool message through the
`Ok(None)` branch and does not set `needs_follow_up`; its tool-call branch is
the one that sets the flag. In pinned Codex, when `needs_follow_up` is false,
`core/src/session/turn.rs` awaits
`run_turn_stop_hooks` before the turn ends (around line 724). The only early
return in `core/src/hook_runtime.rs`'s target selection for this case is an
internal synthetic subagent; root turns select `Stop` (around lines 399-445).
`hooks/src/events/stop.rs` selects handlers for Stop with no matcher input, and
`hooks/src/engine/dispatcher.rs` does not apply a matcher condition to Stop.
With the probed synchronous handler, dispatcher execution awaits the command
(`dispatcher.rs`, around lines 117-151), and the task emits `TurnComplete`
after turn-stop lifecycle handling (`core/src/tasks/mod.rs`, around line 865).

The fixture creates a root thread with `thread/start`; it is not an internal
subagent. The inspected path contains no condition that excludes the configured
Stop handler because the assistant response used no native tool call. Source
therefore provides no explanation for the absent wrapper entry. Registry
discovery is confirmed, but dispatch, process launch, or wrapper entry during
the failed actor turn remains unknown. No actor rerun followed.


## Synthetic Stop command viability

A direct command probe used the trusted command from the isolated registry with
a synthetic valid Stop payload, empty fake auth, `bwrap --unshare-net`, a fresh
disposable PostgreSQL/API, and a loopback-only Responses endpoint. It did not
start a Codex actor turn or create a notice. Before the fix, the command exited
nonzero before the wrapper wrote any entry stage. The isolated profile contained
`record_codex_hook_metadata.py` but not its imported
`codex_fixture_diagnostic.py` dependency; the app-server sandbox exposes the
profile and does not expose repository `scripts/` on Python's import path. The
first process error text was not retained.

The fixture now stages both private mode-0600 Python files into the Codex
profile. A focused test runs the staged wrapper with a minimal child command
and no repository import path. A fresh post-fix direct invocation of the exact
trusted command reached `wrapper_entry`, `payload_valid`, `command_start`, and
`command_complete`, exited zero, and wrote its Stop completion marker. The
owner-only fixed summary is
`/home/halbritt/.cache/cairn-221-native-batch-actor/stop-command-summary-20261025.json`
(mode `0600`); it retains only stage, exit, and bounded error classes. No
loopback request or provider call occurred, and the disposable API/cluster were
removed.

This proves the wrapper and coordination command can run in isolation after
its helper is staged. It does not establish that Codex dispatched the command
in the failed actor turn.

## Verified three-notice batch continuation and fourth-notice pending boundary (2026-10-26)

Source inspection of `scripts/check_codex_full_inbox_native.py` and
`scripts/native_notice_batch_diagnostics.py` identified two remaining fixture
defects after the wrapper dependency staging:

1. `scripts/check_codex_full_inbox_native.py` authenticated the binding token
   against `api_role`, then hardcoded `"bob"` for the subsequent `agents list`,
   `agents claim`, and `event-inspect` API calls even when the staged binding
   used `carol.token` (`agent/carol`). Replacing the hardcoded `"bob"` calls
   with `api_role` aligned the controller with the verified binding principal.
2. In Codex 0.164.0 `js_repl`, `text(await tools.exec_command(...))` wraps the
   command stdout in `Chunk ID: ...\nWall time: ...\nProcess exited with code 0\nOriginal token count: ...\nOutput:\n{...}`.
   `_parse_advance_tool_result` in `scripts/native_notice_batch_diagnostics.py`
   attempted `json.loads` directly on `outer.get("output")` when `output` was a
   string without `returncode`, returning `inner_json_invalid` and `tool_error`
   even though `cairn-inbox advance` had succeeded and leased the successor
   notice. Unwrapping the `\nOutput:\n` payload when `Process exited with code 0`
   is present restored exact classification of `cairn.inbox.advance/1`
   (`advance_successor` and `advance_limit`).

Focused unit tests (`PYTHONPATH=scripts python3 -m unittest scripts/test_codex_full_inbox_native.py scripts/test_native_notice_batch_diagnostics.py`,
20 tests) cover these paths. Because the three-notice batch turn executes nine
`tools.exec_command` calls and nine synchronous `PostToolUse` hooks (~50-60s on
this host), the disposable runner set `turn_timeout=120`.

One fresh provider-free disposable run under `bwrap --unshare-net` with empty
fake auth (`{}`), synthetic `agent/alice` and `agent/carol` identities,
disposable PostgreSQL/API, and a loopback-only Responses server passed
end-to-end:

- Private mode-0600 trace:
  `/home/halbritt/.cache/cairn-221-native-batch-actor/actor-post70cdb69-0a24b290-52d1-4c77-ac25-ed5c7426d770.jsonl`
  (144 lines).
- Fixture summary: `result="PASS"`,
  `schema="cairn.codex-full-inbox-native-fixture/1"`,
  `workflow_id="native.notice_batch"`, `provider="local_responses_stub"`,
  `provider_call=false`, `native_hooks_registered=true`,
  `native_turn_joined=true`, `scripted_response_count=11`,
  `verified_store_boundary="first_three_notices_handled_fourth_pending"`,
  `task_outcome="unassessed"`.
- Trace verification: all 11 scripted exchanges completed (`registration`, three
  iterations of `read_notice` -> `settle_handled` -> `advance_successor` with
  `advance_result_stage="success"`, and `final_message` after `advance_limit` at
  `batch_index=3, batch_limit=3`), Store slots 1–3 recorded
  `handled=true, attempted=true`, slot 4 recorded `pending=true, attempts_zero=true`,
  and `actor_terminal="completed"`.

This completes the provider-free native Codex batch continuation and
three-notice cap fixture proof for CAIRN-221. Live paired provider usage,
latency distribution, and canary activation remain deferred under the
[token-efficiency gate](token-efficiency-gate-2026-10-24.md).
