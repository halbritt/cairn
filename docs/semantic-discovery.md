# Optional semantic discovery

Use semantic discovery when the query and saved note use different vocabulary:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task investigation --run investigation-1 \
  --semantic 'Where does Cairn keep its data?'
```

Native MCP and OpenCode use the existing `cairn_search` tool with
`{"query":"Where does Cairn keep its data?","semantic":true}`. Conversation/session
scope remains supplied by the harness configuration. Read required `selected`
context and pull relevant sources with their returned `pull_arguments`.

Set `offset: 0` (CLI `--offset 0`) to request ranked pages, then follow
`page.next_offset` with the same query and settings. Scoring still covers the
complete eligible candidate set before paging. Every page has its own budget
and reads current state; check `discovery.state` each time and restart if fallback
changes the ordering. [Paging contract](index-and-pull.md#agent-commands-without-request-json).

This is optional discovery, not answer confidence. It can return unrelated
neighbours even when no note answers the question. Lexical search remains the
default. Semantic discovery requires a nonempty query, cannot be combined with
browsing and is available only for context index requests, not authority-bearing
placement, capability or security decisions.

## Local backend

The optional worker uses FastEmbed 0.8.0 and a local quantized
`BAAI/bge-small-en-v1.5` model. It does not require a model server or GPU. Prepare
its separate Python environment and downloaded model with:

```sh
bash scripts/install-semantic.sh
```

This requires `uv` and obtains Python 3.11 if necessary. The installer writes
under `${CAIRN_HOME:-$HOME/.local/share/cairn}/semantic`, records model-file hashes
and prints the worker command. It does not modify running services. Configure the
existing API process explicitly:

```sh
cairn serve --semantic-command "$HOME/.local/share/cairn/semantic/worker"
```

To reuse the loaded model between requests, select the alternative launcher:

```sh
cairn serve --semantic-stream-command "$HOME/.local/share/cairn/semantic/worker-stream"
```

Choose one command mode. The streaming worker starts on the first eligible query
and by default exits after 30 seconds without scoring work. Hosts with spaced
follow-up searches can retain the same loaded model and vector cache longer:

```sh
cairn serve --semantic-stream-command "$HOME/.local/share/cairn/semantic/worker-stream" \
  --semantic-idle-timeout 5m
