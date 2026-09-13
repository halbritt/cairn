# Deterministic OpenCode configuration, 2026-09-08

`cairn opencode-config` generates the native MCP configuration shape that caused
the first [configuration task](mcp-host-use-2026-09-08.md) to fail. The task now
has a deterministic setup path; a model is unnecessary for assembling this JSON.
See the [usage guide](../mcp.md#opencode-example).

The command shares the MCP startup flag parser and scope/room validation. Its
output names the running executable, makes socket/token paths absolute, preserves
literal scope and context arguments, and uses a command array. The default
fragment leaves tool permissions absent. `--memory-only` emits the observed
standalone policy allowing search/body pulls and denying other tools.

Unit checks cover spaces, quotes, Unicode, newlines, shell metacharacters,
missing credential files, absent HOME/database access, invalid scope/room and
output failure. The full disposable PostgreSQL integration suite executes the
generated command through real stdio and authenticated retrieval, including all
five matching context pins and absent-context preservation. `make check` and
all 17 Python unit tests passed.

OpenCode 1.18.21 independently connected using both generated modes. These
checks used an executable path containing spaces and a quote, then launched the
harness from another working directory. The generator ran without HOME or a
usable database address. No model call was made. The
[metadata](opencode-config-2026-09-08.json) records the development binary and
configuration hashes; clean installation is recorded separately.

The generator does not read tokens, authenticate a profile, verify runtime
context, merge configuration files or launch a task. The host owns deployment,
permissions and current task/run identity. In particular, existing explicit
tool allowances can survive a merge with the memory-only policy. No global
user configuration, API/store permission or dependency was changed.

The bounded doctrine decision used repository precedence, evidence before
intervention and existing behavior preservation. Its identities and remaining
nonmaterial obligations are in the metadata; private evidence and decision
records retain the observations. No general task-benefit or architecture claim
is inferred from these checks.

## Installed CLI

Code commit `ea2324f2c8cd350c568cb7b7aede7157ff428e57` passed
[CI run 34281053830](https://github.com/halbritt/cairn/actions/runs/34281053830).
The clean build is installed at `~/.local/bin/cairn`, SHA-256
`a471294df74cfda683f064ef69be988751e44e1ea8e808fca7636506533a4926`.

Its generated command retrieved the exact current MCP procedure, preserved
the declared task class and pull retries, and ended with clean stdio EOF.
OpenCode connected using the installed generator's unmodified configuration.
The API remained on its `cda6762` build without restart or profile changes;
both services stayed active. A selected update to the reusable procedure now
points future setup tasks to the generator, and its exact new body was retrieved.
This is source-verified ordinary memory, not an additional model-benefit trial.
