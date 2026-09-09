# Local semantic ranking, 2026-09-09

A local embedding model recovered the labelled answer within the first three
results for all fifteen answerable documentation questions. Lexical v4 reached
thirteen or twelve, depending on insertion order. This supports testing optional
semantic retrieval against Cairn's actual eligibility and packing rules. It does
not support replacing lexical search, treating similarity as answer confidence,
or claiming improved agent task results.

## Experiment

The [comparison script](../../scripts/compare_semantic_retrieval.py) uses the
unchanged [documentation workload](../../core/testdata/retrieval-quality.json):
twelve passages, fifteen labelled questions and two no-answer controls. Only
`storage?` is a verbatim user question. Other questions and relevance labels are
authored development material previously used to tune lexical search. No model
selection, threshold fitting or parameter tuning followed the results.

The script records its plan before loading the model. It compares:

- Lexical v4's distinct shared terms, then insertion recency.
- The sum of `floor(1,000,000 / document_frequency)` for shared terms, then
  insertion recency. This candidate was first explored manually on the same
  corpus; it is not a fresh hypothesis or a BM25 implementation.
- Cosine similarity over local `BAAI/bge-small-en-v1.5` embeddings, with note ID
  breaking ties. Passages contain only their existing bodies; queries use the
  retrieval prefix documented in the [model card](https://huggingface.co/BAAI/bge-small-en-v1.5).

[FastEmbed](https://qdrant.github.io/fastembed/examples/Supported_Models/) 0.8.0
loaded its quantized ONNX artifact from `qdrant/bge-small-en-v1.5-onnx-q` using two
CPU threads. Model and tokenizer files totalled 67,179,163 bytes. All 29 inputs
fit without truncation; the longest was 222 tokens against the 512-token limit.
The script refuses longer inputs. Model loading took 2.46 seconds and embedding
the batch took 6.44 seconds on this host. These single observations measure no
concurrency, long-note, scale or steady-state latency behavior.

## Results and tradeoffs

Counts are out of fifteen answerable questions. These are ranks before Cairn's
packing and delivery, not delivered contexts or answered tasks.

| Method | First | Top three |
| --- | --- | --- |
| Lexical v4, forward insertion | 11 | 13 |
| Lexical v4, reverse insertion | 10 | 12 |
| Frequency weighting, forward | 10 | 13 |
| Frequency weighting, reverse | 10 | 12 |
| Semantic similarity | 11 | 15 |

Semantic ranking addresses the observed vocabulary gaps:

- `storage?`: no lexical result; labelled answer at semantic rank 2. An unrelated
  evidence-pull passage still ranks first.
- “Where does Cairn keep its data?”: lexical rank 11 or 4; semantic rank 1.
- Scope broadening: lexical rank 1 or 6; semantic rank 2.

It also regresses answers that lexical search placed first: the revision
question falls to rank 2 and citation counting to rank 3. Exact and separated
`CAIRN_HOME` questions remain first. Frequency weighting fails to recover the
storage misses and worsens some other ranks, so it is not selected.

Semantic search always returns neighbours here, including for both no-answer
controls. The unanswered GPU question's leading similarity is 0.6704, higher
than the labelled storage answer's 0.5475 and citation-count answer's 0.6175.
A single similarity threshold cannot separate these examples while retaining
those answers. No threshold was fitted. The unanswered price question also
returns neighbours, whereas lexical v4 returns nothing.

## Verification and next step

All 34 lexical query/order comparisons match ordering and answer ranks in the
retained real-store v4 index results from the
[question-word experiment](question-words-2026-09-08.md). This checks the scratch
comparator against actual compiler evidence; production tokenization remains
owned by `core/compiler.go`. The comparator explicitly requires ASCII inputs.
Model outputs have checked cardinality, dimension, finite values and nonzero
norms. Reusing the output directory refuses before model loading and leaves all
recorded artifact hashes unchanged.

The next integration should offer semantic discovery as an optional route while
preserving lexical search. It must rank only permitted, current candidates and
keep mandatory selection independent of the model, as design §11.1 requires.
A same-snapshot comparison must exercise real packing, long notes,
deletion/currentness checks, model unavailability and downstream source use before
claiming production usefulness. Those requirements remain pending; this script
does not implement a Cairn search backend or alter the installed application.

Run in a separate environment, choosing a new output directory for each
intentional run:

```sh
uv venv /tmp/cairn-semantic-env
uv pip install --python /tmp/cairn-semantic-env/bin/python fastembed==0.8.0
timeout 180 /tmp/cairn-semantic-env/bin/python scripts/compare_semantic_retrieval.py \
  --output /tmp/cairn-semantic-ranking-new \
  --cache /tmp/cairn-semantic-model-cache
```

Python 3.11 or later is required. First use downloads model files; inference is
local. The package is pinned, but upstream model files and transitive dependencies
can change. Compare recorded identities before treating another run as a replication.

[Comparison metadata](semantic-retrieval-2026-09-09.json) retains ranks, model
hashes, package versions and local artifact pointers. Vectors and complete score
matrices remain outside Git. No operational notes, database, harness configuration,
core ranker or installed binary changed.

The decision used doctrine packet `pkt-592d89c0775c8bbc`: repository precedence,
evidence before intervention, one uncertainty at a time and explicit eligibility
invariants. The metadata retains its hash, corpus/retriever identities, validated
recommendation receipt and seven nonmaterial obligations. Those remaining
obligations concern architecture/co-change analysis, repeated scheduling and
formal preservation procedures outside this experiment's claims. They do not
waive the integration requirements above.
