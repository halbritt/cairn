# Prefer exact text in memory search

Put a known identifier, file path or error message in ASCII double quotes to
prefer notes containing that exact text. Other lexical matches remain available.
For example:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "/projects/shared-memory" --task applicability-review --run review-1 \
  'repair review "core/currentness.go"'
```

Both Codex MCP and native OpenCode accept the same query:

```json
{"query":"repair review \"core/currentness.go\""}
```

The exact path takes precedence over a note that only mentions “core,”
“currentness” and “Go,” even if that other note matches more words. A saved error
message or signature digest can be quoted the same way. The text must already be
in the note body; Cairn does not inspect files or infer observed failures.

## Matching contract

Matching is case-sensitive, contiguous substring matching against eligible note
bodies. It does not resolve entity identity, canonicalize paths, follow symlinks,
or require a whole-word or whole-path boundary. A quoted relative path can match
inside an absolute path. Short, common phrases may be less useful than distinctive
identifiers. Use ordinary unquoted search for case-insensitive word matching.

The first eight distinct nonblank pairs of ASCII double quotes are considered,
with at most 256 UTF-8 bytes inside each pair. Duplicate phrases have no additional
effect. Whitespace inside a phrase is literal. Empty, whitespace-only, overlong,
additional and unmatched quote content still participates in ordinary lexical
matching. There is no quote-escaping syntax inside the query; JSON and shell
quoting only transport the query text. Backticks and typographic quotes have no
special meaning.

Any one literal match supplies one preference. Repeating a phrase, matching several
phrases, or repeating text in a note does not multiply it. Required instructions
are allocated first. Scope, currentness, evidence, authority, destination, kind
selection and token budgets retain their existing meaning. A quote neither makes
a note eligible nor forces its inclusion. Exact quoted stop words or punctuation
can retrieve a note even when lexical filtering supplies no terms.

With semantic discovery enabled, exact text still takes precedence among eligible
optional notes; semantic scores order notes within the matching and nonmatching
groups. If scoring is unavailable, the same preference remains in the labelled
lexical fallback. Similarity and literal overlap do not establish correctness.

Long-note previews show the earliest matching occurrence and retain source byte
positions for pulling the original passage. Previews remain at most 160 bytes;
a long literal can be truncated. Read relevant notes before relying on them.

## Receipts and compatibility

Nonempty lexical context index searches with usable quoted text use
`cairn.semantic/17` with `binary-idf-scope-recency/6` (or `/7` with a failure
signature, `/8` with entity hints); ready persistent semantic index searches use
`cairn.semantic/19` (or `cairn.semantic/20` with `semantic_query`) and
`interleaved-scope-recency/6` (or `/7`, `/8`). Whole-body `compile` and
request-scoring semantic discovery use `lexical-scope-recency/5` or
`semantic-scope-recency/2`. Unquoted queries keep their corresponding plain-tier
ranking profiles. The literal preference is a frozen boolean in the protected
candidate explanation; raw queries are still represented by their existing
digest. Historical recompilation checks the preference against the supplied
original query and retained note version, and reproduces the receipt's original
ranking and preview behavior.

Older binaries cannot recompile newer ranking profiles; older receipts still
recompile under their original profiles. Retrying a compile request whose
current package would change returns `STALE_PACKAGE`; use a new request ID for
current retrieval. Task-value evidence across real agent runs remains on the
[roadmap](roadmap.md).

Known failure signatures can also find reviewed lessons without shared query
vocabulary. See [failure signature search](failure-signature-search.md) for the
optional `error_signature_sha256` tool field and operator sharing requirement.
