# Authenticated local access

## Agent event operations

The [event fabric](agent-event-fabric.md) uses this same token-bound API. All
operations use POST with JSON. No request can select its publisher or inbox owner.

| Endpoint under `/v1/` | Request | Result |
|---|---|---|
| `event-publish` | request_id, optional repo, kind, ref `{record_id,version}`, destination `{type,name}`, optional causation_id/correlation_id | Durable immutable event |
| `event-next` | optional repo/agent/lease_seconds | `{delivery: null}` or a leased delivery |
| `event-complete` | request_id, delivery_id, lease_id, disposition, optional code/draft | Terminal delivery with optional result reference |
| `event-retry`, `event-renew` | delivery_id, lease_id, optional lease_seconds for renew | Updated delivery |
| `event-subscribe` | request_id, optional repo/agent, topic, active | Subscription state |
| `event-subscriptions` | optional repo/agent, topic as exclusive cursor, limit | subscriptions, next_topic, more |
| `event-list` | optional repo/agent/topic, after numeric position, limit | events, next_after, more |
| `event-inspect` | event_id, optional after delivery UUID, limit | Event and visible recipient handling reports |
| `event-metrics` | optional repo/agent | Publication and inbox counters |

The raw JSON form is `cairn agent OPERATION < request.json`; the top-level event
commands are flag-based aliases. Subscription lists include inactive membership.
Completed deliveries remain in event history. Status never exposes lease tokens.
An expired or replaced lease returns HTTP 409 / `STALE_LEASE`. Completion request
retries return the original result, with no duplicated result note.

## Existing memory API

JSON request text must be valid UTF-8 with paired Unicode surrogate escapes.
Malformed text that would decode with replacement characters returns
`INVALID_REQUEST` before the operation runs. Valid pairs, literal backslashes
and explicit U+FFFD characters are preserved. Authentication, body limits,
unknown-field rejection and the one-request rule still apply.
[Verification and limits](verification/json-unicode-integrity-2026-09-09.md).


`cairn serve` exposes a Unix socket, mode 0600, in an owner-only directory.
Identity comes from a bearer token whose SHA-256 digest is in an owner-only
configuration file. Requests cannot choose principal, observer role or destination.
The `agent` role records testimony; `observer` also records host-observed spawn,
terminal, delivery, outcome, binding, coverage and task-state facts. Neither role
has operator authority endpoints or access to database credentials.

## Find an ordinary request format

Use local help before preparing a JSON request:

```sh
cairn agent --help
cairn agent search --help
cairn agent remember --help
cairn agent pull --help
cairn agent pull-evidence --help
cairn agent replace --help
cairn agent history --help
cairn agent assess-run --help
```

Help prints readable text and exits successfully without reading stdin, resolving
a home directory, opening credentials or contacting the API. Flag help for
`search`, `remember`, `pull`, and `pull-evidence` is generated from the same flag
definitions used by normal execution, and includes positional usage.
JSON-operation help covers `edit`, `revise`, `append`, `replace`, `cite`, `history`,
`assessments`, `assess-run`, and `recompile`; `-h` also works. Replace example
placeholders with actual values, then pass one JSON request on stdin. Connection
flags go before the operation. Normal responses retain their JSON envelope and
existing access checks.

The API's `edit` requires a complete `draft`; use `revise` for a body-only
replacement. Native `cairn_edit` combines those choices in a different tool
interface. Help explains this distinction and exact-request retries; a stale
`expected_version` still requires a fresh read and reconciliation.

## Configure a profile

Generate a local agent profile, keeping the plaintext token out of shell history:

```sh
python3 - <<'PY'
import hashlib, json, os, pathlib, secrets
root = pathlib.Path.home() / '.local/share/cairn'
root.mkdir(mode=0o700, parents=True, exist_ok=True)
token = secrets.token_urlsafe(32)
for name, value in {
    'agent.token': token + '\n',
    'identities.json': json.dumps([dict(
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        principal='agent:cairn', repo=os.getcwd(), role='agent', destination='local'
    )]) + '\n',
}.items():
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
PY
cairn serve
```

