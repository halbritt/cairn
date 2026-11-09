# Semantic protocol codecs and decoded stages

This pure library implements selected CPython 3.11.15 byte/JSON behavior over a
lossless IR, followed by separate decoded semantic/4 prefix, hash and cache
stages. It provides no executable worker, tokenizer, initialized model/scorer,
process transport or Go wrapper qualification. Codec success proves only the
selected framing, Unicode, JSON or rendering stage. Actual embedding Source
constructs its backend before reading input; this library does not perform or
prove that constructor.

## Lossless values and existing admission stages

`Value` distinguishes Null, Bool, arbitrary signed limb Integer, binary64
FloatBits, code-point String, Array and insertion-ordered Object. Integers never
narrow through floating point. Strings retain lone surrogates and code-point
length without normalization. Strict UTF-8 fails on a surrogate only when used;
IDs are not UTF-8 encoded by prefix validation. Structural equality compares
IR tags/bits, rather than Python numeric equality.

`PyObject::new` requires unique keys. Its borrowed-code-point BTreeMap preserves
the first observed duplicate indices while avoiding quadratic validation. Raw
JSON duplicates are different: the decoder parses every value, replaces the
last value and retains the key's first position before constructing the unique
object. An overwritten conversion failure is still a failure.

`validate_rank_prefix` looks up notes/query, checks count 1..64 and the nonblank
4096-byte query, then encodes every body before the 1 MiB aggregate bound.
Extra rank/note fields remain intact. `hash::admit_rank_hashes` consumes those
immutable bytes, computing each SHA-256 before its expected-hash lookup and
Python inequality comparison. Missing expected hashes are lookup failures;
non-equal Values, including non-strings, are mismatches. The first failing note
wins, and repeated bodies are hashed separately. `HashAdmittedRank` still needs
a tokenizer; an empty body with its correct digest is not token-empty admission.

Stream prefix checks retain iterable-key/type-at-use behavior and the 1..64
code-point ID guard. Actual hashes precede supplied Active-phase late-restore
policy; any present cache key, including Null, is late in Active, while absence
is permitted. Fresh/Active are policy inputs, not process/scorer attestations.
Embedding prefix checks retain the 1..128 ID guard, common type checks and
identity/query/document dispatch. Identity needs empty text and an initialized
identity stage; query/document remain pending tokenizer work. Unqualified echo
and projection types still do not establish score, model or worker correctness.

## Input byte codecs

`json::decode_input` takes a completed input snapshot, not incremental transport
state. It returns a decoded Value and consumed byte count, line EndOfInput, a
Source-family error, or a private original-input/mode pending-depth record.
There is no object/envelope prevalidation. Existing semantic guards are called
separately after successful decoding.

| Mode | Selected Source read and first framing disposition |
| --- | --- |
| RankOnce | read at most 8 MiB+1; more than 8 MiB is ValueError; empty input proceeds to JSONDecodeError |
| RankLine | first byte-LF/read cap 8 MiB+2; empty ends; refuse length above 8 MiB+1 or missing terminal LF |
| EmbeddingLine | first byte-LF/read cap 512 KiB+1; empty ends; refuse length above 512 KiB or missing LF |

Byte framing precedes decoding. The entire selected read is converted using
BOM/NUL-prefix detection and UTF-8/16/32 surrogatepass before any JSON syntax
work. Invalid Unicode later in that read wins over earlier JSON-looking syntax;
bytes after the first selected line remain untouched. Byte LF can truncate a
UTF-16/32 code unit before Unicode conversion, particularly in LE encodings.
This is not a claim that every multibyte JSON document forms a valid Source line.

UTF-8 rejects overlong/truncated/bad-continuation/out-of-range sequences but
preserves surrogate triples. UTF-16 combines valid unit pairs and preserves
lone low/high or high/non-low units, consuming only the high in the latter case;
odd trailing bytes fail. UTF-32 preserves surrogate units independently and
rejects incomplete units or values above U+10FFFF. JSON combines valid adjacent
*escaped* surrogate pairs, while literal surrogate code points remain distinct.
A final passed high surrogate can still be followed by a JSON missing-quote
failure. No lossy Rust String conversion or normalization is used.

The scanner accepts only ASCII JSON whitespace, strict strings/escapes and
exact number-token prefixes. Integer sign is excluded from the selected default
4300-digit limit; -0 is canonical Integer zero. A dot or valid exponent selects
FloatBits, with no integer digit limit. Established Rust f64 parsing is called
only after strict tokenization, preserving ties-even/underflow/overflow/signed
zero. NaN/Infinity/-Infinity are exact default literals; NaN normalizes to
positive quiet bits 0x7ff8000000000000. Other spellings are not aliases. This is
selected static default behavior, not proof of a real interpreter's globals.

