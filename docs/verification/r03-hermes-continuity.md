# R03 Hermes native continuity mechanism

This mechanism is a new child of accepted prep `417b4e818d67a77f097a92359403cd82cb92722b`.
Only the continuity checker, its tests and this document change. The original
prep remains frozen for Release Root. Review `c7a4b3cb-e137-4c3a-873c-0a87f663af45/v1`
was read in full: N1 requires observing finalizer/auxiliary traffic, N2 requires
fresh exact inputs rather than the old author-path constructor request, and N3
requires an executable full A/B/reverse mechanism. The old constructor request
is now deprecated data with no executable argv. Do not execute SHA `554d8e55`.

The native mechanism is implemented but has not run. The permitted validation
covers guards, the scripted transport, frozen runtime assets, installed setup
and owned Cairn API/PostgreSQL preparation. A stub of the client-process boundary
is labelled `SETUP_STUB_ONLY`; it creates no fake CLI, hook, registration,
authentication record, conversation transcript or native PASS. AC4 and R06
remain open even if the isolated component later passes.

## Executable native sequence

1. An actual first HermesCLI owns selection A and generates the one native
   session ID. Its genuine MemoryProvider and five PluginRegistration handles
   are inspected. It completes a real baseline chat and durability barrier;
   actual SessionDB history is recorded before B is staged. The parent waits
   for the owned child's ready receipt, then
   uses the maintained installer/stager to install B while that A owner remains
   cached. The provider configuration and selected command must remain A.
2. An attempted transition is refused while that owner remains open. Actual
   `HermesCLI.chat()` uses the local scripted chat-completions transport. The
   held client emits callbacks itself; the fixture never calls a hook dispatcher
   or substitutes a provider. The agent/provider identities must remain stable.
3. `_finalize_single_query()` performs the supported idle boundary. Finalizer,
   plugin, auxiliary and atexit activity remain observed. The owned process must
   exit and be reaped before another owner starts. A new CLI resumes the SAME
   native session through `_preload_resumed_session()`, reads the same real
   history, and selects checked Go B. It then chats and finalizes normally.
4. The parent explicitly restores the retained Python /2 configuration,
   installed manifest and engine settings, verifies its frozen runtime members,
   and resumes that same session for the reverse turn. No error path chooses
   this reverse. Profile/account/workspace, model route and credential-selector
   bytes remain fixed throughout.
5. After each child, real SessionDB readback must match its result after atexit,
   preserve both prior history projections as prefixes. A contributes its
   baseline and cached-A turns; B and retained /2 contribute one turn each.
   All late callbacks must use the same session and exact selected argv.
   The real disposable Cairn Store must gain exactly one selected record and
   recall observation per turn, with no coordination-session/delivery effects
   or raw dialogue capture. Repeated finalize capture must not duplicate records.

Successful ownership boundaries never use a kill/replacement to simulate idle.
A failed owned fixture process may be cancelled solely for cleanup; that run
fails and cannot continue or qualify the boundary.

## Admission and isolation

`--phase native` without a plan and native admission context still returns
75/NOTRUN before path inspection, client import or state creation. With both,
the host queries authenticated Cairn `event-status` and the exact source body.
The server event must be a request from Release Root, addressed to this agent
and execution, with this delivery currently leased. Its JSON source must match
`cairn.r03-hermes-native-admission/1`, `one-isolated-hermes-continuity`, one
attempt, a fresh zoned expiry and every plan binding. A local file or phase flag
is not authority. The existing hosted profile stays outside isolation and its
token is never read or exported. A per-Root-event O_EXCL ledger prevents another
attempt under a different output directory. NOTRUN creates no attempt ledger.
Root must separately admit the reviewed exact plan; this code assignment does
not admit a client run.

The unchanged qualified `test_hybrid_runtime_operations.py` wrapper supplies
fresh namespaces, a read-only source tree and disposable application state.
The normal host HOME remains outside it. Inside, the wrapper's established
HOME is `/tmp/home`; HERMES_HOME names only the owned fixture application home.
The frozen standalone Python 3.11.15 uses its existing frozen venv packages
through an explicit module path; there is no runtime rebuild or replacement
environment. The same interpreter bytes also back the retained venv executable.
The complete 64,533-file / 1,428,418,926-byte inventory is rehashed before use.
Hermes is `9904fd411587729dcdf7440cfb830e96e9830597`, package 0.20.5;
inventory SHA is `b2910e13d782eef359ce920659518385c6c022e2901d71b765d86617eb9c39c0`.

