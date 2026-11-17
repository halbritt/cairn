# Fresh Claude settings owner

`cairn-stage-lifecycle native-claude-owner` composes checked settings publication,
a dedicated native child owner, and an authenticated read of that child's Cairn
custody. The implemented slice qualifies Claude Code **2.1.326**. Qualification
is control-only initialization in a fresh private settings namespace; live host
adoption needs separate authorization and canary admission.
The qualified image SHA-256 is
`b6b3647d981130dc44fc670846ae97b6cde531ffb667cf9c4d17cfcafb38cb37`;
another image with the same reported version is unsupported.

The controller owns native stdin from birth. Its admission, publication, inverse
and recovery operations share one mutex. Each initialization allocates a fresh
native UUID and process, sends `initialize`, `get_settings` and
`get_hooks_listing`, and verifies effective command hooks against the owned
leaf. It sends no user/model prompt. Command hooks execute normally. Other hook
types are refused before launch. A failed hook response, stale effective cache,
incomplete join or remaining descendant leaves the owner unqualified.
After the first joined session, a fresh complete G2 is also required before
another native initialization is admitted.

Existing clients, resume, arbitrary user turns, unmanaged settings namespaces,
and external restart supervisors are unsupported. The controller cannot attach
to them or certify their quiescence. An empty owner list, reported idle or a
disappeared PID cannot supply native authority.

## Public controller

All paths and SHA-256 pins are explicit. This example names already approved
disposable inputs; invoking it is not a production transition grant:

```sh
cairn-stage-lifecycle native-claude-owner \
  --client /absolute/claude-2.1.326 \
  --client-sha256 SELECTED_NATIVE_SHA256 --client-version 2.1.326 \
  --source-settings /absolute/original-settings.json \
  --source-sha256 ORIGINAL_SETTINGS_SHA256 \
  --parent /absolute/private-parent \
  --account-home /absolute/application-account \
  --work /absolute/workspace
```

The existing private parent receives a fresh owner directory and `settings.json`.
The source leaf remains unchanged. The child retains normal host HOME, PATH and
authentication; its explicit `CLAUDE_CONFIG_DIR` selects application state.
`--setting-sources ''` and the owned `--settings` leaf select the native hooks.
The selected image is hashed, retained by descriptor, executed through that
descriptor, and checked against the actual child executable and version.

Stdout starts with a `cairn.native-owner-control/1` observation. Stdin then accepts
one JSON object per line. Every request needs a canonical nonzero UUID `id`;
mutation/admission requests also need the current `expected_generation`:

```json
{"id":"11111111-1111-4111-8111-111111111111","operation":"inspect"}
```

| Operation | Behavior |
| --- | --- |
| `inspect` | Pure selected observation; returns QUIESCENT, BUSY or UNKNOWN. |
| `initialize` | Launch and initialize one fresh owned stdio client. |
| `close-native` | Close native input, drain output and join that actual child; require no descendants. |
| `select` | Validate `publication` material below; never publish it. |
| `publish` | Recheck G2 and selected bytes through the first mutation, prepare recovery, then exchange the owned leaf. |
| `inverse` | Restore the exact retained original inode when all guards still match. |
| `recover` | Resume a pending live transaction after pure pinned-record inspection and fresh G2. |

The request limit is 1 MiB and 1,024 unique IDs per controller lifetime. Exact
byte-identical retries return the original response; reusing an ID with different
bytes refuses. IDs are never evicted. Serialized observations and recovery files
cannot reconstruct a live owner. Operations have a three-minute bound; a
cleanup deadline kills and reaps the owned direct child and retains UNKNOWN.
It does not convert failed cleanup into a successful join.

`select` requires a `publication` object containing:

- `selection`: the complete three-file hook generation from the existing
  off-path renderer (`hook-settings.json`, `previous-settings.json`,
  `hook-selection.json`). Its preimage path/hash must name this owner's leaf.