## Shared rendering and byte measurement

One emitter walks values into owned byte or u128 count sinks. It supplies strict
rank/embedding replies and default-spaced cache measurement with nonfinite
values allowed. No output/count escapes a conversion error or pending traversal.
It never shortcuts on a byte bound or identity. ensure_ascii escaping preserves
key/array order, lowercase BMP escapes, DEL/lone surrogates and supplementary
surrogate-pair escapes; slash and printable <>& remain literal.

Ryu 1.0.20 supplies finite shortest digits; a notation adapter uses scientific
form below exponent -4 or at/above 16, lowercase e, explicit sign and at least
two exponent digits. Fixed integral floats have .0, scientific values do not;
-0.0 is preserved. No local shortest-float algorithm is implemented. Strict
profiles refuse every NaN/Infinity. Cache measurement spells all NaNs as NaN
and infinities as Infinity/-Infinity, including negative/payload NaNs.

Integer decimal conversion uses exact limb arithmetic. A normalized magnitude
with at least 448 u32 limbs is proven above 4300 digits (its minimum is 2^14304)
and fails before cloning/division. Smaller magnitudes still undergo exact
conversion and digit counting; 447 limbs is not a blanket refusal. Encoder and
count failures remain ValueError, including after an already-overbound field.

The selected implementation supports at most 64 nested containers, counting
the root as one. Entering 65 returns private `NeedsSourceDepth` carrying the
original input/profile. It is neither Source RecursionError nor ValueError,
configured-stack attestation, approximate count or complete output. Earlier
definite errors win; an earlier unsupported-depth value stays pending before
later possible errors. Runtime stack/memory/initializer policy is unqualified.
No custom hooks/kwargs, cycles, nonstring keys or alternate global config exist.
Unicode diagnostics count original input bytes; JSON syntax diagnostics count
decoded code points; framing/conversion/rendering use NotApplicable. Sites are
not a wire schema or exact traceback wording contract.

| Output API | Serialization then bound, inclusive of Linux LF |
| --- | --- |
| encode_rank_once | default spaces, strict nonfinite, LF; no Source oneshot output bound |
| encode_embedding_reply | compact strict encoding, at most 4 MiB including LF |
| prepare_rank_stream_reply | ordered id/result, default strict encoding, at most 65536 including LF |
| OrdinaryRankReply::encode_with_cache | same retained id/result, cache last, default strict encoding, at most 600 KiB+65536 including LF |

Go's separate oneshot stdout bound is not moved into the Source encoder.
`OrdinaryRankReply` has private fields and can be obtained only after actual
ordinary serialization/size success. It retains immutable id/result, so cached
encoding cannot substitute a result or supplied passed-size flag. A future real
adapter must obtain ordinary success before invoking scorer.snapshot. The cache
parameter is a supplied Value, not proof of snapshot/model provenance.

## Cache payload policy

Restore Null is no-op. Otherwise exact format/model/vectors keys and format
cairn.passage-vectors/1 precede shared full measurement. Conversion failures
become EnvelopeConversion ValueError; depth beyond 64 retains the original
state/profile in the compatibility-named NeedsSourceMeasure outcome. Finite
nonzero floats, larger integers within 4300 digits, and deeper supported
containers are now measured rather than SP2 pending. Only completed measurement
can enforce 614400 bytes and then compare supplied identity with Python !=.
Any non-equal Value, including non-strings, is other-model and ignored before
vector work. SuppliedIdentity does not attest to initialized model identity.

Same-model vectors require at most 128 ordered entries, 64 lowercase ASCII hex
key code points, 2048 encoded string code points, ASCII/strict Base64, 1536 raw
bytes and 384 finite little-endian float32 bit components with at least one
nonzero. Both signs of zero are zero. Every entry validates into temporary
storage before replacement; errors/no-op/ignored/pending preserve old state.
Signed zero, subnormals, accepted bits and order remain unchanged.

Snapshot accepts unique ordered keys and explicitly supplied float32 bits.
All 384-component finite vectors are checked/encoded before aggregate count and
size; a later invalid vector precedes count overflow. Snapshot allows all-zero
vectors and arbitrary string keys, unlike restore. It returns a decoded Value;
real dtype/shape/model provenance remains a future adapter obligation.

