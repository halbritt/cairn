# Frozen comparison of a semantic worker package

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
`rank-inputs.jsonl`, `stream-inputs.jsonl`, `embed-inputs.jsonl`, and the
`stream-trace.*` files from `strace -ff -xx -s 8388608 --trace-fds=0,1`.
The embed file contains 37 standard requests followed by 19 rank-query requests.
The reporter selects the single complete cache-bearing worker pipe and binds
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
plus four owner-approved rank-backed frames/130 scores. The reporter verifies
the captured stream/rank reference equivalence and never calls those four
frames historical captures.

Rule C uses a score shift at most 500 and vector cosine gap at most `1e-6`.
Identical scores, shifts within one and bit-exact vectors are diagnostics.
Source metadata, UTF-8 spans, projections and token counts remain exact. Rank
changes retain their reference/candidate margins; without a frozen near-tie
classifier they require owner interpretation. The 294 prior token/tensor
construction bindings are reused, not newly observed wire tensors. Local
comparison results do not close S-01, fleet, runtime or production acceptance.
