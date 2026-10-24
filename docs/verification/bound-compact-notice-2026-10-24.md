# CAIRN-216 bound compact notice probe, 2026-10-24

`scripts/check_bound_compact_notice.py` joins the pinned source coordination,
memory and inbox-recall handlers to a fresh disposable PostgreSQL database
and local Cairn API. A synthetic owner process receives a native
`SessionStart` callback. The test then uses the Store's authenticated
`session-inbox-claim` to pin one notice to a synthetic native turn and saves
that exact attempt into the private coordinator state. A synthetic reserved
`UserPromptSubmit` callback runs the real bound hook, which generates the
compact context and short handle. No native queue submission or model turn
occurred.

The Store issues a mandatory C instruction. Its whole text and record ID
remain in the bound hook's 3,000-byte output. The notice source is 3,675
bytes: under the compact inline cap, but too large beside the required
context. The hook omits its body and supplies a scoped `cairn-inbox read`
handle. The real helper returns the exact whole source and leaves the
delivery leased. Explicit `handled` settlement commits one acknowledgement;
an identical helper retry returns the same result. The publisher sees the
original delivery handled with one attempt and no invented result record.

The probe then forgets the source through the operator path. The worker's real
history request returns `PAYLOAD_UNAVAILABLE`; retrying the already committed
handled settlement refuses with that status, exposes no body, and leaves the
delivery handled at one attempt. This verifies the deleted-payload recovery
path against the disposable Store rather than a mocked history response.

This is a source, hook and Store transition probe. The synthetic admission
does not prove native transport, actor comprehension, accepted task outcome,
provider usage, latency, or an installed Codex/Claude canary.

## Isolated canary preparation

On 2026-10-24, the committed stager created a private, disabled Codex+Claude
candidate at
`~/.cache/cairn-token-efficiency-stages/compact-inbox-216-20261024`. Its exact
profile enables only `capture_observations` and `compact_inbox_version: 1`, so
capture usage could join a later actor workflow without enabling CAIRN-219,
notice batching, context lifecycle, or compact MCP schemas. Each account has a
separate one-binding manifest, and both manifests remain disabled. The Claude
candidate MCP file contains only the Cairn server; the full `.claude.json`
account file and unrelated MCP server entries were not copied.

The private stage report is SHA-256
`11b4935acaac2208f81224bdba46377aa6792bad1cf843709d749f9b7d35d798`. Its
build receipt is SHA-256
`d9e5d17d12d7aeae722e733bbda113781ce2d41cbe7fefca0fcb59ab8c73043d`. The
candidate CLI was built from clean source `14ad620591dd4b64386728662a06c1628c179ec7`
and hashes to
`7487d594c549a29fa9b6f8263b89ae9bd5b98b033cf730a02a5e633518fd851a`. A
read-only `agent version` call reported installed API revision
`19f39f9426725ce91d2928ff5e871ddfebb6aefb` and protocol 2. Both staged
bindings passed validation against their copied bridge. No provider request,
active binding change, native actor turn, or database write occurred in this
preparation.

The isolated artifact does not yet satisfy CAIRN-216 acceptance. It has not
loaded a fresh native turn, joined sender and recipient usage to the same
workflow, measured latency, or graded any task result. Capture work from
CAIRN-215 is separate and cannot substitute for that join. Before a subscription
turn, freeze fresh account-specific capacity observations and percentage
ceilings/reserves for each stage; keep absolute allowance unknown where the
provider does not report it. The present stage is preparation evidence only;
Plane acceptance remains open.

## Disposable native first-turn check