The established Base64 standard-alphabet engine keeps canonical padding and
allows nonzero unused trailing bits. The wrapper strips only a trailing run of
= after nonempty complete data quartets. TWFu=/TWFu==== -> Man and Zh== -> f;
leading/discontinuous padding, excess partial padding, incomplete input,
whitespace, URL-safe symbols and non-ASCII refuse. These remain pinned
[CPython binascii source](https://raw.githubusercontent.com/python/cpython/v3.11.15/Modules/binascii.c)
cases, without an executed Python oracle.

## Binding, fixtures and verification ownership

Accepted SP3 criteria: 32692 bytes, SHA-256
8406fc29030c071ea15429a344faa94d4523d884dbeebd71db48b13c57341cf4;
R1 upfront 5a05c93d-b1cd-48db-9734-7c0c10639b13/v1, A1(a)/A2 resolved. Source:

| Input | Git blob |
| --- | --- |
| semantic/4 contract | a44b916cf7e48ed579c95f7b4d60fd1914788874 |
| scripts/semantic_rank.py | 3af5ba61ee38450bdb074b6ac97c37338059fd05 |
| scripts/embedding_worker.py | b2d8c38784b2960c67c98ddc360185d754757d00 |
| scripts/install-semantic.sh | 0b5c655e93abbc90544a0fde6b98c753588ed8dc |
| semantic/command.go | d3b95b8249238ef221e74adc5ae093d75b6e6c18 |
| semantic/stream.go | 865ad69caa58b026e06202bf198eed2e856577f2 |
| semantic/embedding_command.go | f33017ede8101c90ba0237f67fe8c654d3de017f |
| core/semantic_ranking.go | b5092f0da3724f5a8a05575c11c72d6bfab0c429 |
| unchanged hash.rs | e77ef7c60e03e833ae449fcb9d6b171026d1d197 |

Static public CPython v3.11.15 snapshots are checksum-bound by the accepted
criteria's upstream-ledger and A1 ledger: json detect/scan/encoder, dtoa/strtod,
integer default, Unicode UTF8/16/32 and surrogatepass. Primary sources are
[CPython json](https://github.com/python/cpython/tree/v3.11.15/Lib/json),
[Unicode codecs](https://github.com/python/cpython/blob/v3.11.15/Objects/unicodeobject.c),
[surrogatepass](https://github.com/python/cpython/blob/v3.11.15/Python/codecs.c)
and [Ryu](https://github.com/dtolnay/ryu/tree/1.0.20).
These static references and hand-derived SDP3 literals are not actual Source
observations or runtime-policy attestation. No generic CPython feature surface
is inferred beyond the accepted contract.

The original 11 lib tests and all 5 hash tests remain byte-identical. All old
12 cache names/branch protection remain, with only the newly supported numeric
measurement/depth expectations reconciled. Twenty-four new inline json tests
cover the nine required fixture groups, including literal UTF families, exact
integer limbs/digit limits, float bits/notation, independent bytes/counts,
framing/output inclusivity, staged replies, pending/error precedence and raw
codecs feeding existing guards. Exact named inventory is 52 tests, declared
before B2's ONE new SP3 package check. Fixtures are not solely roundtrips; JSON
surrogate-pair re-encoding intentionally does not assert an IR identity law.

Dependencies are pinned sha2 0.10.9 force-soft/defaults false, base64 0.22.1,
ryu 1.0.20/defaults false, and local semantic-text. Exact selected manifest/lock
SHA-256 are c5d680def9ce467ff7f757adcd237880f39c84cb7d2b57e8a7dc9c0b470462b8
and 5f8600ad0326981c2bf5e41acb9aca1d5249bc1c66047321785c1dc01584a55c.
The lock retains official registry source/checksums. B2's explicit owned Cargo
directory source is a declared byte-identical materialization adapter, not a
new upstream or hidden fallback. Author API inspection never mutates B2 files.
Only checksum-bound inactive libc0.2.177 is allowed under the fixed x86_64 graph;
libc/script must never compile/execute. Active cpufeatures, existing ordinary
Rust generation/version scripts and version_check build=false remain unchanged.
Target/feature/graph/source/script drift or active native expansion stops work.
No tested MSRV or actual compilation is inferred from metadata/source authoring.

Author checks use installed rustfmt and whitespace checks only:

```sh
rustfmt --edition 2021 --check rust/semantic-protocol/src/lib.rs rust/semantic-protocol/src/cache.rs rust/semantic-protocol/src/json.rs
git diff --check
```

B2 owns the later one offline locked fixed-target changed-package regression,
with its accepted source config, HOME/TMP/target, exact logs/units and cleanup.
R1 owns one exact-candidate primary review. Accepted landing uses explicit
make check. No pre-component Cargo/compiler/test/probe, broad Go/Make default,
protected Python/Source/model/oracle/actual-worker run is part of authoring.
SP2 package FIRST stays consumed; actual-worker FIRST remains unconsumed.
Raw code/fixtures do not establish Source/model/tokenizer/native/controller/ABI,
Go-reference/numeric/worker/runtime/production qualification. C2, D1-A conditional
IO NOT_ESTABLISHED, D2 HOLD and RTERM uncertainty remain in effect.
