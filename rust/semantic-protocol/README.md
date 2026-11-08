# Already-decoded semantic protocol

This library implements the selected `semantic/4` operations on supplied decoded
values. It uses the published local `cairn-semantic-text` path crate, pinned
`sha2` 0.10.9 and `base64` 0.22.1. It implements ordered body hashing, decoded
cache payload checks and a bounded-domain logical envelope-size counter.
It has no executable, decoder, JSON serializer, tokenizer or initialized backend.

`Value` distinguishes Null, Bool, canonical arbitrary signed limb integers,
binary64 FloatBits, code-point strings, arrays and insertion-ordered objects.
Integers never pass through `f64`; FloatBits retains NaN payloads, infinities and
signed zero. `PyString` accepts every code point through U+10FFFF, including
lone surrogates. Its length counts code points, without normalization or pairing.
Strict UTF-8 conversion/length fails at a surrogate only when explicitly used.
IDs are not UTF-8-encoded during validation. Object keys are unique decoded
strings; a duplicate-key constructor error is an IR invariant, not a raw-frame
duplicate policy. `get` distinguishes missing from present Null. Structural IR
equality is not Python numeric equality. Hash and model-identity comparisons
implement their selected Python inequality rules separately.

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
- `hash::admit_rank_hashes` consumes that prefix. For each note in order it
  computes SHA-256 of the retained body bytes, then looks up body_sha256_i,
  then compares the lowercase hex digest using the selected Python `!=` rule.
  A missing expected value is a lookup failure; a non-equal Value, including a
  non-string, is a mismatch, not a new type refusal. The first failing note wins.
  Repeated bodies are hashed separately. `validate_rank_hashes` calls the
  existing prefix once. The private-field `HashAdmittedRank` retains that prefix
  and reports `NeedsRankTokenizer`; tokenization, occurrence completeness and
  scoring remain later obligations. An empty body with its correct hash reaches
  this boundary without establishing token-empty acceptance.
- `validate_stream_prefix` evaluates the iterable key set before ID lookup and
  the 1..64-code-point ID guard, then the rank prefix. `CachePolicy` selects only
  an advertised-key policy, never initialized state. Missing cache, present Null
  and other values are retained separately. `hash::validate_stream_hashes` runs
  this prefix and the actual hashes before late-restore policy: supplied
  `StreamPhase::Active` refuses every present cache key, including Null, while
  absence is permitted. `Fresh` permits the later pure cache policy. Phase is
  an input, not proof of a real process/scorer. The actual fresh constructor
  before restore remains unavailable. `cache::restore_stream_cache` requires
  `HashAdmittedStream`, preserving hash/late-policy ordering before payload work.
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

## Pure cache payload policy

`cache::restore_payload` treats Null as no-op. Otherwise it checks Object/exact
format-model-vectors keys and `cairn.passage-vectors/1`, measures the envelope,
and enforces at most 614400 logical default-JSON bytes before comparing identity.
`SuppliedIdentity` holds a code-point string, with no digest-syntax or initialized
model claim. Any non-equal model Value, including a non-string or other-case
string, is ignored before vectors count/type/key/codec work. A bounded malformed
129-entry state can therefore be ignored for another model and refused for the
same model. Size/format failure and unresolved measurement precede that decision.

Same-model vectors must be an insertion-ordered Object with at most 128 entries.
Keys have exactly 64 lowercase ASCII hex code points. Values have exactly 2048
string code points, then pass ASCII/strict Base64, 1536-byte and 384 little-endian
float32-component checks. Every component must be finite, and at least one must
be nonzero; either sign of zero is zero here. Accepted bits, subnormals and order
are preserved. All entries go into temporary storage, followed by one cache
replacement. Failure, no-op, ignored state and pending measurement preserve the
old cache. This standalone payload calculation never replaces a constructor.

`SnapshotEntries` accepts a unique-key ordered collection of explicit float32
bits; a duplicate is an IR constructor error. `snapshot_payload` checks each
384-component finite vector and encodes its little-endian bits with canonical
standard Base64, then checks count and size after all vectors. A later bad vector
therefore precedes aggregate count/size overflow. Snapshot permits all-zero
vectors and arbitrary string keys, unlike restore. Its result is a decoded
cache Value, without a general encoder or reply path. A future adapter still
has to prove its real native dtype, shape and model provenance.

