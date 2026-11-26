# Rust semantic workers

Cairn configures and supervises operator-prepared Rust semantic workers from the
Go server and Go library entry points. For packaging and staging offline Rust
worker bundles, see [offline Rust semantic package](semantic-package.md).

## Rust workers through Go

`cairn serve --semantic-rust-config /absolute/path/worker.json` selects an
operator-prepared Rust worker. With no flag, Rust is disabled. The flag is
mutually exclusive with `--semantic-command`, `--semantic-stream-command` and
`--embedding-command`; ambient environment variables do not select Rust.

The JSON file contains one object, at most 32768 bytes, with no unknown fields.
This example is a path template, not a supplied runtime installation:

```json
{
  "mode": "stream",
  "executable": "/prepared/bin/cairn-semantic-rank",
  "model_dir": "/prepared/model",
  "ort_runtime": "/prepared/runtime/libonnxruntime.so.1.29.0",
  "openblas": "/prepared/runtime/libscipy_openblas64_-32a4b2a6.so",
  "gfortran": "/prepared/runtime/libgfortran-040039e1-0352e75f.so.5.0.0",
  "quadmath": "/prepared/runtime/libquadmath-96973f99-934c22de.so.0.0.0",
  "source_root": "/prepared/source"
}
```

All seven selections must be absolute existing paths. `model_dir` and
`source_root` must be directories; the libraries must be regular files and
`executable` must be an executable regular file. The Rust worker still validates
asset contents and native library selection. Go does not infer missing paths,
download assets or treat path validation as runtime qualification. Invalid
configuration returns an error before the API opens its identity file or store.

Mode `rank` launches `cairn-semantic-rank` for each request. Mode `stream` adds
`--stream` and owns a reusable rank process; its default idle lifetime is 30
seconds, adjustable with `--semantic-idle-timeout`. Mode `embed` selects
`cairn-semantic-embed` with separate query and document processes and the existing
asynchronous index owner. Rank/query requests retain their 25-second deadline;
document exchanges retain 90 seconds. Earlier caller cancellation still wins.
Cold model initialization is inside those deadlines. Qualify the chosen native
binary and build profile through Go; valid paths alone do not establish that a
cold request completes in time.

Go supplies the six explicit `--model-dir`, `--ort-runtime`, `--openblas`,
`--gfortran`, `--quadmath` and `--source-root` arguments without a shell. Children
receive the existing fixed offline environment plus `ORT_DISABLE_TELEMETRY=1`.
The three `LD_LIBRARY_PATH`, `LD_PRELOAD` and `LD_AUDIT` variables and the
database DSN are absent. Streaming rank retains the existing cache opt-in,
bounded frames, idle restore, busy refusal and child cleanup. This environment
policy does not place production workers in an OS sandbox.

The library entry points are `semantic.RustCommand`,
`semantic.RustStreamCommand`, `semantic.RustEmbeddingCommand` and
`semantic.StartRustIndex`. Their legacy executable-only counterparts retain
their signatures. Rust paths and mode are operator configuration, never request
fields or an arbitrary argument/environment list.

## Protocol and real-worker Go checks

`TestStreamProtocolFrameLimits` in `semantic/worker_acceptance_test.go` compiles
`semantic/testdata/executable-harness/protocol-worker.c` and runs the 10
`stream_limit_cases` from `semantic/testdata/executable-harness/reference-inventory.json`
through `semantic.StreamCommand`. It checks the `600 KiB` cache limit
(`streamCacheLimit = 600 * 1024`), the `600 KiB + 65536` byte outer-frame limit
(`streamFrameLimit = streamCacheLimit + 65536`), compact `{id,result}` bounds
when cache is absent or null, typed zero values, Go HTML escaping, and UTF-8
encoding.

The opt-in `TestRustReal*` tests (`TestRustRealRankAndStreamIdleRestore` and
`TestRustRealEmbeddingHandshakeUTF8SpansAndLanes`) exercise prepared Rust
executables and model assets through `semantic.RustCommand`,
`semantic.RustStreamCommand`, and `semantic.RustEmbeddingCommand`. Set
`CAIRN_RUST_RANK_TEST_CONFIG` and `CAIRN_RUST_RANK_TEST_REQUESTS` for the
rank/stream idle-restore check, and `CAIRN_RUST_EMBED_TEST_CONFIG` for the
embedding check; any `CAIRN_REAL_EMBEDDING_BACKEND` value other than empty or
`rust` fails. Run real-worker checks inside `scripts/test_hybrid_runtime_operations.py`
with explicit read-only inputs and no production home. Ordinary `go test ./semantic`
runs skip these opt-in tests when the config selectors are unset; protocol
fixtures do not count as real Rust/model evidence.