- `retained_selection`: the complete verified Source `/2` retained package for
  this Claude member, including original profiles, literal argv, closures and
  evidence. Existing independent Go package validation is reused.
- `retained_memory` and `retained_coordination`: exact original profile pins.
- `replace_commands`: explicit `memory` and `coordination` lists, each containing
  this retained member's literal shell command.

Both roles remain bound and preserve API route, account, binding, durable state
domains and activation decision. The new `/3` generation's complete receipt,
source closure and binding use the existing production reader. Each Go launcher
is independently derived from its receipt vector and checked against actual
bytes, image, role, profile and timeout. No selected file's mode is treated as
kernel immutability. Every G2 pass repeats current byte/metadata checks.

## Supported G2 coverage

G2 covers only the controller's joined, retired, fresh-only native IDs and their
selected memory/coordination domains. Two bounded fresh passes must agree on:

- Actual controller origin, native join history, empty descendant census,
  settings and complete selected `/2` and `/3` material.
- Local state identity, process identity, retirement, current execution,
  context revision and restore generation; no unresolved local attempt fields.
- Authenticated Store custody, including prior-execution holds, inbox leases,
  associated worker wakes and destination-eligible pending deliveries. A retained
  result policy additionally needs its durable poll's terminal correspondence.
- Indexed and unindexed journals, ownership and replay/reply state; validated
  memory ledgers and initialized markers with every charge retained.

Both memory files must exist for every joined bound session. Missing both is
UNKNOWN; it cannot be interpreted as an uninitialized empty grant history.

The local-only `agent session-quiescence` operation is a read through the base
profile. It remains readable after `agent-leave`, refuses foreign or stale
executions and restore generations, and fails above 128 lease/wake rows instead
of truncating. Expired leases and stopped presence do not erase custody. It
claims, settles, releases, renews and normalizes nothing. Its explicit scope
does not replace repository-wide D4.

Positive memory grants under joined UUIDs remain charged and inspectable. This
owner never resumes or reuses those UUIDs; it never refunds or resets their
allowances. A pending publication makes public inspection UNKNOWN and blocks
new admission until checked recovery completes. Missing, changing, inaccessible,
incomplete or unsupported evidence remains UNKNOWN or BUSY.

## Exact inverse and failure state

The publisher replaces only selected hook rows semantically and preserves every
byte outside the hooks value. Linux `RENAME_EXCHANGE` retains the original inode
for inverse; an absent leaf uses `RENAME_NOREPLACE`. Original inode, raw bytes,
absence, uid/gid, mode, nanosecond mtime, ACL and xattrs must correspond. **ctime
cannot be restored** and is an explicit exception. Partial candidate/recovery
objects are retained on uncertain failure; drift is never guessed safe to erase.

Pure recovery inspection classifies prepared, published, reversed or unknown
state. A successful rename with failed readback reports committed but unverified
state. Only the live transaction can mutate during recovery; after controller
loss, the retained evidence supplies inspection and requires new qualification
before any consequential action.

Native-only publication does not call unit-bootstrap switch or exchange the
lifecycle binding directory. Retained `/2` bindings, Python watchers and issued
commands remain in place. The later whole-unit/Go-watcher exchange remains held.

## Validation

```sh
mise run test:native-slice1 -- /absolute/fresh-report-directory
```

This explicit host fixture requires the installed pinned Claude image and
`strace`, preserves normal host tools/authentication, and creates its own
PostgreSQL database, API, identities, bindings, application account and clients.
It verifies real native `/2` → Go `/3` → exact inverse → fresh Python `/2`,
effective hooks, causal executable/receipt traces, mandatory-memory accounting,
refusals, and interrupted API recovery. Separate synthetic callback cases verify
both durable `/2`–`/3` directions with positive grants, leases, journals, issued
reply retries and retained watcher recovery; those cases do not qualify native
authority or usefulness. No model prompt or live adoption is exercised.

The pinned native fixture is selected explicitly and is separate from ordinary
`mise run test:integration`, which remains required for Store changes.