```

`--semantic-idle-timeout` accepts a positive Go duration and requires streaming
mode. Zero and negative durations refuse; they do not mean unlimited retention.
The interval starts after a completed scoring request and resets after each
successful request. The request deadline remains 25 seconds, and cancellation,
failure and shutdown still discard the worker. Choose the lifetime for the host's
memory budget and query spacing: longer retention avoids re-embedding unchanged
notes but keeps the model and vectors resident between requests. It does not
remove the first cold score or guarantee that the next eligible set will hit the cache.
The library's `semantic.StreamCommand` retains the 30-second default;
`semantic.StreamCommandWithIdleTimeout` lets its host choose a positive interval.

The [spaced-follow-up check](verification/semantic-idle-2026-09-10.md) records this
host's five-minute choice and its observed cost reduction. The worker occupied
about 208 MiB in the original local experiment while loaded. The prepared worker
reuses vectors for exact decoded passages from its last successfully scored
request. An edit only requires new inference for passages whose text changes;
appending to a long note can preserve its earlier windows. An insertion or
deletion earlier in a note can shift later windows and invalidate them. Tokenization still
runs on every supplied body. Identical passages within one request also share
inference, while each occurrence keeps its original position and note identity
for scoring and counts toward the chunk limit.

Only passage hashes and raw vectors are retained; query text, query hashes, note
text and record IDs are not cached. Passages absent from the next successful
request are dropped. All current eligibility checks still run before the worker
receives candidates. A cached passage cannot introduce a candidate or authorize
delivery. The loaded model and tokenizer are fixed for that worker's lifetime.

The existing 64-note/128-chunk limits apply to cache hits too. At most 128 vectors
are retained (192 KiB of vector payload with the installed float32 model, plus map
overhead). A compatible API retains one serialized snapshot in its own memory
when an idle worker exits. A new child restores matching-model vectors before
scoring the current eligible set. This releases the loaded model while avoiding
repeated passage inference. Model initialization and new passages still cost work.
A changed model fingerprint discards the snapshot and computes cold results.

The transport caps the opaque snapshot at 600 KiB and keeps it out of scoring
results, receipts and logs. Only an idle exit preserves it: failed or cancelled
exchanges and API shutdown discard state. A successful reply replaces it; omission
clears it. Requests that never reach the transport do not alter its state, including
searches with no eligible optional notes. Snapshot lifetime can therefore extend
until the next scoring exchange or API shutdown. No file or database cache is
created, and releasing references is not secure allocator erasure.
The [passage-reuse comparison](verification/semantic-chunk-cache-2026-09-10.md)
records localized-edit timing and the remaining cold cost. The earlier
[whole-note cache comparison](verification/semantic-vector-cache-2026-09-09.md)
remains as historical evidence.
Existing custom one-shot workers continue to use `--semantic-command`.

Use the printed path if `CAIRN_HOME` is customized. A systemd installation must
include that argument in its `ExecStart`; restart the API after changing the
configuration. The worker uses the prepared model snapshot with offline loading.
Downloads happen during preparation, not retrieval. Reinstalling can update
transitive Python dependencies or upstream model files; the scoring fingerprint
records the actual files, dependency versions and algorithm settings used.
For streaming mode, that fingerprint describes the model loaded by the current
child. Restart the API after updating worker code, packages or model files for
immediate adoption; otherwise the next child starts after idle release or failure.

The host configures the executable. Agents cannot supply a command or model path.
The worker receives only the query and eligible optional note bodies/identities.
Its environment excludes API credentials and database settings. This is trusted
local host code, not a filesystem sandbox.

## Selection, limits and fallback

Structured repository/task/run scope, destination, currentness and eligibility
checks run before scoring. Mandatory instructions are selected independently and
are not sent to the model. The core validates every returned record/version/body
hash and requires exactly the supplied candidate set. Semantic scores affect
optional ordering only; scope specificity, recency and stable ID break score ties.
The normal context/index budget, duplicate handling, handle credits and pull-byte
limits still apply. Pulls recheck current eligibility and refuse stale handles.

The worker scores overlapping windows of 384 model tokens at a stride of 320,
using each note's highest query/window cosine similarity. Each query or passage
uses a single-input inference batch, avoiding padding short passages to their
longer neighbours. Batch size is included in the scoring fingerprint. It includes the entire
tokenized note instead of truncating to the first window. Scores are rounded to
millionths and are not probabilities. There is no calibrated answer threshold.

Each invocation is bounded to 64 optional notes, one MiB of note bodies and 128
windows. Queries and decoded windows must fit the model's 512-token input. The
worker uses two CPU threads. One invocation runs at a time per API process;
another concurrent semantic request falls back immediately. Workers have a
25-second deadline, leaving five seconds before the native OpenCode client's
30-second timeout and ten before the API client's 35-second timeout, and a 64 KiB
output limit. Cancellation terminates the worker process group.
Streaming mode has the same per-request deadline, busy refusal and output cap.
The API closes pipes and reaps the group on cancellation, malformed responses or
shutdown; a later request can start a fresh worker. It does not retry a failed
request automatically. Idle release bounds process retention, without promising
erasure of Python/native allocator memory or previously delivered context.

The optional streaming protocol is one UTF-8 JSON line per request and response:
`{"id":"1","request":{"query":"...","notes":[...]}}` and
`{"id":"1","result":{"model_sha256":"...","algorithm":"...","scores":[...]}}`.
IDs belong to the API's transport, not agent-supplied task identity. Requests are
bounded to 8 MiB before the newline. Ordinary responses retain their 64 KiB
allowance. A compatible host sets `CAIRN_SEMANTIC_STREAM_CACHE=1` only in the
streaming child's stripped environment. The prepared worker then adds optional
`cache` state to replies; the host passes it on the first request to a replacement
child after idle release. The worker owns the snapshot format and validates its
model fingerprint, 128-entry bound and copied float32 vectors. Later restore
attempts within the same child refuse.

The cache has a separate 600 KiB limit; the entire reply including newline is
bounded to 664 KiB. Cache space cannot enlarge the ordinary scoring result limit.
The transport treats cache content as opaque, trusted worker state. An older
worker emits no cache and receives none; an older API does not advertise support,
so the new prepared worker preserves its original reply shape. No new agent flag
or tool argument is required. Upgrade the API and prepared worker, then restart
the API to adopt both; installing either alone preserves ordinary scoring.

Other unknown response fields, wrong IDs, missing results, partial lines and changed
model/algorithm identity within a child refuse the request and discard the child
and its saved state.
The core independently validates the supplied candidate set and scores as before.

Unconfigured, busy, failed or over-limit backends return lexical results with
`status: "DEGRADED_NO_EMBEDDINGS"` and `discovery.state: "unavailable"`. A decoded
but invalid score set is rejected in full and recorded as `invalid_result`, also
with labelled lexical fallback. Empty eligible optional sets need no model and
record `not_needed`. Successful semantic discovery records `ready`. No partial
model ordering is used after a failed validation.

Unfiltered index requests, including semantic discovery, use `cairn.semantic/8`;
requests with kind filters use `cairn.semantic/9`. Both carry
[preview source positions](index-and-pull.md#index-and-expansion-contract).
Semantic requests predating preview positions retain `cairn.semantic/7`. Successful scoring uses
`semantic-scope-recency/1` for unquoted queries; fallback retains lexical v4.
[Quoted queries](quoted-search.md) use semantic v2 or lexical v5, preferring exact
text matches before the corresponding score. The sealed package pins
the model fingerprint, scoring algorithm and digest of all optional candidate
scores. Protected candidate facts retain those scores. Historical recompilation
uses the frozen values and source versions, never the current model. A changed
score is an integrity failure. Legacy receipts preserve their original schemas
and ranking behavior. A transport retry whose freshly compiled package differs
returns the existing `STALE_PACKAGE` refusal; use a new request ID to request
current results after source or backend changes.

## Evidence and limits

[The offline comparison](verification/semantic-retrieval-2026-09-09.md) motivated
this route. [The integrated check](verification/semantic-discovery-2026-09-09.md)
exercises actual CPU scoring, API/CLI budgets, full-body pulls, long notes,
fallback and historical compatibility. These checks establish usable retrieval;
independent downstream task benefit and performance at larger scale remain open.
A [batching comparison](verification/semantic-batching-2026-09-09.md) records
latency, CPU time, peak resident memory and exact-score comparisons for the local
worker. It does not establish performance on every workload.
A [small-set startup experiment](verification/semantic-startup-2026-09-09.md)
found a roughly half-second saving when a scratch worker reused its model.
The [resident-worker implementation](verification/semantic-residency-2026-09-09.md)
preserves that benefit through the API and verifies process lifetime, current
source selection and fallback. It does not establish downstream task value.

After preparation, run the optional real-worker check against a disposable store:

```sh
CAIRN_SEMANTIC_WORKER="$HOME/.local/share/cairn/semantic/worker" \
CAIRN_SEMANTIC_REPORT=/tmp/cairn-semantic-check-new \
make test-integration
```

Choose a new report directory. The check creates only its own test API, runs the
public development corpus and a synthetic long note, and then restarts that test
API without scoring to check fallback. It does not modify the supplied worker or
running operational services. Without this option, normal integration checks use
controlled scorers and require no embedding model.

For the reusable worker, set `CAIRN_SEMANTIC_STREAM_WORKER` and
`CAIRN_SEMANTIC_STREAM_REPORT` instead. Add `CAIRN_SEMANTIC_BASELINE_WORKER` pointing
to a retained one-shot launcher to run six interleaved API comparisons against
the same disposable store. The stream check also verifies note edits, retirement,
local-only exclusions and shutdown. This optional check uses the installed local
CPU environment; regular Go process and Python framing tests need no model.
