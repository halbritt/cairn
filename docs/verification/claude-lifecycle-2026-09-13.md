# Claude lifecycle memory verification — 2026-09-13

The optional hooks provide bounded ambient retrieval and selected capture in
Claude Code 2.1.270. The proactive Cairn skill is deployed across Codex, OpenCode,
Agy and Claude Code. This verifies the implemented lifecycle slice; it does not
establish full design acceptance or sustained task benefit.

## Executed checks

- The Python suite exercises input/output bounds, Unicode and escaped text,
  omission of tool payloads/reasoning/sidechains, hosted-only retrieval, empty
  searches, stale optional bodies, malformed model output, refused writes,
  timeouts, concurrent capture exclusion, opt-outs, stable project context,
  correction/retry behavior, and preservation of existing installation settings.
- `make test-integration` with `CAIRN_CLAUDE_LIFECYCLE_BINARY` runs the real Claude
  binary against a disposable PostgreSQL API and scripted loopback provider.
  It verifies startup and streamed task context independently, native MCP pulls
  from injected handles, resume, manual PreCompact, post-compaction SessionStart,
  and SessionEnd. The stored checkpoint is a shareable repository-scoped ordinary
  note with API-owned attribution; later capture updates its existing record.
  A local-only canary is absent from provider requests.
- Three real model selections using the installed profile's
  `claude-fable-5-1[1m]` configuration retained a substantive PostgreSQL decision
  and unfinished tests, skipped a simple lookup, and honored an explicit memory
  exclusion. Inputs were synthetic. Memory calls were replaced at the test
  boundary, so this check made no database writes. Native scripted execution
  separately verifies actual writes.
- The current installed Cairn CLI/API accepted the new hook's ordinary hosted
  read, delivering a 6,235-byte context package with an expanded note. This was
  read-only compatibility verification against existing shared guidance.
- `make check` and the complete Python suite pass. Skill quick validation,
  skillpack validation, `install.sh --check`, and exact comparisons of Agy's
  copied skill passed. Two existing skillpack advisories remain unchanged.

Repeat the native check with:

```sh
CAIRN_CLAUDE_LIFECYCLE_BINARY="$HOME/.local/bin/claude" make test-integration
```

The normal CI Python suite includes lifecycle tests without requiring Claude or
provider credentials. To repeat model selection explicitly, using the desired
installed model and existing provider authentication:

```sh
python3 scripts/check_claude_selection.py \
  --claude "$HOME/.local/bin/claude" --model 'claude-fable-5-1[1m]'
```

## Corrections made during verification

The first boundary test found that adding a truncation label could exceed the
excerpt cap. The implementation now bounds the serialized dialogue, including
JSON escaping and role metadata.

The first real-authentication check identified that Claude's `--bare` mode
excludes OAuth credentials. Selection now uses an empty settings-source list,
explicitly disabled hooks/tools, a temporary working directory, and disabled
session persistence while preserving provider authentication.

A later native check found that `SCOPE_EMPTY` is a successful empty search,
not a service failure. Treating it as a failure could prevent the first note
from being created. A regression test covers that case.

One-shot CLI prompts did not exercise `UserPromptSubmit` in this installed
version. The native fixture now submits streamed user messages and requires both
startup and submitted-task injection. Fixture workspaces have their own Git root
so an unrelated parent repository does not change the project retrieval query.
These failures are retained as verification limits of the earlier checks.

## Installation and limits

The skill change is skillpack `9601be0`, pushed to main. All 62 Python tests,
`make check`, and the final disposable integration run passed. The final native
fixture used the exact deployed hook SHA-256
`ff91f658674d289e2eafb6a0624eacfb57bfba4c9fd6fa07de15963d646d0e50`.

The hooks are installed in both `~/.claude/settings.json` and
`~/.claude-harm/settings.json`, with separate deployments under
`~/.local/share/cairn/claude-hooks` and `claude-harm-hooks`. Exact source comparisons,
one-command-per-event checks, preservation of prior hooks and unrelated settings,
and live read-only hosted retrieval passed for each profile (3,694 context bytes
in the final installed checks). Fresh sessions load the new settings.

The hook is a standalone
Python deployment using the existing Cairn CLI, API, hosted token, and collection;
it requires no store migration or service restart. See
[installation and operation](../claude-lifecycle.md).

Model selection remains fallible. It can miss context outside the excerpt, does
not verify tool work independently, and may add latency or cost at compaction
and exit. The automatic-compaction event uses the same PreCompact handler as the
native-tested manual event; automatic context exhaustion was not induced in the
native fixture. Abrupt termination cannot guarantee an end hook. Other harnesses
receive the proactive skill, while their automatic lifecycle integration remains
future work.

Engineering guidance came from validated Pincite release `d3e0c0d4`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`.
The final evidence packet is `pkt-37411cb53aaca730` (JSON SHA-256
`69ef2c0e028454e6c669b0b65d91c12ce59cb132d9e13165e70cfe4dfe25f654`).
Repository contracts and the owner's implementation/deployment instruction
controlled scope. Resource ownership, bounded calls, and structured cleanup
informed the hook design. Existing MCP behavior, authority, and PostgreSQL storage
were preserved. OpenCode's earlier unconditional system hook and instruction-only
recall were alternatives; the former lacked the needed lifecycle boundary and
the latter did not supply the requested automatic retrieval. General domain-model
and long-term retention obligations are nonmaterial to this bounded host adapter;
whole-host budgets and broader usefulness remain explicitly unproved.
