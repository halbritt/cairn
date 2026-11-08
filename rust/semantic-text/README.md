# Semantic text predicates

`cairn-semantic-text` is a standalone, standard-library-only Rust library for
Cairn's frozen `semantic/4` text contract. Its sole public API is
`is_python_blank(&str) -> bool`: empty input and input consisting entirely of the
fixed Python 3.11 whitespace set are blank; any other scalar makes input
nonblank. The predicate allocates nothing, performs no I/O and leaves the input
unchanged.

The private predicate freezes all 29 scalars, including U+001C through U+001F.
Rust's default whitespace classification is not the compatibility definition.
The intended future consumers are S02 rank/stream and S03 embedding blank gates.
This package has no caller wiring, executable, tokenizer or model backend.

## Provenance

- The accepted requirement is in
  [`semantic.md:510-516`](../../docs/plans/hybrid-runtime-contracts/semantic.md),
  Git blob `a44b916cf7e48ed579c95f7b4d60fd1914788874`.
- The unit fixture copies the existing `PYTHON_WHITESPACE_CODEPOINTS` list in
  [`semantic_fixture_cases.py:10-12`](../../scripts/semantic_fixture_cases.py),
  Git blob `b6ff4e33b4f9b9097dbf49a6abfcf1490a9271f2`.
- Source uses `strip()` as a Boolean blank check at `semantic_rank.py:33`
  (blob `3af5ba61ee38450bdb074b6ac97c37338059fd05`) and
  `embedding_worker.py:40,60,107`
  (blob `b2d8c38784b2960c67c98ddc360185d754757d00`). These source identities
  establish the intended behavior; they do not bind or activate a frozen caller.

## Checks

From the repository root, formatting can be checked independently:

```sh
rustfmt --edition 2021 --check rust/semantic-text/src/lib.rs
```

The focused, model-free unit check is:

```sh
cargo test --locked --offline --manifest-path rust/semantic-text/Cargo.toml
```

Use a disposable `CARGO_TARGET_DIR` outside the checkout for review builds.
The package has no external dependencies and does not build the full runtime
kit. Fixtures cover the complete fixed list, empty and combined whitespace,
adjacent nonblank boundaries, NUL/ZWSP/BOM, and ASCII/CJK/emoji/combining text
with leading, trailing and interior whitespace. They distinguish the required
predicate from copying `str::trim`, accepting only empty text, or treating any
whitespace within nonblank text as blank.

Source publication is separate from execution and acceptance. Cargo execution
is reserved for S-B2 first; this candidate's author has not run it. No Python
differential qualification, frozen caller activation, worker, tokenizer, model,
native/ABI, controller or Go-reference execution is established here. D1-A I/O
conformance remains not established, D2 remains on hold, and full runtime and
S01-S03 acceptance gates remain open.
