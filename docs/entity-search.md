# File and symbol associations

Cairn can associate a note with a named file or symbol and retrieve it by that
association, even when its body uses different words. This is explicitly supplied,
fallible metadata. It does not establish that a file exists, a symbol resolved,
the host touched either one, or the note is correct.

Use the ordinary `entities` field on capture and search:

```json
{"entities":[{"kind":"file","name":"core/currentness.go"},{"kind":"symbol","name":"core.applicabilityReason"}]}
```

For CLI `remember`, `agent remember`, `search`, `agent search`, `run` and
`agent start`, supply repeatable `--entity-file` and `--entity-symbol` flags.
For example, after configuring an ordinary hosted profile:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$PWD" --task applicability-review --run review-1 \
  --entity-file core/currentness.go
```

The authenticated API accepts `draft.entities` on `create` and `edit`, and
`entities` on `compile` and `index`. MCP and native OpenCode expose `entities`
on `cairn_remember` and `cairn_search`. Search hints are never copied into captures.
The local profile's configured repository must match the requested repository.

## Names and matching

Each request permits at most sixteen references, with names of 1–512 UTF-8 bytes.
Kinds are `file` or `symbol`. File names must be canonical repository-relative
paths with forward slashes: no absolute paths, traversal, `.` components,
backslashes or colons. Qualified symbol names are opaque labels; choose the
same spelling consistently. Both kinds reject surrounding whitespace and control
characters. Names can contain spaces and Unicode and compare case-sensitively.
Invalid references return `INVALID_REQUEST`; correct the name and retry.
The store sorts and deduplicates references before retaining ordinary mutations
and search intent. At most 16 distinct references are accepted per request;
repeated references count once.

Matching requires the same kind and exact name in the already eligible
repository. `file:core/currentness.go` and a symbol named `core/currentness.go`
are different. There is no basename, suffix, alias, rename or AST resolution.
Use ordinary currentness pins when guidance also depends on a revision or phase.

With only entity hints, optional results must match at least one supplied
association. Supplying a reviewed failure signature as well admits either exact
match. With query text, other lexical matches remain available. Explicit entity
matches and reviewed signature matches precede incidental quoted body text;
within that first group, lexical score, scope specificity and recency decide.
Semantic search still requires nonempty query text. Its scores order candidates
within the same exact-match groups; labelled lexical fallback retains associations.

Mandatory instructions and all scope, currentness, authority, conflict, destination
and budget rules still apply. Hints cannot be combined with browsing. Ranked
pagination supports entity-only searches; repeat the hints on every page.
Unmatched entity-only candidates appear as `NO_ENTITY_MATCH` in the eligible
omission census. No private record identities or counts are exposed to hosted
callers.

## Versions, retrieval and retention

Associations belong to the exact note version. Index entries and body pulls show
them, and their encoded bytes count toward existing output budgets. A body-only
`cairn_edit` preserves them. To change or clear associations, supply a complete
replacement `draft`, copying the other metadata from the current pull and setting
`entities` to the replacement list or `[]`. This uses the ordinary expected-version
check and creates a new version. Privileged records retain their audited edit
requirements. Exact-version `cairn_history` reads show the earlier associations;
metadata-only history pages omit them.

Search receipts retain a normalized hint digest in `entities_sha256`, rather
than the raw query names. Selected notes retain their explicitly captured names.
Historical `recompile` requires the original query and `entities`; it checks the
frozen version's association digest as well as its body. Retained execution also
checks the entity intent before binding or launching a child. A changed note
version requires a fresh search and pull.

Migration 034 adds the versioned association table. Ordinary deletion removes it
with the note; audited forgetting excludes access and purges association names
with record bodies. Existing backup and external-copy residuals still apply.

Upgrade database writers and ordinary clients together before using associations:
older writers can drop metadata they do not understand. Packages carrying entity
intent or delivered associations use `cairn.semantic/13`, or
`cairn.semantic/14` when advisory conflicts are enabled; entity queries use
`lexical-scope-recency/7` or `semantic-scope-recency/4`. Consumers that reject
unknown schemas need an update before receiving those packages. Existing receipts
remain replayable, including receipts from older readers that ignored association
metadata. Searches without entity intent or delivered associations retain
their previous schema and ranking. Automatic file-touch collection and durable
cross-task benefit remain open requirements.
