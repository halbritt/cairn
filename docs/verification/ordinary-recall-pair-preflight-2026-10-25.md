# CAIRN-219 ordinary recall pair preflight, 2026-10-25

## Provider-free fixture

`trials/ordinary-recall/codex-luna-2026-10-25/FROZEN.json` freezes one synthetic
settlement-retry source, its task, whole-source requirement, concise answer
rubric, and the admission block. Its SHA-256 is
`531a2cadfffac20fbaf2b2861ea4dbebf5e9c8ec32023b97e4ca688f5470e80f` and the
rubric SHA-256 is
`163951f25c2890ea42b7368339b07141a05d7feb7e4bee9835d26c41e92c1899`.
The task text SHA-256 is
`137f573c8033f684529ddf7cbd0dc118dc19071f6431c0dd9d24e96898d9aac6`.

`python3 -B scripts/check_codex_ordinary_recall_pair.py` ran the production
`render_agent_candidates` function for `full` and `compact/1`, returned each
output through an actual Codex `UserPromptSubmit` command hook, then captured
the first request at a loopback-only Responses endpoint. The fixture used an
isolated temporary `CODEX_HOME` per arm and stopped after the local scripted
response. It made two local requests and zero provider calls; it produced no
inferred answer or provider usage.

| Arm | Rendered hook context | Model request | Source preservation |
| --- | ---: | --- | --- |
| Full | 2,913 bytes; SHA-256 `39409cb43920e2183ae4ea014fde58935cd824cacd3a06c5bbcb822a1a6921f5` | Exact rendered hook text observed in request; `gpt-6-luna`, medium effort | Source `00000000-0000-4000-8000-000000000219`, version 7; whole 348-byte body; SHA-256 `9dfedb3fa581458ef2a7ae5128a66378a42dac7ddef4e3e9b41c0f67bc985e95`; complete pull arguments retained |
| Compact/1 | 3,002 bytes; SHA-256 `ff3f6b15896d924fec2a8f7f6c1580b536576ca322905d52c004302f899ef5b0` | Exact rendered hook text observed in request; `gpt-6-luna`, medium effort | Same source ID, version, body, checksum, whole-source extent, and pull arguments |

Presentation differs because compact/1 carries its versioned schema and
explicit zero diagnostics; the full renderer keeps its existing projection.
The 89-byte larger compact payload in this small synthetic case is an observed
fixture size only. It is not a token or cost measurement. The source is directly
in the hook context, so this pair needs no MCP discovery or source read.

## Proposed bounded subscription pilot and admission block

The proposed pilot runs exactly two ordinary Codex turns on the existing
`~/.codex` ChatGPT subscription: one arm per frozen task, same `gpt-6-luna`
model, medium reasoning, and same task/rubric/source. The frozen arm order is
full then compact/1. Before admission, recheck and bind the account, auth,
config, and CLI hashes below. Use 180 seconds per turn. Before each turn,
verify the same account identity and fresh
`account/rateLimits/read`; stop before starting a turn if the primary window is
at or above 80%, ordinary usage is unavailable, or the account/model route has
changed. Never retry on another account or route.

The latest read-only status check was at `2026-10-25T13:05:55Z`: selected account
`ec9905df-bf3d-4479-bd7f-46906f198165`, Codex CLI 0.164.0,
`ordinaryUsageAllowed=true`, `limitId=codex`, Luna present, and primary weekly
window at 11% with no secondary window. Reset was
`2026-11-01T06:13:48Z`. The prior check at 13:01:38Z was 10%; this later
observation is authoritative for the packet. It is not an absolute allowance
and does not guarantee capacity for both turns. Re-read at admission and before
each turn. The selected auth digest was
`8b15a6fb18e233842ec7528fcf67bf815bfd130730a196fbe7f173c94f3ad77c`; Codex CLI
digest was `abab8f847d2e2013560057cbe224d5c09eca0b8e48a3065cfb9a7d9f2e3867b1`;
user config digest was
`167d663e27bef76e0ab532360a841a7246eef00eabcf9f218d2f353bbb31d84e`. Recheck
all three at admission.

For telemetry, use response-ID usage only if an actual native completion event
exposes both its response ID and usage; deduplicate repeated completion events
by that response ID. The current Codex `exec --json` parser in
`scripts/trial_task_eval.py` sees usage on `turn.completed`, not provider
response IDs. Therefore leave per-response usage null unless the observed
native stream proves otherwise, and retain the single terminal whole-turn
aggregate keyed by that arm's unique execution/turn. Do not count cached input
twice. The proposed stop thresholds are 125,000
observed tokens per arm and 250,000 total across the pair. There is no hard
provider-side token cap: usage arrives after a response, so an in-flight turn
can exceed a threshold before the runner can observe and stop. Missing,
malformed, or unjoinable usage leaves that arm unscored and stops the pair.
Do not start the second arm unless the first completed within 125,000 observed
tokens and a fresh account check remains below 80%; the total threshold then
allows no more than another 125,000 observed tokens.
Authentication or quota failure stops the affected turn with no fallback.

**Admission is blocked on subscription-auth isolation.** Do not launch this
pilot with `CODEX_HOME=~/.codex` in a model-facing Codex executor. The selected
home contains the ChatGPT subscription auth used by the process; shell/CodeMode
can potentially read it. `--ignore-user-config` does not suppress separately
loaded `hooks.json` ([Codex hooks documentation](https://learn.chatgpt.com/docs/hooks)),
and `--sandbox read-only` still permits reads. A prior route review observed
that nested `functions.exec` can relay a synthetic auth marker when its host
is available; the local CAIRN-219 fixture does not prove that the subscription
executor disables or restricts that host. This
presentation task does not need MCP or the SIWC direct route, but the executor
must not have access to subscription credentials or production
hooks/configuration. No suitable trusted app-server host that holds auth
outside the model-facing executor has been established here. A fresh
synthetic-auth home is safe for the local fixture but cannot authenticate the
subscription.

Resume only after a trusted app-server host keeps subscription auth outside the
model-facing executor, or an equivalent setup proves the account auth is
unreadable, production hooks are absent, and shell/CodeMode is disabled or
strictly allowlisted. Do not copy raw auth into a model-readable home or
sandbox. The earlier proposed `--ignore-user-config` mitigation did not meet
this gate and is withdrawn. The local fixture uses a fresh synthetic-auth-free
home, loopback endpoint, no database socket, and no provider route; it does not
prove live subscription isolation.

The task rubric grades three required facts independently and rejects applying
the read retry rule to settlement writes. The provider-free fixture does not
establish actor comprehension, accepted task outcome, subscription usage,
latency, or token savings. A single pair would be feasibility evidence only.

**Admission status: blocked on subscription-auth isolation.** No subscription
turn has run. The 11% rate-limit snapshot is not an admission signal by itself.