The example refuses to overwrite existing credentials. For additional profiles,
add a distinct principal and token digest to the configuration; up to 32 profiles
are supported. Restart the server after changes. Use `destination: hosted` for a
profile whose retrieved context goes to a remote model. Hosted profiles cannot
read local record bodies or protected usage reports. As with the operator CLI,
this is a cooperative local trust boundary; it does not isolate hostile processes
sharing the owner's Unix account.

In another terminal, submit one JSON request to an agent operation:

```sh
cairn agent create < note-request.json
cairn agent compile < compile-request.json
```

`--token-file FILE` and `--socket PATH` select another provisioned profile or
socket. Each omitted flag uses its default under `CAIRN_HOME`, or under
`$HOME/.local/share/cairn` when `CAIRN_HOME` is unset. Supplying both paths
avoids home-directory lookup, so an agent with neither environment variable can
still connect. An explicitly empty path returns `INVALID_REQUEST`; an invalid
explicit token path is not replaced with the default token. The agent client
never opens the database. Use the same request UUID for a transport retry. The
server has bounded request bodies and deadlines; an HTTP write failure does not roll back a committed mutation.

Token-file I/O failures return `CLIENT_SETUP_FAILED`, with a message directing
the caller to check the configured path and access. Unix socket dial failures
return `API_CONNECTION_FAILED`, directing the caller to check the socket and
service. Both use CLI exit 7 without printing the private path or credentials.
Existing token validation still refuses unsafe sources; empty, oversized and
non-owner-only files return `INVALID_REQUEST`. MCP startup reports token setup
failure on stderr with exit 1; connected MCP sessions report dial failures as
tool errors. Upgrade the CLI/MCP executable for these diagnostics; the existing
API and database suffice.

These codes describe the failing client operation, not an entire run's effects.
An API dial failure after earlier run steps now retains `API_CONNECTION_FAILED`
and exit 7 rather than the generic `RUN_FAILED` fallback. Inspect any returned
receipt, `process_state` and pending run artifacts before further action. Requests
are not automatically retried, and a lost response after connecting is not
classified as a dial failure. Trusted Go callers can still inspect the original
OS or cancellation cause with `errors.Is`/`errors.As`; rendered messages omit it.

Encoded JSON limits are 512 KiB for ordinary `create`, `edit`, `revise` and `append`,
1 MiB for `replace`, 8 MiB for `evidence`, and 128 KiB for other API operations. The larger envelopes
accommodate the existing 64 KiB decoded note and 1 MiB decoded evidence limits,
including JSON escaping and metadata. They do not increase stored source sizes
or retrieval budgets. The agent CLI, API client and server share these limits;
the store still validates size, repository and sensitivity. `agent remember`
uses `create`; native capture/edit tools use these same operations. Trusted local
JSON `create`, `edit`, `revise` and `append` also accept 512 KiB envelopes;
`replace` accepts 1 MiB for its two text fields.
Update both API and CLI before sending larger requests. Existing accepted requests
and their retry identities retain their behavior. Oversized envelopes, including
excess trailing whitespace, are refused before a write or retry reservation.


## Save an ordinary note

With a provisioned agent token, save text without constructing a `create` request:

```sh
cairn agent remember --kind lesson --shareable 'Run make test-integration for storage changes.'
```

`remember` is a client command that calls the existing `/v1/create` endpoint.
The server still checks repository scope, assigns the writer and witness, and
creates an ordinary A record. It does not promote the note or authorize an
instruction. The returned record is the server's normal `create` response.

Both `cairn remember` and `cairn agent remember` accept the same note options:

| Option | Default or behavior |
| --- | --- |
| `--repo REPO` | Current working directory; must match the agent's provisioned repository. |
| `--task TASK`, `--run RUN` | `*`, so the note can apply to later tasks/runs in that repository. |
| `--kind KIND` | `note`; ordinary kinds include `lesson`, `procedure`, `decision`, and `preference`. |
| `--shareable` | Omitted: local-only. Explicit: eligible for hosted delivery. |
| `--request-id UUID` | A fresh UUID per invocation unless supplied. |
| `--stdin` | Read note text from standard input, preserving line breaks. Cannot be combined with note arguments. |

For retryable capture, generate the request ID before the first attempt and
reuse it with the same options and text:

```sh
capture_request="$(python3 -c 'import uuid; print(uuid.uuid4())')"
cairn agent remember --shareable --request-id "$capture_request" 'Check integration before shipping storage changes.'
```

