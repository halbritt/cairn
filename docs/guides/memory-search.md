# Search and inspect memory

Search Cairn before repeating an investigation or applying a remembered project
decision. Search finds potentially useful notes; reading the complete note and
checking current source establishes whether it applies to your task.

These CLI examples assume `CAIRN_COLLECTION` contains your provisioned collection
name and the default socket and `agent.token` are configured. Add `--socket` and
`--token-file` immediately after `cairn agent` for other profiles. Search requires
explicit `--task` and `--run` labels.

## Choose a query

Start with the project, problem, and any known identifiers:

```sh
cairn agent search --repo "$CAIRN_COLLECTION" \
  --task storage-repair --run attempt-1 'schema migration rollback'
```

Lexical search matches words in eligible notes. An ASCII double-quoted phrase
prefers an exact body substring before optional matches:

```sh
cairn agent search --repo "$CAIRN_COLLECTION" \
  --task storage-repair --run attempt-1 'repair "core/currentness.go"'
```

Quotes improve ranking; they do not resolve file identity or guarantee that every
result concerns that file. When notes have explicit file or symbol associations,
add `--entity-file core/currentness.go` or `--entity-symbol Store.Compile`.
Associations are retrieval hints, not verified workspace observations. See
[entity search](../entity-search.md) and [quoted search](../quoted-search.md).

Use `--semantic` when you know the concept but not the saved wording:

```sh
cairn agent search --repo "$CAIRN_COLLECTION" \
  --task storage-repair --run attempt-1 --semantic \
  'recover after restoring an old database backup'
```

Semantic retrieval requires a configured backend. Inspect `discovery.state` to
see whether semantic discovery ran or Cairn returned a labelled lexical fallback.
Other request failures still require handling. See
[semantic discovery](../semantic-discovery.md).

## Read the result

Read required context in `selected`. Candidate entries in `index` are previews:
use a relevant entry's complete returned `pull_command` for the CLI, or copy its
complete `pull_arguments` into `cairn_pull` for MCP. Do not construct handles from
record IDs. Pulls check eligibility and version again; after an expired or stale
handle, search again.

A preview can omit prerequisites or exceptions. Read required instructions and
competing positions whole. For a large ordinary note, an explicit byte span can
answer a narrow question, but broader conclusions require the whole source.
See [index and pull](../index-and-pull.md).

## Set a budget

Declare the room available for memory, including response metadata. In MCP:

```json
{
  "query": "migration rollback",
  "available_tokens": 16000,
  "memory_budget_bytes": 6000,
  "inspection_policy": "first-fitting-whole/2"
}
```

`available_tokens` uses Cairn's conservative UTF-8 byte accounting; it is not a
model's advertised context size. `memory_budget_bytes` narrows the allowance
shared by search and later pulls. `first-fitting-whole/2` reserves room for a
complete eligible source or competing group. It does not pull it automatically
or guarantee relevance. `/1` retains the older 24,000-byte expansion ceiling.
`min_pull_bytes` is an alternative manual reservation, not an automatic-pull
threshold; do not combine it with an inspection policy. See
[whole-source allocation](../inspection-allocation.md).

## Browse or request another page

Use `--browse` without a query when the stored vocabulary is unfamiliar. Optional
`--kind decision --kind preference` filters ordinary results by their labels.
Start paged search with `--offset 0`, then follow `page.next_offset`; browsing
uses `browse.next_offset`. Keep query, scope, semantic mode, filters, and context
unchanged across pages. Each page has its own budget, so account for their
combined size. No result means no eligible match was returned, not proof that
no relevant knowledge exists.
