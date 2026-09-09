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

Use the printed path if `CAIRN_HOME` is customized. A systemd installation must
include that argument in its `ExecStart`; restart the API after changing the
configuration. The worker uses the prepared model snapshot with offline loading.
Downloads happen during preparation, not retrieval. Reinstalling can update
transitive Python dependencies or upstream model files; the scoring fingerprint
records the actual files, dependency versions and algorithm settings used.

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
20-second deadline, within the API's existing request deadline, and a 64 KiB
output limit. Cancellation terminates the worker process group.

Unconfigured, busy, failed or over-limit backends return lexical results with
`status: "DEGRADED_NO_EMBEDDINGS"` and `discovery.state: "unavailable"`. A decoded
but invalid score set is rejected in full and recorded as `invalid_result`, also
with labelled lexical fallback. Empty eligible optional sets need no model and
record `not_needed`. Successful semantic discovery records `ready`. No partial
model ordering is used after a failed validation.

New index requests, including semantic discovery, use `cairn.semantic/8` with
[preview source positions](index-and-pull.md#index-and-expansion-contract).
Historical semantic requests retain `cairn.semantic/7`. Successful scoring uses
`semantic-scope-recency/1`; fallback retains lexical v4. The sealed package pins
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
