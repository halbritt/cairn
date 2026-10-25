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
