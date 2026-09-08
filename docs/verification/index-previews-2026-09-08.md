# Matching index previews, 2026-09-08

An operational search for `opencode-config` returned the right procedure, but
its preview showed only an earlier introductory paragraph. The matching command
appeared later in the stored body. The live search and exact pull were retained
privately before changing the implementation.

Index previews now select a source passage containing more distinct query terms
than the opening preview, within the same 160-byte limit. Candidate passages
start near matching words with up to 40 bytes of preceding context. The existing
ranker's token rules handle case, underscore identifiers and function words.
Repeated occurrences do not increase the distinct-term count. Equal scores keep
the earlier preview, and an empty or unmatched query keeps the original prefix.
Omission markers count toward the limit; UTF-8 characters remain intact.

This is a presentation change after eligibility and ranking. It cannot find a
note with no lexical match or establish that a matching note is useful. Different
encoded preview lengths can affect how many pointers fit the existing budget.
The body digest and full-body pull continue to identify the exact stored version.
Excerpts are incomplete source text, not generated summaries or instructions to
apply without reading the body.

## Verification

- Public store integration shows the buried command in search, pulls the exact
  complete body, and reproduces the historical preview after the note is edited.
- Focused tests cover case, split identifiers, negation, Unicode whitespace,
  repeated versus distinct terms, ties, and empty/unmatched queries.
- A 20-second fuzz run completed 531,720 executions checking the size limit,
  valid UTF-8 and contiguous source text.
- Ten packages produced by the previously installed binary recompile identically
  through the new binary, as do ten newly produced packages. These cover body
  and index modes, buried commands, whole/split identifiers, question-only queries
  and browsing. Three matching index queries expose the buried command or
  identifier previously absent from the preview.
- `make test-integration` and `make check` pass, including real disposable
  PostgreSQL, Unix API and MCP stdio coverage.

New indexes use `cairn.semantic/5`; historical v4 indexes retain their prefix
behavior. Body compilation remains v3 and ranker v4 is unchanged. Reusing an old
index compile request across the format change refuses with `STALE_PACKAGE`;
use a new request ID. Body compile retries remain unchanged. No migration or new
dependency is required.

This establishes a working preview capability and compatibility, not reduced
agent pulls, faster tasks, improved task acceptance or broad memory benefit.
The retained private compatibility output and test logs use the prefix
`/tmp/cairn-search-preview-` on the development host.

## Installed result

Commit `b511f267b02ea79f151d63f4912351e2ae0fae96` passed
[CI run 34283302285](https://github.com/halbritt/cairn/actions/runs/34283302285).
The installed CLI and restarted API use the same clean build. Through the
ordinary hosted MCP profile, the previously hidden `opencode-config` command
now appears in the operational procedure's preview. The exact prior procedure
still pulls unchanged; retry credits and clean EOF also passed without a client
database connection or HOME. Both services are active and profiles are unchanged.
No model was called. [Machine-readable evidence](index-previews-2026-09-08.json)
records the build identity and verification scope.

The decision used validated Pincite packet `pkt-20e3297d3d29a442` for repository
precedence, evidence before intervention, historical behavior preservation and
bounded presentation. Sixteen remaining obligations concern unchanged identity,
ingest, UI, configuration and interface mechanisms or generic procedures; their
individual nonmaterial classifications are retained in the private decision.
The deployment-parity obligation was subsequently satisfied by the installed
MCP and process checks. The packet did not establish general memory benefit.
