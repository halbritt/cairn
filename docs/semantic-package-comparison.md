# Frozen comparison of a semantic worker package

## HC-01 acceptance on 2026-11-12

Under the owner-directed reset `cb71136b-cd84-4fc2-b84f-eb247b975548/v1`,
CAIRN-329 accepts the implemented worker and tested
`x86_64-unknown-linux-gnu` package. The reviewed comparison landed at
`c991d141` (review `cbd58d7d/v1`, documentation condition fulfilled): all
807 rank/stream scores were exact, all 9,414 pair relationships unchanged,
and all 821 distinct paired vectors bit exact with maximum cosine gap
`4.440892098500626e-16`. These stronger observed results do not restore
bit-exactness as a new required tolerance.

The existing retrieval-quality fixture also passed on a disposable PostgreSQL
database on 2026-11-12. Its 68 lexical query/order/mode observations include
55 with an authored relevant result and 13 without one. The
[reference evidence](semantic-reference-acceptance.md#campaign-closure-and-limits)
records the report digest and limits. This is the reset's selected fixture,
not a new absolute relevance benchmark or a repeat of model inference.

The retained Rust package and Python rollback qualifications are recorded in
[package qualification](semantic-package.md#qualification-decision-on-2026-11-12)
and [Python closure acceptance](semantic-python-closure.md#accepted-campaign-target).
S-01's two maximum-byte observation gaps remain documented limits. Installed
service selection, destination ABI verification, cutover and HC-02 remain
Release work under the owner's R-07 production grant.

`scripts/check_semantic_package_comparison.py` reports a retained package's
actual Go-supervised outputs against frozen Python outputs. It launches no
model and creates no replacement Python reference. Run the reporter and its
model-free tests inside the existing semantic isolation wrapper.

The capture driver is
`scripts/testdata/semantic-package-comparison-driver/main.go`. It calls the
existing Go `Command`, `StreamCommandWithIdleTimeout` and `EmbeddingCommand`
owners with fixed, zero-argument package launchers. Their worker deadlines,
stream idle lifetime and protocol checks remain in force. Case tags in the
driver output are separate from the wire IDs generated and checked by Go.

Before capture, bind the package archive, manifest, launcher/worker/native
bytes, inputs and frozen reference hashes. Restore launcher absolute paths
inside the disposable namespace by copying package data without changing its
bytes. Do not rebuild workers, rewrite launchers or substitute ambient thread
settings. Record returned identities and actual worker environments and sealed
native mappings; the existing package fixture's `process_snapshot` observer
understands the sealed memfds.

Required capture files are `rank.stdout`, `stream.stdout`, `embed.stdout`,
`rank-inputs.jsonl`, `stream-inputs.jsonl`, `embed-inputs.jsonl`,
`actual-workers.json` from that observer, and the
`stream-trace.*` files from `strace -ff -xx -s 8388608 --trace-fds=0,1`.
The embed file contains 37 standard requests followed by 19 rank-query requests.
The reporter selects the observed stream worker's complete cache-bearing pipe
and binds
its requests, replies and IDs to the Go inputs and outputs. Partial Go thread
traces cannot stand in for that pipe.

Supply `--capture`, `--reference` (the frozen Go-comparison analysis inputs),
`--rank-metadata` (the frozen token IDs and prior-unpaired ordinals),
`--rank-inputs`, `--rank-arrays`, `--stream-manifest`, `--package-manifest` and
a new `--output` directory. Reports and raw outputs stay outside Git.

The retained corpus has 19 rank requests/269 scores/3,138 pair relationships,
38 stream frames/538 scores, 549 standard embedding vectors and 294 raw rank
rows. The 22 overlapping first-passage references give a union of 821 reference
pairs. Both complete vector groups are checked independently before that union
is summarized. Stream selection remains 34 captured Python frames/408 scores
plus four rank-backed frames/130 scores selected by coordinator decision
`d8b70f55-48fb-4c27-bfc5-f8405c56fe97/v1`. The reporter verifies
the captured stream/rank reference equivalence and never calls those four
frames historical captures.

Owner rule C requires per-vector cosine gap at most `1e-6`, score shift at
most 500, rank order preserved outside near-ties, and the retrieval-quality
fixture passing. This reporter does not evaluate the retrieval-quality clause.
The retained rank comparison records 17 labelled cases; all 15 relevant-note
rank intervals are unchanged. These are observations without an established
PASS contract.
Identical scores, shifts within one and bit-exact vectors are diagnostics.
Source metadata, UTF-8 spans, projections and token counts remain exact. Rank
changes retain their reference/candidate margins; without a frozen near-tie
classifier they require owner interpretation. The 294 prior token/tensor
construction bindings are reused, not newly observed wire tensors. Local
comparison results do not close S-01, fleet, runtime or production acceptance.
