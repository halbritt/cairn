# Semantic rank note score

`cairn-semantic-score` is a standalone, standard-library-only Rust library for
the final single-note reduction in Cairn's `semantic/4` rank contract. Its sole
public API is `rank_note_score(similarities: &[f32]) -> i32`. The planned consumer
is the S02 rank/stream path after matrix-vector multiplication has supplied each
note's chunk similarities. Indexed S03 scoring remains owned by Go/pgvector.

The function follows this fixed sequence:

1. Start with `-1.0_f64`. Visit the supplied similarities in order, promoting
   each `f32` to `f64`, and replace the maximum only when the new value is greater.
2. Clamp the final maximum once to `[-1, 1]`.
3. Multiply by `1_000_000` in `f64`, round ties to even, and return an `i32` in
   `[-1_000_000, 1_000_000]`.

An empty slice returns the accumulator score `-1_000_000`. This primitive does
not admit requests or notes; Source validation and token-empty/chunk failures
remain separate. NaNs never replace the accumulator. Positive infinity reaches
the upper clamp, negative infinity cannot raise the initial accumulator, and
either signed zero scores zero. These arithmetic results define no worker
finite-input or refusal policy.

## Provenance and boundaries

The arithmetic sequence comes from step 4 of
[`semantic/4`](../../docs/plans/hybrid-runtime-contracts/semantic.md#strict-score-arithmetic-and-indexed-parity),
Git blob `a44b916cf7e48ed579c95f7b4d60fd1914788874`, lines 144-180, and the
accumulator/max/clamp/round sequence in
[`semantic_rank.py`](../../scripts/semantic_rank.py#L111-L117),
Git blob `3af5ba61ee38450bdb074b6ac97c37338059fd05`, lines 111-117.

The fixtures use independent literal expected integers. They include the
representable binary ties `+/-1/128`, `+/-3/128`, `+/-5/128` and adjacent `f32`
values. The synthetic scaling regression uses input bits `0x3dfcd6c3`, whose
promoted/scaled exact rational is `258907046875/2097152`, giving `123457`.
Scaling first in `f32` gives `123456.5`, which instead rounds to `123456`.
The negative counterpart mirrors these results. These are synthetic arithmetic
fixtures, not captured model output or a Python differential qualification.

Normalization, division, matrix/dot products, raw CLS values, tokenization,
model execution, request/owner/chunk/cache management and actual caller binding
require their own implementations and evidence. This library supplies no worker
or backend and establishes no full runtime, numeric/model or operational
acceptance.

## Checks

Rust 1.77 or later is required for
[`f64::round_ties_even`](https://doc.rust-lang.org/std/primitive.f64.html#method.round_ties_even).
From the repository root:

```sh
rustfmt --edition 2021 --check rust/semantic-score/src/lib.rs
cargo test --locked --offline --manifest-path rust/semantic-score/Cargo.toml
```

Use a disposable `CARGO_TARGET_DIR` outside the checkout for builds. The package
has no external dependencies and builds independently of the full runtime kit.
