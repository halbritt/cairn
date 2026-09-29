# Reading retained task delivery evidence

`scripts/trial_task_evidence.py` reads a retained task evaluator report and its
native Claude or Codex streams. It does not run agents, call Cairn, or change
grades. Use it when distinguishing hook delivery from successful manual MCP
retrieval:

```sh
python3 -B scripts/trial_task_evidence.py /tmp/trial/agent.json \
  --corpus trials/task-eval/corpus.json \
  --output /tmp/trial/mcp-delivery.json
```

The output must be a new file. The corpus must match the report's frozen hash.
The reader records hashes of the report, corpus, analyzer and each available
stream. Missing streams are labelled; run paths outside the report's `runs`
directory are rejected.

Calls are correlated by native tool ID. Started calls remain incomplete until
a completion arrives. Failed calls contribute response text bytes but no
successful delivery. Search previews, selected whole bodies and checked byte
spans have separate extents. Fixture identity is an exact full-source SHA-256
match. For a known source, a partial span must also match its original UTF-8
bytes. The reader rejects corrupt spans, ambiguous corpus hashes and supported
responses outside the trial collection. It emits IDs, hashes and byte counts,
without copying raw bodies.

Unparsed text, unsupported payloads and unmapped source hashes remain explicit.
For example, generated distractors absent from the authored corpus are
unmapped. This reader supports search and pull responses; it does not establish
complete coverage of arbitrary MCP tools or malformed provider streams.

`response_text_bytes` includes JSON response envelopes and repeated calls. It
excludes provider tool schemas, protocol framing, hooks and direct prompts.
`body_bytes` counts successfully delivered body bytes, including repeated
delivery. `body_notes` is a deduplicated list and can include partial excerpts;
inspect each delivery's extent before claiming that a full note was read.
Neither delivery nor a fixture ID proves applicability, truth, action or task
completion.

## Verification

Local tests cover native call correlation, failed and incomplete results,
preview/body separation, UTF-8 span validation, frozen-corpus mismatch,
out-of-directory runs, missing streams and unaccounted payloads. The reader
has also been exercised against retained Claude and native Codex trial streams.
This is evaluation tooling, with no store or deployed recall changes.
