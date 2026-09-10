# Explicit file and symbol retrieval — 2026-09-10

Cairn now retains explicit file/symbol associations and retrieves by them through
ordinary CLI, MCP and native OpenCode tools. A note associated with
`core/currentness.go` can precede an incidental body mention even when its own
body omits that filename. This addresses part of E1; automatic host intent
collection and durable task-value evidence remain open.

## Behavior checked

The pre-change storage test lost supplied associations. The pre-change retrieval
test selected only the incidental mention and missed the associated note.
The implemented version passes these cases and the following boundaries:

- PostgreSQL capture, canonical retries, body edits, deliberate association
  replacement, exact-version history and case-sensitive file/symbol distinction.
- Required instructions, repository/task/currentness gates, hosted privacy,
  semantic discovery and fallback, and entity-only search pages.
- Frozen association and query digests, replay after a note changes, detection
  of altered historical associations, and retained-run intent before child launch.
- Ordinary source-reference and metadata preservation, plus association removal
  when record payloads are purged.
- Authenticated CLI/API, MCP stdio and OpenCode 1.18.21 custom-tool calls without
  client database access. The harness checks capture, search, pull, revise,
  replace associations and read the earlier version.

The full disposable PostgreSQL/race/API/MCP suite, static checks and forty Python
tests pass. An actual previous `72e7c24` binary also creates receipts and cached
responses in the disposable database; the new reader reproduces them, including
receipts from old readers that did not observe newer association metadata.
Native verification predates only the final consequential-demand accounting
change, covered by the final full integration suite. No answering-model task was
run for this feature.

## Corrections made during verification

Initial tests exposed missing history metadata, unchecked retained-run hints,
and association names surviving payload purge. Those paths now handle the new
metadata explicitly. A mixed failure/entity test corrected the first ranking
group so lexical score decides between the two exact matches.

Broader checks caught an unrelated associated note changing a search's schema.
The new schema now accompanies entity intent or delivered association metadata.
Previous-binary testing then caught a different case: replay was treating fields
ignored by an older reader as if that reader had observed them. Frozen facts now
distinguish an absent historical digest from an explicitly empty association set.

One early mode test used invalid revision labels instead of Git object IDs; that
fixture was corrected. An MCP assertion still expected the old search error
wording and was updated to include entity hints. These fixture failures were not
counted as evidence of implementation defects.

## Interpretation and provenance

The result establishes an ordinary retrieval capability and preserves tested
contracts. The examples were chosen to distinguish an explicit association from
a body mention; they do not measure general retrieval quality, maintenance cost,
or incremental model-task benefit. Associations are manually maintained, consume
context budget, and need consistent names. Earlier writers may drop unknown
metadata; consumers that reject schema 13 need an upgrade before receiving it.

The [interface contract](../entity-search.md) records matching, limits, retention
and compatibility. The [verification manifest](entity-search-2026-09-10.json)
identifies check logs, failed baselines and the validated decision receipt retained
outside the repository. Pincite's preservation and risk-driven-test guidance
informed the checks; its remaining modeling/interface obligations are explicitly
nonmaterial to this bounded value-reference feature. They do not establish the
larger goal's completion.
