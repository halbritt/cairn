# Recent file hints in OpenCode

The optional recent-files plugin makes guidance associated with a file easier to
find after an agent reads that file. It observes successful native text-file
`read` results and adds repository-relative file hints to the next fresh
`cairn_search`. The agent still chooses when to search and what guidance to pull.

For example, a note associated with `core/currentness.go` can explain how to
check applicability without mentioning that filename in its body. After reading
the file, an agent can search for guidance using its own wording; the association
can bring that note into the result. File associations remain fallible and
require deliberate maintenance. This capability does not establish task benefit
or make a retrieved note authoritative.

## Install and disable

Add `--recent-files` to the full [project installation command](opencode-tools.md#install-in-a-project):

```sh
cairn opencode-install --project "$PWD" \
  --socket /absolute/path/to/api.sock \
  --token-file /absolute/path/to/hosted-agent.token \
  --repo /absolute/path/to/canonical/repository \
  --recent-files
```

This writes `.opencode/plugins/cairn-recent-files.ts` alongside the ordinary
adapter and settings. Inspect differing files and use `--replace` for upgrades;
all selected destinations are checked before writes. Start a fresh OpenCode
session. Installation preserves host permissions and does not contact Cairn or a
model. The CLI/API must support [explicit entity retrieval](entity-search.md);
the plugin adds no database migration.

Omitting `--recent-files` preserves the usual two-file installation and leaves
any existing plugin untouched. To disable it, remove that exact plugin file and
start a fresh session. For one search, use `entities: []`.

## Search and continuation

Automatic hints apply only when `entities` and `request_id` are omitted,
`browse` is false or omitted, and `offset` is zero or omitted. Explicit hints,
including an empty list, always take precedence. Searches with an explicit
request UUID or a later page receive no new automatic hints.

Native search results include `query_entities`, the effective hints submitted to
Cairn. Copy that list into `entities` when retrying the returned `request_id` or
requesting a later page. Preserve the query, context and other search options as
well. This prevents subsequent file reads from changing the repeated search.
`query_entities` is presentation metadata within the output budget; it is not
part of the sealed semantic package. The core still validates and normalizes
the supplied hints.

Without text, an automatically hinted search matches associated notes only.
If none are found, use a text query or explicit browsing. With text, broader
lexical matches remain available. Automatic hints never alter capture, edits,
scope, applicability, destination filtering, or mandatory instruction handling.

## Observation and retention limits

The plugin keeps up to 16 distinct file names per session and 128 sessions per
OpenCode instance, evicting the least recently read entries. Each file hint
expires 15 minutes after its last read. Expiration is checked on read/search
hook activity; an idle process can retain the bounded map until another hook or
process exit. Restarting the host loses these hints; resuming a stored session
does not reconstruct them from its transcript.

The plugin retains relative names and read times in memory. It does not retain
file contents, scan files, write a cache, call Cairn, or invoke a model.
OpenCode and ordinary Cairn retrieval retain their existing histories and
receipts independently of this map. Submitted names appear in the ordinary
tool arguments/result and are therefore visible to the model using the tool.

Only native read metadata labelled `file` is considered. Missing or denied
reads, directory listings and reads without that metadata add no hint. Paths
outside the worktree, filesystem-root worktrees, and names incompatible with
Cairn's file-reference contract are skipped. Paths are logical paths relative
to OpenCode's worktree, without physical symlink resolution. Reading a symlink
inside the worktree can consequently supply its logical name. Reading is not
proof of editing, relevance, file identity across renames, or execution success.
Shell reads, language-server activity and inferred symbols are not collected.

The hook never fetches memory. Retrieval passes through the existing native
`cairn_search` tool and its permission callback. This avoids the permission gap
found in the earlier [system-prompt hook investigation](verification/opencode-startup-hook-2026-09-09.md).
For a file-specific read permission in OpenCode 1.18.21, use the worktree-relative
name, such as `core/currentness.go`; the read argument itself may be absolute.
An absolute-only allow pattern did not permit that read in the installed check.

The [verification report](verification/recent-file-hints-2026-09-10.md) records
the actual native path and its limits. Automatic intent in other harnesses and
evidence of sustained task value remain open.
