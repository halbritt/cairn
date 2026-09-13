# OpenCode compact-startup investigation

The native OpenCode 1.18.21 system hook can deliver an index, but the hook alone
does not provide the permission and lifecycle boundary Cairn needs for automatic
retrieval. No production startup plugin was installed. E2 remains partial.

Two isolated native sessions used a synthetic marker and scripted loopback
responses. They made no model inference or Cairn API calls. The fixture included
a synthetic `cairn_search` tool with a native permission request, plus a harmless
tool that caused a second main provider request.

| Observation | Cairn denied | Cairn allowed |
| --- | --- | --- |
| `cairn_search` present in the main model's tool catalog | No | Yes |
| Hook marker in both main provider requests | Yes | Yes |
| Hook marker in the auxiliary title request | Yes | Yes |
| Hook invocations in one session | 3 | 3 |
| Native process exit | 0 | 0 |

The hook preserved existing system blocks and appended the marker once per
request. Each session supplied its own actual ID. Hook input contained only
`sessionID` and `model`; it did not contain the agent, tool permissions, an abort
signal or a marker distinguishing the title request. This checks transport and
hook behavior, not memory use or task benefit. The fixture never read real memory.

## Why a direct retrieval hook is insufficient

Pinned [request preparation](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/llm/request.ts#L68)
invokes `experimental.chat.system.transform` before `chat.params` supplies the
active agent. A once-per-session cache populated by that hook could therefore
start with an auxiliary request; caching by session alone would also leave
freshness, permission changes and expiration unresolved. Fetching on every hook
would multiply retrievals and expose memory to auxiliary requests.

The current Cairn adapter calls `context.ask` before retrieval. A system hook has
no equivalent callback. Its [plugin client](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/plugin/index.ts)
uses the instance API, whose [permission routes](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/server/routes/instance/httpapi/groups/permission.ts)
list pending requests and accept replies. A separate v2 server has a permission
creation endpoint; finding that endpoint in the installed SDK does not establish
that this native plugin can use it. Reading `session.permission` alone is also
insufficient: it did not contain the configured Cairn rule in either fixture.
Native [permission evaluation](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/permission/index.ts)
combines rules and retained approvals; reproducing only part of it would create
another source of permission decisions.

This is expected power of an installed plugin, not a discovered bypass in Cairn's
existing tools. The experiment rejects an unconditional system-hook fetch as the
implementation of E2. It does not establish that every native bootstrap design
is impossible.

## Next implementation boundary

Automatic startup still needs an authorized retrieval before the first relevant
main request, a working pull route under the receipt's caller and destination,
mandatory context, expiration/freshness handling and combined context budgeting.
The generic H0 runner currently rejects index packages. Investigate a controlled
launch with an explicitly supplied ordinary-memory bootstrap and supported pull
tools, or a native entry point that supplies the full permission and request
context. Preserve observer authority separately. A prompt reminding the model to
search can help discoverability, but would not complete automatic index delivery.

The existing OpenCode procedure v10 was pulled before the investigation. Its
permission, session-scope and verification guidance directed these checks; the
new hook findings came from source and native execution. No causal memory-value
claim or task assessment was added.

[Verification metadata](opencode-startup-hook-2026-09-09.json) retains the native
binary and artifact hashes, source pin and doctrine decision. Production code,
adapter settings and services were unchanged. The runtime suite was not rerun
for this investigation and documentation change.