The opt-in `CAIRN_COMPACT_STAGE` path in
`scripts/check_agent_coordination.py` copies the staged Codex account into the
integration test's private temporary home. It points the copied configs at the
test API socket, token and repository, enables the compact manifest only in that
copy, validates its exact pins, then starts the installed Codex app-server with
the test home's `CODEX_HOME` and a refused loopback fixture-provider endpoint.
The native first turn registered the expected Codex thread and associated the
fixture wake attempt with it. The actual staged files remained disabled and
unchanged. The local disposable `check_agent_events.py` run also completed its
existing CLI/API notice, response, oversized-source and settlement-retry
checks. PostgreSQL was stopped and the private test root removed afterward.

This closes native first-turn loading for the staged Codex compact binding in a
fixture environment. It is not a subscription request or a model task: no actor
read a notice, chose a disposition, or produced an accepted result. Sender and
recipient usage, capture joined to the same workflow, latency, paired task
outcomes, and the Claude native path remain unmeasured. The live canary is still
held pending an enforceable per-run capacity policy and fresh status checks.

## Claude private-config attempt

A bounded follow-up used Claude Code 2.1.310 with the staged Claude candidate
copied into a fresh private `HOME` and `CLAUDE_CONFIG_DIR`. Its settings contained
only the staged Cairn coordination hooks; the Messages endpoint was a loopback
stub with a fixture-only API key, and the Cairn API/Store ran on a disposable
PostgreSQL cluster. No installed account configuration, credentials, or
external provider was used. The fake provider received one request and returned
scripted text. The native session was absent from the disposable Store before
that request, so no inbox item was published, claimed, read, or settled. The
Claude `SessionEnd` hook then reported
`INVALID_HOST: native process is not an observed ancestor`.

The isolated binding had `process_names: ["claude"]` and no
`process_exe_dirs`. In this run the native executable was
`~/.local/share/claude/versions/2.1.310`; with the temporary `HOME`, the
coordination engine's default Claude executable-directory lookup pointed inside
the private home, while the process comm did not match `claude`. Source review
confirms that `owner_process` accepts either a configured process name or an
executable under `claude_exe_dirs`, so this attempt stopped at host-process
admission before compact delivery. It supplies no evidence for Claude compact
notice/response, exact source handling, or settlement. The attempted private
fixture code was discarded rather than retaining an unverified integration
path. A future attempt needs a narrowly pinned `process_exe_dirs` entry in its
private test binding, then must repeat the disposable native notice/response
and explicit-settlement checks. CAIRN-216 remains open.

## Claude native compact notice retry

A focused retry copied the binding into a fresh private `HOME` and
`CLAUDE_CONFIG_DIR`, retained `process_names: ["claude"]`, and added only the
resolved installed executable's parent directory to the **private copy's**
`process_exe_dirs`. For Claude Code 2.1.310 that directory was
`/home/halbritt/.local/share/claude/versions`; the staged candidate and
installed account configuration were not edited. The fixture used a fresh
disposable PostgreSQL cluster and Cairn API, a fixture-only API key, and a
loopback Messages server. It made no subscription or external provider call.

The native Claude SessionStart hook registered the session before the local
provider exchange. A notice with a 6,748-byte source was published to that
session. Claude's native Stop path invoked Bash to run the real compact `read`
command; the fixture verified the returned kind, record identity and exact
source body, then invoked compact `settle ... handled`. The command confirmed
the handled result and one attempt, and the sender's event status reported the
delivery handled. PostgreSQL, API, hook state and fixture output were removed
after the run. Reproduction is available through
`scripts/check_claude_compact_native_disposable.py`; the same native fixture is
gated into `scripts/check_agent_coordination.py` when
`CAIRN_CLAUDE_BINARY` is supplied.

This run verifies the request shape around the read boundary: the compact
handle was present, and the exact source was absent, from the outgoing request
before the read command. After Bash ran the explicit read, the full source
appeared in the expected `user/tool_result` block and was checked before
settlement. This establishes native compact notice context and exact source
read; it does **not** establish a provider usage saving or accepted task work.
It exercised one notice only; a compact response workflow, paired task
outcome, provider usage, latency and canary/rollback evidence remain open.
CAIRN-216 remains open.
