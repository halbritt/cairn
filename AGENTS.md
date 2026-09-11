# Cairn

Read README.md and docs/design-review.md before extending the initial slice.
The source design is pinned under docs/sources. Follow the operator-authored
producer-attribution correction where it supersedes earlier synthesis.

- Keep PostgreSQL as the operational store; tests use disposable clusters.
- Run `make test-integration` and `make check` for store changes. `make test`
  alone skips database coverage when no test DSN is set.
- Never use an existing production database for tests or test cleanup.
- Preserve store-owned identity and observation channel fields. A payload field
  or a caller-selected principal flag is not authentication.
- Keep unimplemented authority, consequential reads, and destination delivery
  unavailable until their contracts and tests exist. Do not infer permissions
  from these project instructions; the user's current authorization governs.
- Do not capture private Council sessions or commit operational memory,
  credentials, generated binaries, model output, or raw workspace exhaust.
- Report the implemented slice and tested claims separately from full design
  acceptance and measured usefulness.

## Use the provisioned memory interface

The owner's host now uses this existing memory collection across projects in
Codex, OpenCode, Agy, and Claude Code. The canonical repository value below is
the shared collection name, independent of the working directory. Include actual
project context in notes where it matters. See [shared memory](docs/shared-memory.md).

On the owner's host, when `~/.local/share/cairn/hosted-agent.token` exists and
`cairn` is installed, use that profile for hosted-agent memory in this repository.
At the start of a substantive task, search for relevant prior decisions,
preferences and lessons. Project direction and corrections are useful memory
alongside technical procedures; consult their current source before applying them.

When the native Cairn MCP tools are available, use `cairn_search` and inspect
relevant results with `cairn_pull`, passing their complete `pull_arguments`.
When prior notes have explicit file or symbol associations, supply `entities`
on `cairn_search` (CLI `--entity-file` / `--entity-symbol`) to prefer those notes.
Entity-only search requires an association; if none is found, use ordinary text
search. Associations are fallible and never certify a workspace observation.
See `docs/entity-search.md`.
When the task names a file, symbol or exact error text, put that known text in
ASCII double quotes inside the query, for example
`{"query":"repair review \"core/currentness.go\""}`. This prefers an exact body
substring before other optional matches. It does not resolve file/entity identity,
inspect the workspace, filter out all other results, or establish relevance from
a filename mention. Pull the current note and check its guidance against source.
See `docs/quoted-search.md` for syntax and limits.
If lexical wording misses a likely topic, try `semantic: true` with the nonempty
query (CLI `--semantic`). Inspect `discovery.state`: unavailable semantic workers
return labelled lexical fallback. Similarity does not establish that a source
answers the question; pull and verify it. Keep ordinary lexical search for precise
identifiers and use semantic discovery selectively because it adds CPU latency.
To inspect more ranked matches, include `offset: 0` with the query (CLI
`--offset 0`), then follow `page.next_offset` with the same query, semantic mode,
kinds, scope and context. Each page has its own budget; account for combined
context and restart if notes or semantic availability change. No offset keeps
the existing unpaged behavior.
If the saved vocabulary is unknown, use `cairn_search` with `{"browse": true}`
and no query to inspect eligible previews, or add `--browse` to the CLI search
below. Follow `browse.next_offset` with `offset: N` (CLI `--offset N`) to reach
later pages within the same scope. Each page has its own budget; account for
combined context. Pages read current state, so edits can shift positions.
For saved project direction, narrow browsing with `kinds: ["decision", "preference"]`
(CLI `--browse --kind decision --kind preference`). Keep those kinds on later
pages. This uses fallible labels, so search without a filter when classification
is uncertain. Required instructions and normal access checks still apply.
The local Codex configuration and native OpenCode adapter use conversation/session
scope by default; neither identifies individual turns or execution attempts.
Use the CLI below
when MCP is unavailable or explicit task/run scope is needed.

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task TASK_ID --run RUN_ID 'relevant task terms'
```

This profile is bound to the canonical `$HOME/git/cairn` repository identity;
keep that `--repo` value when working in another worktree. Choose actual task/run
identifiers and reuse them for that work. Inspect relevant
bodies with the returned `pull_command`; A records are fallible notes, so verify
current source before applying them. A missing profile or unavailable service
does not block work. Never substitute a local-destination profile when its result
will enter a hosted model, and do not provision credentials as part of routine
retrieval.

If remembered commands and the installed interface disagree, use `cairn version`
and `cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" version`
to distinguish CLI and running API builds. Fresh MCP initialization identifies
the facade build. Missing VCS stamps mean unknown; revision equality does not
prove capabilities or freshness. See `docs/build-identity.md`.

Use native `cairn_remember` when available, or `agent remember` with the same
profile, for explicitly selected, useful
findings. Native `cairn_remember` defaults to repository scope; choose `scope:
"task"` or `"run"` only when guidance belongs to the host's current search labels.
For reusable project guidance keep repository scope. These labels are host
configuration or conversation/session metadata, not execution attestation.
Include source/verification context in the note, choose
`--shareable` only for material suitable for hosted delivery, and supply a stable
`--request-id` when retrying. `--stdin` accepts a chosen note body. Do not capture
raw sessions, private Council content, credentials or workspace dumps; ordinary
capture does not confer authority or prove task success.

When comparing revisions, `cairn_history` lists retained metadata or reads one
exact earlier body using a known record ID. Add `span: {offset, length}` with a
positive version to inspect a bounded historical passage; offsets count bytes.
CLI/API `agent history` remains
available. Historical wording and class do not establish current eligibility or
authority; pull the current note before editing. Budget combined context across
history reads as well as searches and pulls. See `docs/record-history.md`.

When an owner clarification materially changes priorities, evaluation or a recurring
workflow, retain a concise selected `decision` or `preference` with its date and a
pointer to the updated repository source. Check for an existing note first and
revise it when appropriate. Preserve what superseded the earlier direction rather
than recording contradictory summaries as equally current. Do not turn every
message into a note, or treat a remembered preference as authority over current
instructions. Evaluate whether this continuity helps sustained work; successful
capture and retrieval alone do not establish that benefit.
