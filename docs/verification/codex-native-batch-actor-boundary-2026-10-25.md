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
