# Native OpenCode session tools

Verified 2026-09-09 UTC on OpenCode 1.18.21. Baseline Cairn `86f3298`.

## Why this adapter

The pinned OpenCode
[MCP catalog](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/mcp/catalog.ts#L38)
passes tool names and arguments without session metadata. Its
[native tool interface](https://opencode.ai/docs/custom-tools/) supplies
`context.sessionID`. The installed binary's `debug agent --tool` path creates an
actual OpenCode session and invokes the custom tool without a model call.
A native prototype searched the operational Cairn API under that exact session
scope and returned the current setup procedure.

The selected adapter uses that existing native context and Cairn's authenticated
CLI. Keeping manually rewritten MCP scope remains supported. Inferring OpenCode
session identity from the Codex metadata field, process IDs or startup environment
was rejected. No generic plugin framework, second store or new authority channel
was introduced.

## Implemented behavior

The native file exports search, body pull, evidence pull, ordinary capture and
ordinary edit. It reads host-owned connection settings and declares search scope
as repository plus `opencode/<sessionID>` and `<sessionID>`. The CLI and API retain
all authentication, destination, class, scope, eligibility, retry and edit checks.
Writes return identifiers without echoing bodies. Native permission requests and
CLI cancellation/timeouts remain explicit.

The CLI index adds `pull_arguments` alongside its existing `pull_command`, using
one receipt/handle/request UUID for both. Existing command consumers keep their
interface; native callers no longer need to parse shell text. Full selected
context and source seals remain in the presentation. Source semantic schemas,
ranker and database schema are unchanged.

Source review found that OpenCode's custom-tool registry checks Zod defaults but
passes the original arguments to `execute`. The adapter therefore implements the
ordinary `note` default itself. Native verification includes omitted `kind` and
`shareable`, with the operator inspecting the resulting default local note in
the disposable database.

## Checks and local adoption

- `make check` and `make test` passed, including 26 Python tests.
- `CAIRN_OPENCODE_TOOLS_BINARY=... make test-integration` passed with a disposable
  PostgreSQL cluster, package race tests and actual OpenCode custom-tool execution.
- Native calls verified different session scopes; exact capture retries and
  changed-payload refusal; default local exclusion from hosted search; exact body
  and evidence pulls with unchanged retry credits; A edit/retry and fresh version
  retrieval; stale handle/version refusals; outside-repository denial; and native
  tool permission denial.
- CLI checks verified that structured expansion and the displayed shell command
  return the same retained response and spend one credit using their shared UUID.
- The checked CLI and adapter were installed on this host. The local project tool
  searched and pulled the exact existing ordinary setup procedure through the
  hosted profile. Its native session exists in the isolated OpenCode runtime's
  own session database. Installation files are excluded from Git.

The actual harness checks used an isolated no-model catalog and explicit tool
permissions. Its configured model endpoint was unusable and no model was called.
This is direct native tool execution, not delegated problem solving. The
[manifest](opencode-tools-2026-09-09.json) retains source, binary, log and private
operational evidence hashes.

## Shared revision follow-up

After implementation `3285367`, the existing ordinary OpenCode setup procedure
was revised from version 4 to version 5 through the installed native edit tool.
The full draft was preserved except for a selected source-checked paragraph about
the new adapter. A fresh native OpenCode session retrieved the exact new body.
Two fresh Codex conversations, using their existing project MCP configuration,
each searched and pulled that same exact revision twice. No model turn was
started and no authority promotion occurred. This establishes working maintenance
and retrieval of shared knowledge through both installed harness interfaces.

## Limits

The installed adapter makes current shared memory available through native
OpenCode tools without manual session IDs. A session can contain multiple turns;
it is not an execution attempt or a task-success witness. This does not establish
model-directed use, automatic extraction, resume/compaction correctness, or better
task outcomes. OpenCode still applies its own result truncation and task budget.
The source and runtime compatibility claim is limited to the tested version.

Remove the local tool file to return to the existing CLI/MCP paths. Revisit the
adapter if OpenCode exposes session metadata through MCP, changes its custom-tool
contract, or actual use needs a different scope or result budget.
