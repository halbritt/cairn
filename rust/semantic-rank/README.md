# Semantic rank/stream source core

This library joins the published semantic protocol, cache codec and score
reducer with the request engine and bounded byte I/O used by rank-once and
rank-stream. It is the S-RANK-1 source cut for CAIRN-325 under CAIRN-301.

`run_rank_once(&mut Read, &mut Write, factory)` reads to EOF or 8 MiB + 1,
validates the selected bytes through hashes, constructs one owning session,
scores and writes/flushes the reply. `run_rank_stream(&mut BufRead, &mut Write,
factory, CachePolicy)` stops each read at LF, the 8 MiB + 2 cap or EOF. It
validates before construction and, for an existing session, rejects any cache
key after hash admission. Only the first valid advertised-cache request may
restore. Each complete reply is written and flushed before another read.

The engine owns its backend session and copied raw passage cache. It keeps
query encoding before body work and completes each yielded chunk's budget,
reencode and hash checks before decoding another chunk or encoding a later
body. It counts occurrences, builds unique misses in first occurrence order,
and reconstructs query plus every occurrence's raw row before normalization.
Late metadata lookup and result construction precede cache replacement. The
ordinary stream reply's size/conversion check precedes snapshot creation.

`BackendFactory::construct` returns an owning `BackendSession` before the core
reads its identity or calls its tokenizer/model. A future native factory must
verify the complete consumed manifest/assets, loaded runtime and tokenizer and
numeric settings. There is no production backend, executable, substitute
tokenizer, numerical implementation, readiness flag or fallback in this cut.
The separate norm, zero check, division and matrix-vector operations preserve
Source sequencing; implementing the NumPy/OpenBLAS equivalent remains a native
backend obligation. Raw model rows are explicitly float32 bits. The core adds
no new finiteness refusal after arithmetic and reuses the published reducer.

Every failure terminates that invocation. `InvocationError` retains the selected
original input, input mode, typed failure or pending decode/render profile and
number of bytes successfully written. An unresolved codec depth boundary is
pending, never success or an invented Source exception. Partial write/flush
failures differ from serialization failures. The next iteration cannot retain
the core's prior request/query/passages/response; the engine's only request
derived persistent state is copied current-success passage hashes/raw vectors.
This is an ownership property, not an allocator memory-erasure claim. Backend
implementations must also avoid retaining request text through their handles.

The `cfg(test)` scripted backend supplies literal tokenizer steps, raw bits and
arithmetic outcomes. Component tests check control flow, row ordering, failure
boundaries, cache ownership, exact JSON and interactive flush/read order without
processes, Python, native libraries, assets or model execution. They do not
establish tokenizer, numeric, model, native worker, supervisor or runtime parity.
The author performed source/format inspection only; the separately admitted B2
component check and R1 exact candidate review supply their own evidence.

The lockfile copies the unchanged twelve registry entries from the published
protocol lock, adding only the local rank and score packages. Rank depends on
local protocol/score (text through protocol) and the already admitted
`sha2 = 0.10.9`, no defaults, `force-soft`. The fixed x86_64 inactive-libc
exception is unchanged. No Cargo resolution, fetch or materialization belongs
to author/reviewer preparation; B2 binds its own isolated execution conditions.

Source references are `scripts/semantic_rank.py`: chunks 22–30, construction
46–72, score 74–124, snapshot/restore 127–168, stream 171–211 and rank-once
226–232. Each test names its accepted R1–R7 or D1–D3 criterion. Existing protocol,
text and score acceptance is reused. Real tokenizers 0.23.2 closure/reference
tokens, ORT 1.29 assets/runtime, NumPy 2.4.6/OpenBLAS operation and raw-bit parity,
Release containment/ABI and the thin executable/Go supervisor remain separate
prerequisites. Actual-worker FIRST remains unconsumed; S-RANK-1 provides no
S02/runtime acceptance.
