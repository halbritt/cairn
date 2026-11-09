# Prepared semantic tokenizer

S-TOKENIZER-1 implements the prepared tokenizer and batch-one input-tensor
portion of the future semantic backend. It calls the real pinned Tokenizers
0.23.2 crate. It supplies neither an ORT/model session nor a complete rank
backend, worker, readiness reply or fallback.

`PreparedTokenizer::from_bytes` consumes caller-supplied `config.json`,
`tokenizer_config.json`, `special_tokens_map.json` and `tokenizer.json` bytes.
It follows FastEmbed 0.8.0 `load_tokenizer`: select the configured context
length, load the serialized tokenizer, enable model truncation, enable default
padding only when no padding is already present, then add special tokens in
the source map's insertion order. Pad ID zero is the explicit FastEmbed
`config.get("pad_token_id", 0)` policy. Model vocabulary, normalization,
pre-tokenization, processing, decoder and existing padding all come from the
supplied tokenizer JSON. No BERT vocabulary or unknown-token ID is substituted.

String special entries are special and unnormalized by default. Dictionary
entries validate the Python AddedToken constructor's supplied `special`,
`single_word`, `lstrip`, `rstrip` and `normalized` flags. The Python wrapper then
sets special before materializing the Rust AddedToken: unspecified normalized
is false, while an explicit normalized flag still overrides it. The Source ignores
other map-value types and prints unknown AddedToken options. This component
records those branches as `PreparationNotice` values rather than concealing
them. Parsing, configuration and Tokenizers errors remain explicit and carry
their input file or operation stage; they do not become a successful default.

As in `Scorer.__init__`, the model tokenizer is serialized and deserialized to
make a separate rank tokenizer, then both truncation and padding are disabled
on that rank clone. `rank_encode` maps the published `SpecialTokens` policy to
`add_special_tokens`; `rank_decode` retains specials and the real decoder's
cleanup. The published PyString strict UTF8 conversion rejects lone surrogates
with its existing UnicodeEncodeError family. This component adds no text/token
admission limit or query prefix; the published rank core owns those policies.

`model_encoding` encodes one text in a one-element batch with the model settings
intact. `model_tensors_in_order` repeats that operation in caller order, so
query/passage neighbours cannot change each other's padding. `BatchOneTensors`
copies actual Encoding IDs and attention mask into owned int64 rows of shape
`[1, length]`, checking matching lengths, and creates zero model token-type rows.
Empty text follows the real
tokenizer and postprocessor. There is no model invocation or numeric scoring.

FastEmbed's `OnnxTextModel.onnx_embed` constructs zero token-type tensors;
selected clarification `4a61b883/v1` binds that policy here. `model_encoding`
still exposes the raw Encoding with its original type IDs, unmodified. A
synthetic nonzero pad-type fixture proves the distinction through both production
conversion paths. The later P2 ORT adapter must obtain actual owning-session
input names: input_ids is supplied, and attention_mask/token_type_ids are added
only when advertised. This helper supplies values and invents no session names,
ready flag or ORT qualification.

The object retains exact copies and SHA256 digests of all four input files.
`model_json` exposes the actual prepared model serialization for a later
derivation record. Neither digest nor serialization is a worker identity or
proof of an initialized model/backend. The caller supplies bytes; this code
does no asset discovery, download or external process operation.

The manifest pins `tokenizers = 0.23.2` with the Python reference's root defaults
`progressbar`, `onig`, `esaxx_fast`, plus `parity-aware-bpe`. Its regex path is
Onig. Onig native C and esaxx C++ build requirements are part of the new closure
which B2 must account for before execution. No HTTP/hub/TLS, fancy-regex or WASM
feature is selected. Direct `serde_json = 1.0.145` uses `preserve_order` for
Python map order and `unbounded_depth` for the component config parser; the
actual Tokenizers JSON parser retains its upstream behavior. Existing
`sha2 = 0.10.9` no-defaults/force-soft and local protocol/rank are reused.
Cargo.lock is copied unchanged from B2's completed source-only intake
`9582f497/v1`: 26338 bytes, SHA256
`9d800559de2a1ba150b44ca2f9a5c7c7f825e8dc8711c3578b9549e4245c9354`,
116 packages (111 registry, five local), retaining all twelve old registry
records. B2 independently matched the archive checksums to canonical index
rows. Its static graph has active libc, Onig C/esaxx C++, build scripts and
procedural macros that require a separately selected component environment.
This resolved lock and static disposition do not grant compilation or native
qualification. The author ran no Cargo resolution/fetch, compiler, tests or
prepared-asset tokenizer execution.

Tests use a small literal synthetic WordPiece JSON through the production
constructor and real Encoding API. They cover special flags/order, model versus
rank policies, decoder cleanup, batch-one IDs/masks/types/shapes, Unicode/empty
text, strict surrogate refusal, owned input receipts and explicit config errors.
They are authored component cases, not the required prepared294-ID/length
comparison. B2 owns the admitted component invocation; R1 owns primary review.

Source basis: retained FastEmbed 0.8.0 `common/preprocessor_utils.py`
`load_tokenizer` and `text/onnx_text_model.py` `tokenize`/`onnx_embed`;
`scripts/semantic_rank.py` `Scorer.__init__`, `chunks`, `score`; published
semantic-rank backend policies; pinned Tokenizers archive `tokenizer/mod.rs`,
`added_vocabulary.rs`, `encoding.rs`, `utils/padding.rs` and `truncation.rs`.
The official v0.23.2 Python binding's Cargo manifest and AddedToken constructor
confirm the feature selection and constructor-before-add-special ordering.

All294 token IDs/lengths before model/session construction, ORT assets/runtime,
NumPy/OpenBLAS arithmetic/raw bits, containment/ABI and actual Go-supervisor
qualification remain separate P1-P5 gates. Actual-worker FIRST is unconsumed.
Source construction and synthetic component tests do not complete S02 or grant
runtime or production acceptance.