Repeating the second command returns the same record. Changing its text or scope
with that request ID returns `IDEMPOTENCY_CONFLICT`. Omitting `--request-id` on
repeated invocations creates separate notes. Authentication owns the request-ID
namespace; two writers do not share retry identity.

For a file you have explicitly chosen to capture:

```sh
capture_request="$(python3 -c 'import uuid; print(uuid.uuid4())')"
cairn agent remember --request-id "$capture_request" --stdin < note.txt
```

Use a new request ID for a new note. Empty/blank input, invalid UTF-8, input over
65,536 bytes, or a mixture of `--stdin` and argument text returns `INVALID_REQUEST`.
The API's encoded-request size limit still applies. A note that begins with a
hyphen can follow `--`, as in `cairn agent remember -- '-literal note text'`.
No directory scan, implicit stdin capture, or automatic shareability is added.

Use a profile configured with `destination: hosted` when the retrieved material
will enter a hosted model. Passing `--shareable` on a write does not change the
profile's read destination. An ordinary hosted agent can save a note and later
search/pull another writer's shareable note within the same authorized repository.

## API operations

Authenticated `version` takes `{}` and reports the running API executable without
reading repository data. `cairn agent ... version` also reports its own CLI build
and needs no stdin request. [Build identity and limitations](build-identity.md).

