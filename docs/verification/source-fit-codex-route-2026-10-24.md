# CAIRN-223 Codex/Luna route assessment, 2026-10-24

## Scope and source pins

This was a provider-free read-only assessment on source commit
`6929621a7274cbd8eaa57e2e35bf7e3966ad2a72`. The unchanged recovery panel
`trials/source-fit/recovery-2026-10-24/FROZEN.json` validates as six cases and
15 arms with SHA-256
`7924fd78fb240c5910778486e043f6b0d08c1c38c5daa7a5844e3e83c6f99430`.
The installed Codex CLI reports `codex-cli 0.164.0`. No model/provider call,
subscription status query, account configuration read/write, or pilot replay
occurred.

## Evidence and current boundary

The Codex side has useful components: `trial_task_eval.parse_codex_stream`
collects terminal token usage and MCP call arguments; `trial_task_evidence`
parses completed Codex `mcp_tool_call` results; `trial_source_delivery` checks
whole bodies and partial spans against exact source identities. The relevant
provider-free fixtures pass (17 tests across task evidence, source delivery,
and the Codex event parser). The frozen recovery source panel also validates.

Those components do not yet form a Codex source-fit runner:

- `scripts/run_source_fit.py` checks `claude auth status`, invokes `claude -p`
  with Claude-only stream/permission flags, and requires a single model ID from
  Claude assistant events. It has no Codex CLI, account-binding, or Codex usage
  path.
- `scripts/preflight_source_fit.py` drives the lifecycle with
  `harness='claude'`. The existing successful native tool-recovery fixture is
  also Claude-specific and uses a loopback Messages stub.
- The generic Codex usage parser returns input/cache/output counters but no
  resolved model identity. The retained Codex MCP evidence parser is exercised
  by synthetic JSONL fixtures; no source-fit Codex native run has verified
  actual Codex tool results, model identity, and terminal usage together.
- The installed-Codex local request-shape fixture verifies the model label in
  one request and static MCP inventory, then receives HTTP 418 before a native
  task/tool call. It cannot fill the missing accepted-run contract.

Therefore no Codex/Luna panel copy or executable selection was frozen. A model
label alone would not make the current runner conform to the selected route,
and a second runner written without an observed Codex full-tool stream would be
speculative. The next bounded readiness step is a Codex local-only loopback
fixture that uses the disposable source-fit Store to complete native `cairn_search`
and whole `cairn_pull`, emits a Codex terminal usage event, and proves the exact
source extent/digests without retaining response bodies. A Codex runner then
needs an explicit subscription-account binding and actual-model verification
before any provider admission. Only after those checks should a separate Luna
panel freeze be created.

The Claude recovery panel remains unchanged and its stopped pilot is not
replayed. CAIRN-223 remains open for a compatible Codex/Luna runner, accepted
task outcomes, and paired usage/cost evidence.