The Base64 wrapper uses the established standard-alphabet engine, canonical
padding and `allow_trailing_bits=true`. It strips only an all-`=` suffix after
nonempty complete data quartets; for example `TWFu=`/`TWFu====` decode to `Man`.
`Zh==` decodes to `f`. Leading or discontinuous padding, excess padding after a
partial quartet, incomplete input, whitespace, URL-safe symbols and non-ASCII
are refused. Empty input is a valid primitive decode, while vector length
checks still apply. These compatibility rules and fixtures are source-derived
from pinned CPython 3.11.15
[binascii.c](https://raw.githubusercontent.com/python/cpython/v3.11.15/Modules/binascii.c)
and the accepted criteria, without an executed Python oracle.

`measure_envelope` is a logical byte counter, **not a JSON serializer**. Its
complete supported domain is root containers at depth 0 with child containers
at depth 1, Null/Bool/code-point strings, canonical signed integers with at most
64-bit magnitude, both zero FloatBits and all nonfinite FloatBits. It uses exact
ensure_ascii escaping, default `, ` / `: ` separators, decimal/sign lengths and
`0.0`/`-0.0`/`NaN`/`Infinity`/`-Infinity` lengths. Every NaN, including negative
payload NaNs, costs three bytes. DEL, lone surrogates and supplementary points
retain the pinned escaping costs. These rules derive from CPython 3.11.15
[_json.c](https://raw.githubusercontent.com/python/cpython/v3.11.15/Modules/_json.c),
not a new worker reply contract; actual replies still use `allow_nan=False`.

Finite nonzero FloatBits, larger integers or deeper containers return
`CacheEnvelopeNeedsSourceMeasure` carrying the original state, before identity
or payload work. Domain eligibility is checked without an early bound shortcut:
an unsupported later value remains pending even after a supported field already
exceeds 614400. No caller-supplied byte count or acceptance flag can complete
that boundary. All correctly typed snapshots are within the supported domain.

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

SP-PROTOCOL-2 adds hash/cache source under resolved criteria SHA-256
`8d19b01fad9897feb865e2e9cbca4e6648c50ff54aff3752024df49ef89a6b12`,
including accepted R1 Q-1 through Q-6 from
`e2ac1c01-2f22-4379-a2cd-df03cb7a98d5/v1`. The Source blobs above remain unchanged;
cache policy binds `semantic_rank.py` snapshot:127-139, restore:141-167 and
serve_stream:170-206. New inline `sdp2_*` cases cover independent literal hashes,
codec/bit fixtures, precedence, atomic replacement and pending boundaries. The
existing 11 protocol1 tests remain intact. These are authored source-derived
fixtures, not observations of a protected Source worker.

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
rustfmt --edition 2021 --check rust/semantic-protocol/src/lib.rs rust/semantic-protocol/src/hash.rs rust/semantic-protocol/src/cache.rs
cargo test --locked --offline --target x86_64-unknown-linux-gnu --manifest-path rust/semantic-protocol/Cargo.toml
```

The selected manifest and lock are copied exactly from the accepted dependency
selection, without regeneration: SHA-256
`d1eeedf8b52925e3d3f2da1c986abcf57218b151332dcc17b96c97544753ee50` and
`581f62e1bdcfdc41b0d057b05ca13c51d7ac20a1c9cbb009c870049349c54114`.
The lock binds two local and eleven registry packages. `sha2` has default
features disabled and `force-soft` enabled. On the fixed x86_64 host/target,
cpufeatures remains active; generic-array and typenum have selected ordinary
Rust generation/version-check build scripts. version_check has no build script.

Root `61f86ab3-0d21-4ddc-971e-a3881a84035d/v1` permits only the already fetched,
checksum-bound libc 0.2.177 archive to remain **inactive** in lock/cache. libc
and its build script must not be active compilation/execution units. Active
libc/native-library/script expansion, target/feature/graph drift, refetch or a
new origin stops the affected operation. This exception appends to historical
BLOCKED_INTAKE; it does not relabel that earlier result. B2 owns the retained
intake Cargo home/receipts and the one offline locked package check, including
pre/post pins and its own cleanup. The author performs only installed rustfmt
and `git diff --check` before that FIRST check. No tested MSRV is claimed.

Package execution and independent candidate review are separate evidence from
source authoring. No Python execution, observed oracle, tokenizer/model run,
worker admission, runtime/ABI/Go-reference/numeric or production qualification
is established by these pure fixtures. Internal refusal sites are diagnostics,
not wire names or promised traceback wording.