Operations: `create`, `edit`, `revise`, `append`, `replace`, `cite`, `delete`, ordinary `supersede`, `compile`, `recompile`, `index`, `expand`, `expand-evidence`, `get`, `history`, `evidence`, `usage`, `use-report`, `run-report`, `run-status`,
local-profile-only `conflicts`, `conflict`, `preview-retract` and `supersession`,
`assess-run`, `assessments`, and observer-only `spawn`, `terminal`, `task-state`, `bind-run`,
`run-package`, `run-index`, `claim-run`, `link-run-retrieval`, `register-context`, `delivery`, `outcome`, `usage-coverage`. All use `POST /v1/OPERATION` with JSON
matching the corresponding core request. `get` takes `record_id`.
`history` lists bounded retained version metadata or reads one exact body (optionally
as a byte excerpt with `span`), under
current repository/destination restrictions. See [record history](record-history.md)
for paging and historical-inspection limits.
`recompile` takes the original `receipt_id`, `query` and optional `entities`, with
no request UUID. It returns a historical package only to the original caller and
destination, subject to current privacy and forgetting checks. See
[historical reconstruction](currentness-and-replay.md#recompile-a-retained-read-set).
`assessments` takes `receipt_id` and returns the owner's full assessment versions
in ascending order, including reasons and evidence IDs. The profile's repository
and destination must match the receipt. It returns no evidence or package bodies;
an owned receipt with no assessments returns `[]`. See the
[review path](use-outcome-loop.md#qualitative-and-cumulative-review) for use and limits.
`assess-run` checks the same current repository and original destination before
applying a write or replaying its cached response. Changing a principal's binding
does not authorize access to the earlier binding's receipts; mismatches return
`AUTHORITY_DENIED`. This is enforced by the API's `AssessRunForDestination` path;
the trusted direct-store `AssessRun` interface is unchanged.
The [expansion contract](index-and-pull.md) binds
body/evidence pulls to an indexed version and one shared session budget. `compile` takes
no destination field; the configured profile owns that decision. An observer may
designate a configured ordinary `expansion_reader` when creating an index.
The [observed index contract](observed-index.md) preserves receipt ownership and
requires matching repository and destination. A hosted profile
cannot use the protected `use-report` or `run-report` endpoints. The server checks repository scope
at the store boundary as well as endpoint authorization. SIGTERM shuts down
requests and removes the owned socket; a second listener cannot replace it.

[Supersession](supersession.md) on the agent endpoint requires an A source and
omits `grant_id`. B supersession uses the operator CLI. The preview and
supersession inspection endpoints take `record_id`; a preview belongs to the
authenticated caller that obtained it.

An observer can use [the authenticated process runner](authenticated-runner.md)
with `cairn agent --token-file OBSERVER_TOKEN run ...`, without opening the database.
The server records observations; commands execute on the client host.

An observer may reserve one launch with `claim-run` and a `receipt_id`. The
receipt must belong to that observer. Repeated claims do not authorize another
execution; ambiguous responses require [owner-only run status](run-status.md)
using the same profile and receipt ID. The read grants no launch permission. [Restore fences](restore-fencing.md)
make old receipts unusable for new claims without changing historical outcomes.

## Striatum boundary

Striatum-next's accepted model requires context to enter lanes as sealed, declared
inputs. Its current supervisor renders pinned dispatch inputs before starting the
backend. Ambient memory injection into that prompt would violate those semantics.
This API supplies authenticated host ingestion and agent access, but it does not
change Striatum's pass contracts or declare Cairn integrated into real builds.

The next host integration must compile Cairn context before dispatch sealing,
include it as a permitted dispatch input, retain the Cairn receipt ID beside the
host attempt identity, and forward host terminal/task-state observations. Task
acceptance must come from the host's actual gate evidence; a backend process exit
cannot set it. No live Striatum lane was altered by this implementation.

The inspected Striatum commit `a6b1ae71d95cdf99200c10d8c6ce855d9da70a69`
declares only work graphs and optional diagnostic review ledgers as inputs to
build contract v3 (`catalog/passes/build.yaml`). Its driver sources ordinary
dispatch inputs from accepted artifact heads, with admitted evidence handled
separately (`internal/driver/session_dispatch.go`). An arbitrary Cairn export
file therefore cannot enter a real build as an extra input under that contract.
Native integration must establish the permitted input and provenance path in
the owning compiler contract before enabling delivery; an adapter probe cannot
establish that contract change.

The same inspected tree has an accepted
`catalog/target-states/recall-compiles-to-knowledge.yaml` target. It names hippo
recall entering knowledge promotion through existing foreign-head attestations
and exogenous-change records. That is a relevant existing admission path to
investigate before adding Cairn-specific artifact machinery. It does not declare
Cairn a producer, make raw recall authoritative, or authorize ambient build inputs.
The decision index still lists RFC 0015 as in flight, and the RFC index records
its ladder as parked. Current driver dispatch also seals empty prompt assets.
The owning RFC/decision and admission code therefore still need reconciliation;
that target entry does not establish an available native Cairn route. The subsequent
[native admission assessment](verification/native-admission-assessment-2026-09-08.md)
found the accepted promotion contract and implementation, traced the historical
Verified request to a fixture-labelled audit, and reproduced limitations in the
current knowledge checker. It preserves the distinction between recorded
artifact acceptance, current source, live knowledge, and native Cairn delivery.

## User services

The units under `deploy/` manage this repository's dedicated PostgreSQL cluster
and Unix API. They assume the checkout is `~/git/cairn`, the installed binary is
`~/.local/bin/cairn`, and the store is `~/.local/share/cairn`. Adjust those paths
before installation if your layout differs. The API requires the store service;
neither unit uses the host PostgreSQL cluster or opens a TCP listener.

After building/installing the binary and creating the owner-only identity/token
files described above, install both units into `~/.config/systemd/user/` and run
`systemctl --user daemon-reload`. If the dedicated cluster was started manually,
back it up, then stop it with `bash scripts/local-store.sh stop` before starting
it under systemd. Enable/start `cairn-api.service`; it brings up `cairn-store.service`.
The store service uses `CAIRN_BINARY` to run migrations with the installed binary.

```sh
systemctl --user enable --now cairn-api.service
systemctl --user status cairn-api.service cairn-store.service
journalctl --user -u cairn-api.service -n 30
```

The API runs for the user-manager lifetime. Whether that includes time before
login or after logout depends on the machine's existing user lingering policy.
Stopping the API leaves the dedicated store running. Stop the store service to
shut down both. Unit files, source code and generated credentials have separate
lifecycles; never commit the token/config files.

### Body-only revisions

`POST /v1/revise` accepts `request_id`, `record_id`, `expected_version`, `repo`
and `body`. It changes only an active A record's text while preserving its stored
draft metadata. The repository must match both the record and the authenticated
profile. It returns only `record_id` and `version`; no stored body or metadata is
returned by this endpoint. The same JSON is accepted by `cairn agent revise` and
the operator `cairn revise` command.

The version comparison and revision commit share the existing serializable edit
transaction. Exact request retries return the original revision, including after
a later edit. Changed intent under the same UUID returns `IDEMPOTENCY_CONFLICT`;
a new request based on an old version returns `VERSION_CONFLICT`. Read and
reconcile before submitting another revision. Full `edit` remains available when
changing other ordinary draft content is intentional. Upgrade the API and clients
together before using `revise` or the native edit tools' body-only form.


### Append selected guidance

`POST /v1/append` accepts the same fields as `revise`, but `body` is a suffix.
It adds those bytes verbatim to the expected current version, preserving the
existing body, draft metadata and source citations. Supply separating whitespace
in the suffix. The suffix must contain non-whitespace text and the combined body
must fit 65,536 UTF-8 bytes. Overflow is refused without writing a version.

The same JSON works through `cairn agent append` and operator `cairn append`.
The native MCP and OpenCode `cairn_edit` tools use `append` instead of `body`
to select this mode:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "PULLED_RECORD_UUID",
  "expected_version": 3,
  "append": "\n\nAdditional source-checked guidance.\n"
}
```

Supply exactly one of `body`, `append`, `replace`, `draft` or `evidence_citations` to the
native tool. The API returns only `record_id` and `version`; native tools also
return the request ID. The old body is not echoed. Exact retries return the
original revision even after later edits, without adding the suffix twice.
A changed suffix under the same request UUID conflicts. A stale expected version
requires a fresh read and reconciliation. Concurrent edits cannot silently replace
one another. Only active ordinary A records can be appended to; scope, sensitivity
and authority remain unchanged. Citations are retained, including degraded ones;
an appended sentence does not acquire verified support from that preservation.

Read the current note and check the addition against its guidance and sources.
Use append for a selected additive update; use replacement for corrections or
consolidation. Appending does not remove contradictions, obsolete guidance or
redundancy. Upgrade the CLI, API and adapter together before using this mode.


### Replace one exact passage

`POST /v1/replace`, `cairn agent replace` and `cairn replace` accept
`request_id`, `record_id`, `expected_version`, `repo`, `old_text` and `new_text`.
The current active A note must contain exactly one occurrence of `old_text`.
Matching is literal and case-sensitive, with no whitespace or Unicode
normalization. Overlapping occurrences also make the request ambiguous.
Supply enough surrounding text to identify the intended passage.

The native `cairn_edit` tool fixes the repository from configuration and takes:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "PULLED_RECORD_UUID",
  "expected_version": 3,
  "replace": {
    "old_text": "Run the old command.",
    "new_text": "Run the corrected command."
  }
}
```

Supply `replace` as the only edit mode. `old_text` must be nonempty;
`new_text` must be supplied, and may be an empty string to remove the passage.
Both fields have a 65,536-byte limit. The final body must remain nonblank and
fit 65,536 bytes; the request envelope allows 1 MiB for escaped old and new text.
A missing or ambiguous passage, omitted/null `new_text`, invalid text or an
invalid final body returns `INVALID_REQUEST` without a new version. Read the
current note and reconcile the request. A stale version returns `VERSION_CONFLICT`
even if the old passage no longer matches.

All other body bytes, draft metadata and source citations are preserved. The
operation uses the ordinary edit transaction and returns the new record ID and
version, without echoing the body. Exact retries return the original result even
after later edits; changed arguments with the same request ID conflict. Existing
full-body, append and draft edits retain their own retry identities.

This is selected text correction, not merging or automatic contradiction review.
Retained citations do not certify the corrected wording. Earlier versions remain
available for comparison. Upgrade the CLI, API and native adapter before use.


### Ordinary source citations

`POST /v1/cite` accepts `request_id`, `record_id`, `expected_version`, `repo`
and an explicit `evidence_citations` array. It creates a new A version with the
same body and metadata and replaces its references, returning only record ID and
version. Each nonempty citation uses a captured evidence ID, full SHA-256 and
optional byte spans. Empty `[]` clears current references; absent/null refuses.
The 128 KiB request limit applies. Text/full-draft edits preserve the existing
references. Upgrade all store writers before use; older edits drop A references.
See [ordinary citations](evidence-citations.md#ordinary-notes) for source checks,
retry behavior, native edit arguments and the qualification boundary.