Root's exact five Go images come from `7d22b663d6abb0295dda0263adc9033353e5cefa`,
tree `b9589ad0ac27a1557fd25e198143233239b322a5`. Their full recorded source
inventory is checked against those Git blobs, and current retained Python,
installer and adapter files must carry exactly. Image bytes, PostgreSQL assets,
interpreter, libraries, tools, checker/tree/source and local provider target are
bound into the plan before use. Literal selected argv, H13/H150, eight flags,
stdin, backend, SDK, Store and authentication contracts are unchanged.

The wrapper accepts explicit frozen runtime assets. This fixture freezes the
existing `/etc/hosts` and `/etc/nsswitch.conf` at their standard locations so
localhost lookup uses files rather than an undeclared DNS request. No DNS
connection is admitted. Libc's absent `/var/run/nscd/socket` probe is recorded
only when it returns ENOENT; a live connection is refused. Known failed `git`
PATH probes and the maintained installer's deterministic private launch-anchor
probe are recorded separately from executable targets. The locale executable
and required dynamic libraries are pinned. The wrapper itself is not edited.

Python audit hooks reject unexpected sockets, DNS targets and executables.
Passive profiling records actual provider/SDK/request/auxiliary/finalizer calls
and actual installed `run_engine` entries/returns without modifying held client
or provider functions. The enclosing strace records all child process/network
syscalls, including Go and selector descendants and finalizers. Unknown targets,
provider fallback, wrong model/credential requests, foreign callbacks and Python
Source fallback in Go phases fail the fixture. Only the pinned local scripted
HTTP route is a model target. There are no paid providers, signins or outbound
model calls. The response transport implements nonstreaming and SSE completions.

## Shared users and limits

| User | Configuration/state dependency |
| --- | --- |
| CLI owner and memory manager | Held `cli.py`, setup mixin, agent route/credentials and memory manager lifecycle |
| SessionDB | Native session ID, history projections, active-session and turn leases |
| Installed Cairn provider | Cached lifecycle selection, runtime bundle, six selected event commands and five registration handles |
| Plugin manager | Pre/post LLM, post-tool, pre-command, request middleware and session-finalize plugins |
| Cairn controls and MCP | Profile control/binding state, shared MCP process/connection, skill and selected API profile |
| Memory/selector workers | Selected engine config, journal/dedup state, capture/recall observations, external selector process |
| Gateway | Cached agents, profile environment reload, watcher and per-thread gateway/session-store users |
| Native broker and coordination | Active-session lease/broker/RPC, queue/wake hooks and inherited coordination consumers if enabled |
| Unobserved users | Any other watcher, plugin, inherited hook, config or binding user sharing those paths; unknown users are not omitted or presumed independent |

This fixture starts no live gateway or installed watcher and proves no unit
independence. Shared/unknown users still join R06. Session switching changes
per-session provider state; it is not lifecycle-config reload. MCP/skill reloads
are not provider refresh. Neither isolated results nor preparation can be added
up as a full shared-unit qualification.

## Commands and receipts

All execution uses maintained mise/host:exec, private 0700 evidence, one heavy
case at a time and fresh owned state. `mechanism-plan` accepts explicit held
native/manifest, PostgreSQL-root and Root-image manifest paths plus evidence-root
and fresh output. It emits `plan.json` and an exact admission template.

```sh
mise run test:unit -- test_r03_hermes_continuity -v
mise run host:exec -- --timeout 8m --weight 2 -- python3 -B \
  scripts/check_r03_hermes_continuity.py --phase mechanism-setup --plan PLAN
```

The later Root-admitted invocation replaces the setup phase with `native` and
adds `--admission-context` from that actual native request. The packet supplies
concrete argv and all bindings. Internal isolated/worker phases refuse the host
namespace or absent admitted grant before importing the client. Root selects
one exact 1416 code review and separately admits one actual native fixture.
No author landing, push, coordinator CC or extra reviewer/session is implied.
Raw streams, syscall/callback/request journals, selectors, Store readback,
compiled-source/image/runtime pins and process cleanup accompany the candidate.
