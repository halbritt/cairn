# Native session identity in the watch readers

The hook producer writes `cairn.native-session/1` state containing
`registration.native_session_id` and the returned `agent.native_session_id`.
It does not add an outer `native_session_id`. The drain configuration lookup and
D2 result projection previously required that outer field. A producer-shaped
record therefore stopped before the authenticated historical checks.

This cut starts at `311e10fd58b1d27c74d768b2eae2a69905f85f91` (tree
`c52326908cd4a43583327417ca189c03ec34bb5c`). That input carries the native census
consumer and its refusal characterization; it is not a qualified installation.
The original producer, state bytes, API, journal and authentication interfaces
are unchanged by the reader repair.

## Shared identity observation

`watchStateNativeSession(state *value.Value) (string, error)` is an internal
observation helper. It compares every present identity at these supported paths:

- `registration.native_session_id`
- `agent.native_session_id`
- the retained legacy outer `native_session_id`

Present identities must be valid strings and agree exactly. A valid legacy outer
identity can still identify retained state whose agent object lacks that field.
Malformed containers or identity values cannot be rescued by another valid
field. Missing, conflicting or unknown identity refuses. The helper preserves
spaces and Unicode without trimming or normalization. It applies the existing
agent-registration domain: a nonblank identifier of at most 256 UTF-8 bytes,
without NUL or invalid Unicode scalar values.

The helper returns observed correspondence data. It does not authenticate an
agent, authorize a read or declare a session settled. The two readers retain
configuration binding and exact path, agent/execution reference and state SHA
correspondence. D2 evidence still comes only from the existing successful
historical API and issued-journal checks. An unknown or conflicting identity
cannot contribute a result that satisfies a census drain requirement.

The census reader callsite is a separate owned change using the same committed
helper. This cut does not edit `shared_unit_census_files.go` or make a claim
about a later composition before its input checks.

## Verification boundaries

The Go identity tests start from records written by the actual
`ensureHookAgentSession` producer path. They cover legacy and nested forms,
exactly agreeing mixed forms, current/old conflicts, missing or malformed values,
registration limits, and unchanged input values and file bytes.

The existing `scripts/check_r06_census_d2.py` fixture supplies the real disposable
API/PostgreSQL historical checks. On this base its census fixture invokes the Go
producer and supplies the actual API-returned agent object without inventing an
outer identity. Its seven cases cover matching retained policy, mismatched claim,
missing journal, unknown attempt, wrong session, final policy mutation and a real
final claim. Their snapshot and final-invalidation assertions remain in force.
A fixture session is not substituted for evidence about a retained native row.

The initial committed helper (`32e45a5717bbc1ff4d96d533f221cc78aaf805b0`)
passed 2 top-level tests and 27 cases under race instrumentation with zero failures
or skips. Its actual image was pinned before use as
`bc3d99a883ed3f541fced0ac28da5b6b73574fb39e1074aae17ead05e875d4eb`.
That initial run covers the helper, not the later completed reader changes.
The completed reader source (`f063aa41f4730b0bbfd753bffef523bae5dd27e4`)
then passed 13 top-level tests and 51 cases under race instrumentation, with
zero failures or skips. The actual reader image was pinned before use as
`99cf4ed87e227b454d952075c6bce205d10131d714446abc4faa2ec32cfca8fd`.
The first reader attempt retained one failing fixture test and its three cases:
the ordinary ancestor guard refused an unsuitable fixture root. The correction
used the existing private fixture-directory helper and preserved the product
guard. A missing-journal refusal case was also added before the passing run.
Final candidate commands, exact source and image pins, counts, failures and raw
API receipts are recorded in the selected result separately.

No native provider or model is invoked by this cut. There is no live state,
configuration, service, credential, store, protocol or authority change. Reader
component checks do not establish native refresh, a complete unit, a common
image, installation acceptance or production rollout readiness.
