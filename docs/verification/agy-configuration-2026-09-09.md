# Agy configuration reconnaissance, 2026-09-09

The installed Agy reports **1.2.0** and provides native stdio MCP management.
The isolated configuration check preserves Cairn's executable and argument
vector, including explicit repository/task/run scope and a task-phase label.
It updates the chosen entry, enables/disables/removes it, and preserves an
unrelated entry. This supports a [setup recipe](../agy.md) using the existing MCP
server. It does not establish native tool execution or task benefit.

## Observed contract

The CLI exposes `mcp add`, `list`, `enable`, `disable` and `remove`. Its add help
requires flags before the server name and permits `--` before the server command.
File tracing of an isolated `mcp list` showed a read of
`$HOME/.gemini/config/mcp_config.json`; no project-scope flag appears in registration
help. The file uses `mcpServers` entries with `command`, `args` and optional
`disabled`. Native add writes `disabled: false`; enable removes that key.

The first scripted check incorrectly expected enable to retain the explicit false
field and stopped at that assertion. The corrected check follows the observed
representation and passes. Raw outputs and the failed configuration remain
separate from the successful run. An attempted rerun into the successful output
directory refused and preserved all 27 retained files.

The [optional script](../../scripts/check_agy_config.py) creates a new owner-only
output directory with isolated HOME/XDG directories, forwards only PATH/LANG/TERM,
and uses a synthetic token path that is never created. It runs configuration
commands only. It does not launch Cairn, read owner credentials, change the owner's
MCP configuration, call a model or contact the operational Cairn API.

```sh
python3 scripts/check_agy_config.py \
  --agy "$HOME/.local/bin/agy" --cairn "$HOME/.local/bin/cairn" \
  --output /tmp/cairn-agy-config-new
```

An `execve,connect` trace of the complete check observed only Python and nine
Agy invocations, no launched Cairn process, and no IP or operational API socket
connection. This bounds the no-execution claim to the observed configuration run.

It requires Agy 1.2.0 and records executable and script hashes. `make check` also
passed. No Cairn runtime/store code changed, so database and model-task suites were
not repeated. The installed CLI/API, semantic worker and adapters remain unchanged.

Later note (2026-09-25): the host's Agy now reports 1.2.11, so
`scripts/check_agy_config.py` refuses to run there. This record and the check
cover Agy 1.2.0 only; the check is not part of `make check`.

## Remaining work and decision

U7 remains partial. Still required: native discovery, exact record/evidence pulls,
capture and compare-and-swap edits across sessions, destination and stale-handle
refusals, observed scope metadata or explicit scope in a real session, permission
and reload behavior, and a meaningful Agy task using cross-harness memory.
No H1/H2/H3 lifecycle capability is inferred from MCP registration.

Keep further Agy work behind task-value work in Codex/OpenCode unless a concrete
task needs Agy. The configuration result earns no custom adapter, owner-wide
registration or new harness acceptance claim. It also identifies a constraint for
future work: fixed task/run arguments in user-wide settings can misgroup concurrent
sessions. Do not silently treat them as observed conversation identities.

[Metadata](agy-configuration-2026-09-09.json) retains identities, observations and
decision provenance. Prior Council invocation notes were context for keeping
configuration and native execution separate; their historical permission-bypass
requirements were not applied to these configuration commands.
