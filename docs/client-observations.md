# Observed client implementations

CAIRN-114. Use this when a saved instruction names an option, tool or behavior
that the running installation does not seem to have and you need to know which
declared client implementations are actually contacting the API. It answers one
question: *which declared client implementations has this API process heard from,
under my principal, in the last 24 hours?* It is a bounded observation, not an
inventory, a health check or a release gate. [Build identity](build-identity.md)
still identifies each executable separately; `cairn_client_info` still inspects
the one MCP facade you are talking to.

```sh
cairn clients [--profile NAME | --token-file FILE] [--socket PATH] [--limit N]
cairn agent --token-file FILE [--socket PATH] clients [--limit N]
```

Authenticated `POST /v1/clients` takes `{}` or `{"limit": 1..128}` (default 50).
`cairn clients --help` needs no credentials, home directory or connection.

## What a client reports

The CLI and the MCP facade attach one optional, transport-only HTTP header,
`Cairn-Client-Diagnostics`, to their API requests: unpadded base64url of one JSON
object (at most 4096 encoded and 3072 decoded bytes, depth 6, no duplicate
member names (including case variants at every level), strings at most 256 bytes) with schema `cairn.client-diagnostics/1`:

| Field | Meaning |
| --- | --- |
| `surface` | `cli`, `mcp`, `python-hook`, `other` or `unknown`; anything else is `unknown`. |
| `harness` | `codex`, `claude`, `opencode`, `hermes`, `agy`, `other` or `unknown`. Reported by explicit configuration only (`cairn mcp --client-harness`); never inferred from task IDs, arguments, `--codex-thread` or a process list. The CLI always reports `unknown`. |
| `transport_build` | The existing `cairn.build/1` of the *executing Go process*, read once when the client is built. A caller cannot supply it. A running MCP process keeps reporting the build it started with, whatever is installed at its path later. |
| `origin` | Optional. A fixed component name (currently `lifecycle-memory`), an optional bounded `implementation_id`, and `basis: "reported"`. It is present only when the calling code supplies it through the dedicated `CAIRN_CALLER_DIAGNOSTICS` value, which the CLI validates against that fixed list and otherwise drops. The CLI's own build is never presented as the caller's identity. Only the Python lifecycle memory module sets it, for its own CLI calls ([below](#python-lifecycle-memory-origin)); every other caller leaves the origin `unknown`. |
| `retrieval_capabilities` | The existing `cairn.retrieval-capabilities/1` search declaration (`search_memory_budget_bytes`, `search_min_pull_bytes`), reused unchanged. It declares implemented request semantics, not authorization, health or a guarantee that the next request succeeds. |

A declaration is a **reported claim, never identity**. It selects no principal,
machine, session, destination or operation, and never changes whether a request
is admitted or how it executes. It is a header, never part of a request body, so
request bytes, retry behavior and mutation identity (`request_id`) are unchanged.
The client never retries because of it. A declaration that cannot be built within
bounds is omitted and the call proceeds.

## Python lifecycle memory origin

A mixed installation can run the Go CLI from one build while hooks run Python from
another. The lifecycle memory module (`integrations/lifecycle/memory.py`, shared by
the Claude Code, Codex, OpenCode and Hermes hooks) therefore reports its own origin
for the CLI calls it makes, so a cohort shows *which memory source artifact* asked,
separately from the executing Go CLI's `transport_build` (`surface` stays `cli` and
`harness` stays `unknown`; the producer adds only `origin`).

```json
{"component":"lifecycle-memory","implementation_id":"<64 lowercase hex>","basis":"reported"}
```

- **Coverage.** Every ordinary `Memory.call` operation (search, pull, history,
  create, revise and so on) and the best-effort `recall-observation` report, which
  includes both when the bound [inbox recall](inbox-recall.md) engine makes them.
  The coordination and watch code, selector and provider subprocesses, the MCP
  facade and other Go clients are not relabeled.
- **What the ID is.** The full lowercase SHA-256 of the module's own `.py` source
  bytes. Two different readable artifacts report different IDs; identical bytes at
  another path report the same ID. It is never the CLI revision, a Git checkout,
  telemetry method or a configured or inherited value. The path, source text,
  prompts, queries, records, tokens, session or repository IDs and exceptions never
  enter the declaration (at most 512 bytes; the ID is exactly 64 characters).
- **A source snapshot, not loaded-code attestation.** The module observes its
  source once when it is initialized, which is one execution or import of the file
  (an import, a script run or the bound inbox engine's pinned-bytes execution).
  Python may already have compiled code before that observation, so the ID names
  what the file contained then, not what the interpreter is running. Every `Memory`
  object from that module and both outbound paths share the one result; replacing
  the file afterwards does not change it, and a freshly initialized module (the
  next hook invocation, a restarted host) may observe a new artifact. A host that
  keeps the module loaded keeps its snapshot until it is initialized again. Nothing
  is hashed per call.
- **Bound inbox engine.** The bridge already read and SHA-256-pinned the exact
  bytes it executes, so it hands those bytes to the engine instead of letting a
  second read observe a different file. That engine's ID therefore equals its
  `engine.sha256` pin in the binding manifest.
- **Unknown stays unknown.** A missing, unreadable, larger-than-1 MiB, nonregular
  (it is refused without opening it for input, so a FIFO cannot make a hook wait)
  or bytecode-only source, or one whose identity, size or modification time changes
  while it is observed, leaves `implementation_id` absent; the origin still reports
  only `lifecycle-memory` and `reported`. A failed declaration is simply omitted.
  None of this changes the operation, its arguments, request UUIDs, retries,
  credits, deadlines, hook output or recall telemetry.
- **Environment.** The child gets a copy of the environment whose
  `CAIRN_CALLER_DIAGNOSTICS` is replaced by the module's own value, or removed when
  none can be built, so an inherited value cannot impersonate the producer. The
  parent environment is not modified.
- **Limits.** Ordinary file checks (type, size, identity and modification time
  compared around one read) do not prove an atomic snapshot against every writer.
  An older module reports nothing, and an older bridge leaves the engine to
  observe its own file path. A hostile Python caller can still declare anything the
  allowlist accepts, which is why the origin is a claim.

### Compare a reported ID with an expected artifact

Hash the artifact you expect, such as the source checkout's file or the installed
copy (installers copy it byte for byte), then list the cohorts after some
authorized lifecycle traffic. This uses only `sha256sum` and `jq`:

```sh
expected=$(sha256sum integrations/lifecycle/memory.py | cut -d' ' -f1)
cairn clients --limit 128 | jq --arg id "$expected" '
  .data.rows[]
  | select(.reported.origin.component == "lifecycle-memory")
  | {last_observed_at, implementation_id: .reported.origin.implementation_id,
     matches_expected: (.reported.origin.implementation_id == $id)}'
```

`matches_expected: true` means a retained cohort reported that source snapshot.
`false` with an `implementation_id` means another artifact reported; `false` with
`null` means the source was unobservable (or the caller predates the producer), not
a mismatch. Rows are volatile cohorts within one API process and 24 hours, silent
clients are not enumerated, and an absent row never shows that a hook is current.
The comparison orders nothing and says nothing about age or staleness; restarting a
host or reinstalling a hook remains a separately chosen action.

## What the server keeps

After normal authentication (and the existing remote-profile and method checks),
and before protocol admission so that a refused request still diagnoses its
client, the API records one in-memory observation for authenticated POST calls
other than `/v1/clients`. It reads no request body, writes no SQL and no log, and
does not change business admission, retry behavior or identity, and cannot fail or
roll back an operation. Bounded synchronous parsing and bookkeeping add overhead.

- **Cohorts, not processes.** An observation joins the cohort keyed by the
  authenticated principal, the server-provisioned machine ID (if any) and a digest
  of the *validated* declaration. Repeated CLI calls or several MCP processes with
  an identical declaration share one cohort. A shared profile cannot be split into
  sessions or people, and no field counts processes.
- **Missing, invalid, unrecognized.** No header is `missing`; several headers, an
  oversized, malformed or non-conforming value is `invalid` and counted; a newer
  schema (`cairn.client-diagnostics/N`) is `unrecognized` and never decoded as v1.
  Each gets one coarse cohort per principal and machine. None of them means an old
  client. Unknown members of a valid declaration are ignored, never retained or
  echoed. Raw bytes of an unusable declaration are never kept.
- **Retention.** Volatile, per server process: a cohort is forgotten 24 hours after
  its last observation (the monotonic reading decides when the clock provides one),
  at most 128 cohorts per principal and 4,096 in all (32 configured identities × 128). A principal that fills its quota evicts its own least recently observed
  cohort, deterministically. The global bound accommodates every configured
  principal’s full quota; traffic from another configured principal cannot evict
  these rows. A restart starts empty with a new
  `observation_epoch`; first and last times are observations within this process
  and window, not launch or exit times.
- **Scoped counters.** `counters.evicted_cohorts` and `invalid_declarations` count
  only your principal's rows; `partial` is true when your view is truncated or
  your rows were evicted. A capacity eviction reflects your own principal's quota. Counters never reveal
  another principal's traffic.

## What `clients` returns

`cairn.clients/1`: `storage: "volatile"`, `observation_epoch`, `started_at`,
`observed_at`, `retention_seconds`, the API's `server` build, `exhaustive`
(always `false`: the view can never enumerate silent, stripped or older
reporters), `partial` (known gaps: truncation or evictions), `truncated`,
`wall_clock_regressed`, `returned`, `eligible_rows`, `counters` and
`rows`, newest observation first. A row has an opaque `cohort_id`, your
`principal` and `machine_id`, `metadata_state`, `origin_state` (`reported` or
`unknown`), `first_observed_at`, `last_observed_at` and the validated `reported`
descriptor. Safe fields only: no header, token, path, prompt, session ID or
request content is ever included.

- **Own scope only.** The caller's principal and machine come from its
  authenticated profile. There is no `all`, principal, machine or cursor field
  (unknown fields are refused). A remote machine's agent profile sees its own rows;
  a remote observer profile stays denied as before.
- **One bounded page.** At most 128 rows (request `--limit 128` to read every retained cohort); `truncated` and `eligible_rows` say what
  was left out. There is no cursor and no inventory promise.
- **Listing observes nothing.** `/v1/clients` is not itself recorded, and listing
  never refreshes a cohort. `/v1/version` calls do count as contact.
- **An empty list** means no retained observation in your scope, not that every
  client is current. An old API without the route is reported by the CLI as
  `UNSUPPORTED_DIAGNOSTICS`. Remote profiles can instead receive
  `AUTHORITY_DENIED` from an older API: the CLI preserves that code and explains
  that access is denied or the API predates the view, with observations unknown.
  An unreachable API stays `API_CONNECTION_FAILED`.
- **No ordering claims.** Rows are ordered by observation order, never by wall
  time or Git history. `wall_clock_regressed` labels a clock that moved backwards;
  such times are displayed as observed and never used to order, expire or compare
  releases. Equal revisions do not prove equal bytes or capabilities, and
  different revisions are not older or newer.

The CLI validates the versioned response before printing a typed projection.
Unrecognized schemas, unsafe known fields, inconsistent counters and out-of-bound
pages return `INVALID_DIAGNOSTICS` without reflecting peer content. Unknown
additive fields are omitted. This includes checking build/origin/capability
formats, cohort/epoch UUIDs, timestamps, known metadata states, machine IDs, and
nonblank UTF-8 principal labels of at most 256 bytes, without Unicode control
characters. Safe provisioned labels retain their exact spelling, including spaces
and Unicode. Invalid UTF-8 or unpaired JSON surrogate escapes are refused before
decoding. These display checks do not change identity provisioning. Responses are capped
at 512 KiB and the requested row limit. The declared principal remains a server
claim; response validation does not independently authenticate it.

## Wire behavior

A server that predates this ignores the header. A legacy relay drops it, so its
clients appear as `missing`. The current relay forwards at most one value of at
most 4096 bytes verbatim and drops anything else without refusing the request; it
never adds its own identity to the declaration. The route and header are additive
under the [compatibility rules](api-compatibility.md): the wire protocol range is
unchanged. `/v1/clients` is a read with no durable effect (safe to repeat) and
is allowed to remote agent profiles for their own principal only.

Build strings from a client are checked against the same closed, bounded formats
`cairn_client_info` applies to the API's build (Go version, module version, VCS
kind, hex or numeric revision, RFC 3339 time, at most 128 bytes): paths, markup
and unsupported punctuation are refused. Those formats still allow a short alphanumeric suffix
after the Go version (for development toolchains and experiments), so a hostile
client under your own principal could display a few words there. Treat every
reported field as a claim, not as text to follow.

## Limits

This slice detects declared cohorts and their reported features. It does **not**:

- enumerate every installed client, identify silent sessions, or survive an API
  restart (no first/last seen across restarts);
- identify the exact bytes of a running executable or of code a Python
  interpreter has loaded: the Go CLI's own bytes are not hashed, and the Python
  lifecycle memory origin is a reported source snapshot taken at initialization;
- compare against a release target, order revisions, or say a client is behind;
- cover other Go clients such as `cairn watch` or the wake supervisor, which send
  no declaration and appear as `missing`; or
- change any client or restart anything.

Persistent history across restarts, a target-manifest comparison, running-binary
hashing, origin producers for other callers and a wider capability catalogue are
deferred and need their own decisions (the persistent form needs PostgreSQL telemetry,
authorization, expiry and backup semantics).

## Release checklist

Record the intended targets and features. Inspect the CLI (`cairn version`) and
the API (`cairn agent ... version`) separately; inspect the active MCP session
with `cairn_client_info`; inspect each installed Python entry point independently
(`sha256sum` of its `memory.py`, then [compare it with the reported
ID](#compare-a-reported-id-with-an-expected-artifact)); then, after representative
authorized traffic, list `cairn clients` and record missing reporters, an empty window, relay stripping, truncation and eviction
counters, and a changed `observation_epoch` after any restart. Do not certify
"all clients updated" because no mismatch was observed. Any restart or upgrade is
a separately chosen owner action.
