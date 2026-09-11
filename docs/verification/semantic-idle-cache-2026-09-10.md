# Semantic cache across idle worker release

The streaming API now retains one bounded passage-vector snapshot in memory when
its idle worker exits. A fresh worker restores matching-model vectors and scores
the current eligible notes. In the disposable real-model API check, the cold
request took 11.838 seconds and the request after an observed child exit took
0.605 seconds, with the same complete score digest and source identities.

This implements the [preceding experiment](semantic-idle-snapshot-2026-09-10.md).
It reduces repeated inference after idle release; initial cold scoring and new
passages still require inference. It does not establish downstream task benefit.

## Ownership and compatibility

`semantic/stream.go` owns an opaque JSON snapshot, capped at 600 KiB. It transfers
that state only to the first request in a replacement child after idle release.
Successful replies replace it; omission clears it. Failed or cancelled exchanges
and owner shutdown discard it. Busy or cancelled requests that never reach the
owner, and searches with no optional candidates, do not change retained state.
Snapshots can therefore outlive the child until the next exchange or API shutdown.
They are not written to disk, receipts, model responses or API logs. Releasing
references does not promise secure allocator erasure.

The host advertises `CAIRN_SEMANTIC_STREAM_CACHE=1` in the stripped streaming-child
environment. An older worker ignores it and retains its original protocol. An
older API does not advertise it, so the new prepared worker emits its original
`id`/`result` response. One-shot mode is unchanged. No agent tool or command flag
selects the cache. Updating both API and worker, then restarting the API, adopts it.

Only the optional `cache` field extends the worker envelope. Ordinary score
responses remain separately bounded at 64 KiB; the cache limit is 600 KiB and
complete reply frames including newline are bounded at 664 KiB. Requests retain
the 8 MiB cap. Wrong IDs, unknown fields, changed model identity within a child,
invalid frames and excessive state/result sizes still refuse and discard the child.

The bundled worker owns `cairn.passage-vectors/1`: a model fingerprint and at most
128 passage hashes with copied raw float32 vectors. It stores no query/note text
or record IDs. Restore validates the complete snapshot before publishing any
vectors. A different model fingerprint computes cold results. Later restore
attempts within a running child refuse. Each successful score still drops absent
passages, tokenizes every supplied body and reconstructs the original numerical
path. Scope, sensitivity and currentness checks still precede scoring; a snapshot
cannot add a candidate or make an old handle current.

## Verification

The first Go test failed against the old transport, then passed after actual idle
child exit and restoration into a different process. Tests also cover state
replacement, omission, failed exchanges, cancellation, owner isolation, oversized
state/result/frame refusal and the unchanged legacy-worker cases. The first
numerical stream test likewise failed before implementation. Eighteen numerical
and framing tests pass, including full 128-vector transfer, copied ownership,
invalid/nonfinite snapshots, unchanged/changed/removed notes and model mismatch.

The full disposable PostgreSQL/race integration passed with the actual prepared
CPU model and candidate streaming worker. A separate fixture API used a 200 ms
idle interval to verify child exit without changing the operational five-minute
setting. Its fresh child returned the identical score digest and ordered source
identities in 0.605 seconds. After another idle exit, an edited source was scored
and pulled at version 2; its old handle was refused. A local-only fixture note
stayed excluded from hosted output. Snapshot fields were absent from API responses.
Existing real-model checks also verified warm retirement and fallback.

The full authenticated model check was then run using a separate API process from installed CLI
`208c1c8` with the new worker. It passed without cache support: the after-idle request
took 11.859 seconds versus 12.745 seconds cold. This checks compatibility, not an
additional paired performance comparison; the documentation input had changed
between the two check invocations. Legacy workers also pass the new transport's
existing process tests. Static checks and all 48 Python tests passed.

The API timings are individual requests on a shared host using two public
documents. They are not service percentiles. Actual source hashes and both response
sets are retained; the new transport check used the documents at `3db42c8`, before
this turn updated the semantic guide. The earlier experiment remains the fixed
paired comparison. Model files, packages, numerical settings and worker deadline
remain unchanged. No native OpenCode answering-model run was added.

The [manifest](semantic-idle-cache-2026-09-10.json) records checks and source hashes.
Scratch evidence is under `/tmp/cairn-semantic-idle-cache-20260910`; it is not a
durable archive. Installation is recorded in a later checkpoint after CI.


Pincite packet `pkt-c43c53a89e17325a` supports the ownership and preservation
choices. The decision receipt validates and consumption is closed. Seven
nonmaterial obligations concern unchanged Go interfaces and broader performance
representativeness; they do not establish general latency or task-value claims.
