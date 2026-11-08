# Already-decoded semantic protocol

This library implements the selected `semantic/4` operations on supplied decoded
values. It depends only on `std` and the published local `cairn-semantic-text`
path crate. It has no executable, decoder, serializer, hash implementation,
cache codec, tokenizer or initialized backend.

`Value` distinguishes Null, Bool, canonical arbitrary signed limb integers,
binary64 FloatBits, code-point strings, arrays and insertion-ordered objects.
Integers never pass through `f64`; FloatBits retains NaN payloads, infinities and
signed zero. `PyString` accepts every code point through U+10FFFF, including
lone surrogates. Its length counts code points, without normalization or pairing.
Strict UTF-8 conversion/length fails at a surrogate only when explicitly used.
IDs are not UTF-8-encoded during validation. Object keys are unique decoded
strings; a duplicate-key constructor error is an IR invariant, not a raw-frame
duplicate policy. `get` distinguishes missing from present Null. Structural IR
equality is not Python numeric equality or the future hash comparison.

## Operations and remaining work

- `validate_rank_prefix` performs notes/query lookups before its short circuit:
  notes length 1..64, query blankness, strict query UTF-8 at most 4096 bytes.
  It then encodes every body in note order before checking the 1 MiB total.
  A later encoding/type failure therefore precedes aggregate overflow. Notes
  are not eagerly required to be arrays: a nonempty Object or String passes
  length and reaches the body-access failure. Extra rank/note keys remain intact.
  `RankPendingHashes` borrows the exact input/query/notes and owns ordered UTF-8
  bytes. Private fields and read-only accessors provide no completion/readiness
  method, hash-acceptance flag or fully validated request.
- The remaining hash stage is deliberately unimplemented. For each note in
  order: hash body_i, then look up body_sha256_i, then compare using Python `!=`.
  A missing expected value is a lookup failure; a non-equal Value, including a
  non-string, is a mismatch, not a new type refusal. The first failing note wins.
  Tokenization, occurrence completeness and scoring remain later obligations.
- `validate_stream_prefix` evaluates the iterable key set before ID lookup and
  the 1..64-code-point ID guard, then the rank prefix. `CachePolicy` selects only
  an advertised-key policy, never initialized state. Missing cache, present Null
  and other values are retained separately. Late restore, fresh-worker checks,
  cache format/size/model/vector checks remain unavailable after pending hashes.
  Array key names can pass set membership and then fail string-key lookup;
  arrays containing unhashable arrays/objects fail while building the key set.
- `validate_embed_prefix` requires an Object with exactly id/operation/text,
  then checks the 1..128-code-point string ID and string text before operation
  dispatch. Identity requires empty text, including refusal for whitespace-only
  text, and returns `NeedsInitializedIdentity`. Query/document apply blank and
  strict 4096/65536-byte guards and return `NeedsTokenizer`. Unknown operation
  values, including non-strings, fail after the common guards. Actual Source
  constructs its embedding backend before reading input; these decoded-prefix
  calculations neither perform that constructor nor grant worker admission.
- `project_unqualified_rank_echo` independently reads record_id, version and
  body_sha256 in note order, preserving each Value type. Projection failures
  are separate from prefix refusals and establish no whole-worker first failure.
  `UnqualifiedRankEcho` supplies metadata only, without model/score validation.
  A no-cache `StreamPendingHashes` can wrap `UncheckedLogicalResult` in an
  `UnqualifiedStreamEnvelope`; advertised-cache replies remain unavailable.
  These logical values have no serializer, process path or byte-bound proof.

Nonfinite version echoes are IR retention only. Every actual Source reply uses
`json.dumps(allow_nan=False)`, so NaN and both infinities would cause a later
serializer refusal. No successful embed identity is built from a supplied
digest. Query retreat, token-empty refusal, passage/span bounds, vector checks,
Go reply matching, initialized identity and all runtime/model/native/numeric
acceptance require separate implementations and evidence.

## Source and fixture binding

The accepted interface criteria have SHA-256
`b6fa6c3c9f9672497a62c047511e10bc89e129b0edf6fab197a35e8582b36f1a`,
including R1 additions R-1 through R-5 from
`fcf3548c-2409-483c-a407-b0300d97ffcf/v1`. Bound source at
`94cf66857e7c96602f9dc3bc4b3a1d0984091c1c`:

| Input | Git blob |
| --- | --- |
| `docs/plans/hybrid-runtime-contracts/semantic.md` | `a44b916cf7e48ed579c95f7b4d60fd1914788874` |
| `scripts/semantic_rank.py` | `3af5ba61ee38450bdb074b6ac97c37338059fd05` |
| `scripts/embedding_worker.py` | `b2d8c38784b2960c67c98ddc360185d754757d00` |
| `scripts/semantic_fixture_cases.py` | `b6ff4e33b4f9b9097dbf49a6abfcf1490a9271f2` |
| `semantic/testdata/executable-harness/reference-inventory.json` (8977 bytes) | `a60001871c2c183bf84e1121b2cc6d1849c3d401` |
| `rust/semantic-text/src/lib.rs` | `0f566623ef294cdc762d668f1d5846a17bdda824` |

The predicate is reused per scalar after surrogate detection, with one stack
UTF-8 buffer and no per-scalar allocation. Its old exhaustive predicate suite
is not duplicated here.

Tests reconstruct the literal `protocol-rank` input and selected embedding IDs:
`query-ascii`, `query-4096-bytes`, `query-token-empty`, `query-empty`,
`query-byte-overflow`, `document-ascii`, `document-maximum-cjk`, `document-empty`,
`document-byte-overflow`, `document-token-empty`, `blank-query-u001c`,
`blank-document-u0085`, `blank-query-u00a0`, `blank-document-u3000`.
`rank/64-short` selects its 64-count boundary only; test bodies there are explicit
source-derived representatives, not the full frozen retrieval workload.
`document-token-empty` reaches NeedsTokenizer here; its later tokenizer refusal
is not counted. All outcomes are prefix stops, never observed worker acceptance.

Other cases are source-derived SDP-IR/RANK/STREAM/ID/HASH-STOP/ECHO fixtures:
arbitrary versions, nonfinite bits, surrogates, Unicode/byte inclusivity,
type-at-use and missing/Null distinctions, error precedence and ordered unchecked
projections. They are not newly observed direct-input oracles. Raw duplicate-last
and first-position behavior, decoder digit limits, BOM/UTF-16/32 and surrogatepass
byte decoding are outside this library and are not emulated.

## Checks

From the repository root, use a disposable `CARGO_TARGET_DIR` outside the
checkout for the focused locked/offline build:

```sh
rustfmt --edition 2021 --check rust/semantic-protocol/src/lib.rs
cargo test --locked --offline --manifest-path rust/semantic-protocol/Cargo.toml
```

The local dependency is listed in the lockfile. No registry dependency, network
fetch, Python execution, observed oracle, model run or full-runtime qualification
is part of these pure unit fixtures. Internal refusal sites are diagnostics,
not wire names or promised traceback wording.
