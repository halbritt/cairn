# R03 Hermes continuity on the normal host

This privacy correction is a new child of frozen
`343bbb374325dbec0bc3c9dd45dd749967046ad0`, following the earlier direct-host
child of `d28f3f3941f4c81267011b7a1d9df23425b66b7c`.
Only the same continuity checker, tests and document change. That earlier cut and
all diagnostics remain frozen. Owner `28030d65-5545-4ca4-ba29-288f487242a7/v1`,
coordinator `97195305-81b8-4698-a55e-a938edcb82a1/v1`, and Release notice
`c212efb9-6139-41e8-a147-f31eb0e2e52f/v1` were read whole. They supersede the
old bubblewrap execution assumption. Tests and fixtures run directly through
maintained mise/host:exec, preserving normal HOME, PATH, tools and authentication.
There is no namespace sandbox, remapped filesystem, replacement HOME, socket
forward, shared-fixture-view extension or change to the old wrapper.

The actual Hermes client remains unadmitted. Code, guards and owned non-native
API/PG preparation are authorized; imports/construction/chat/finalization and
model use require Root's separately reviewed exact native admission. Setup uses
an explicitly labelled stub of the client-process boundary. It creates no fake
CLI, callback, registration, auth record or native transcript and cannot close
AC4 or R06. Old constructor request `554d8e55` is deprecated and must not run.

## Implemented native sequence, not yet executed

1. One genuine A CLI generates the native session ID. Its real provider and five
   registration handles are inspected. A real baseline chat and memory-manager
   durability barrier establish actual history before B is staged.
2. The parent waits for that owned ready receipt, stages B with the maintained
   installer and checks that the same A provider/agent remains cached. A second
   actual chat must still emit A's selected commands. Transition is refused
   while the old owner remains open.
3. Supported `_finalize_single_query()` plus atexit and process reap establish
   the idle boundary. A new CLI resumes that SAME native session via
   `_preload_resumed_session()`, preserves both real history projections, selects
   checked Go B, chats and finalizes normally.
4. Explicit retained Python /2 configuration, engine and installed manifest are
   restored with their runtime-member pins. That same session resumes and chats
   again. There is no error-triggered reverse or provider-only recreation.
5. Each child is reaped before the next owner. Parent SessionDB readback after
   atexit must preserve prior history prefixes without duplicate user turns.
   The planned one fixture has three owned CLI processes and four native turns:
   baseline A, cached A, B and retained /2. Credential-selector/profile bytes,
   account and workspace remain fixed. Each turn must have one selected Store
   capture/recall effect; finalize duplication, foreign callbacks and Source
   fallback during Go phases fail the fixture. No direct hook call substitutes
   for actual client emission and no held client method is patched.

A failed owned fixture child can be terminated/killed by its exact PID for
cleanup; no process-group signal targets unrelated host work. Such cancellation
fails the run and never qualifies a supported idle boundary.

## Direct host state, identity and provider

The maintained disposable_host OwnedCluster/DisposableHost and installed Hermes
fixtures create private temporary application state. `/tmp` holds only that
short-lived fixture state, never a source worktree. API, PG, controller, client
and hooks see the same actual files and host PIDs/namespaces. HOME and PATH are
recorded and checked unchanged. HERMES_HOME names the one disposable application
profile. No installed profile, live conversation, service or protected database
is changed. Evidence is copied before owned state removal and process cleanup.

The existing frozen venv Python 3.11.15 runs directly; no new interpreter,
container, package environment or runtime relocation is built. Held Hermes is
`9904fd411587729dcdf7440cfb830e96e9830597`, package 0.20.5. The full 64,533-file /
1,428,418,926-byte inventory is rehashed before use; its manifest SHA is
`b2910e13d782eef359ce920659518385c6c022e2901d71b765d86617eb9c39c0`.
Root's exact five images come from `7d22b663d6abb0295dda0263adc9033353e5cefa`,
tree `b9589ad0ac27a1557fd25e198143233239b322a5`. Their recorded source inventory
is checked against those Git blobs; retained Python/installer/adapter sources
must carry exactly. PostgreSQL assets, enumerated libraries and effective normal
host tools/configuration are pinned. No product, Source, SDK, Store, auth,
H13/H150, eight-flag, literal-argv or stdin contract changes.

A socket bound to port 0 lets the kernel select the proposed loopback provider
port at plan time. The admitted run must exclusively bind that exact pinned
127.0.0.1 target before starting any client; failure does not choose another
provider. The local transport implements nonstreaming and SSE completions for
one fixed synthetic model/key. Main and every auxiliary route point to it.
No paid/external model, signin or provider fallback is admitted. Unexpected
credentials are refused before a provider send and never logged as raw headers.

Python audit hooks restrict native child execution to exact selected commands,
the configured disposable MCP command and read-only Git operations. Shell/env
chains do not grant model execution. Socket guards admit only the owned model
and application API. Passive profiling observes provider/SDK/auxiliary/finalizer
calls and genuine installed engine entries/returns without substituting them.
Strace observes process/network metadata for all owned descendants, including
Go and selectors; send/receive buffers are excluded so authentication wire
bytes cannot enter evidence. Synthetic model bodies and hook stdin have their
own selected journals. It rejects
unknown targets and Python Source fallback in Go phases; normal missing lookup
and deterministic installer-anchor probes are separately recorded. The existing
normal hosts/NSS files are used as shared host configuration, not copied or
mounted into a synthetic environment. Workflow admission readback of Cairn's
existing service is identified by exact helper PIDs and kept separate from
application traffic; no test data is written to that service.

## Selected evidence and trace privacy

Review `4815fad5-2a9d-42d7-af4d-bbe2bf2b0a01/v1` found two blockers in the
earlier cut: blanket scratch retention copied disposable API credentials, and
the raw strace file retained unvalidated descendant argv. Historical packets
remain immutable finding evidence; their sensitive files are not copied into
this candidate. This repair does not authorize any native execution.

Retention now defaults to file hashes and stat metadata. Only named checker
receipts are copied: selected synthetic Store bodies, hook stdin, observation
rows, selection/member hashes, baseline/history/idle receipts and validated
phase results. Unknown future files, identity/token documents, application
configuration, source trees, databases and process logs retain no content.
Selected JSON with credential fields fails closed before copying that file.
Required selected bytes are preserved exactly. Credential continuity uses the
existing hashes; it does not require a retained credential payload.

Strace writes to an anonymous pipe, never a raw trace file. A bounded reader
parses exec, process and socket events in memory and discards argv. Exact
executable paths and byte hashes identify the pinned tools, PG images, held
interpreter, Root images and selected owned launchers. The controller checks
stable executable bytes before launch. The reducer observes file identity while
children run, including Go descendants that parent Python audit hooks cannot
see. Dynamic installer probes additionally require the exact source-produced
script bytes, an owned private parent, mode 0500 and no inode alias; their name
or prefix alone never admits them. Failed normal PATH lookups remain declared
negative probes. Unknown or changed successful executables refuse.

NativeAudit still enforces the exact selected hook/MCP vectors before exec and
records literal selected callback stdin. The tracer does not invent an argv
registration requirement for all subprocess descendants. Go-phase descendant
Python Source fallback still fails independently. Rejected native argv, process
errors and tracing input never become retained raw output.

Retained trace facts contain PID, validated executable path, result, process
ancestry/exit and approved sockaddr. Rejected paths/addresses retain hashes and
fixed violation codes. A terminated process or verified-gone owned daemon can
leave a blocking accept/wait, thread creation, or socket metadata operation
unfinished when strace detaches; these remain explicit shutdown metadata.
The unavailable `???` syscall marker requires traced thread creation and confirmed
termination; an unowned marker or unknown successful operation refuses.
Unfinished exec/connect/bind or process creation, unexplained detach,
other malformed input, limits, forbidden network targets and missing required
owned daemon/socket observations still fail. Wire buffers and environment
contents are not traced. Controller and native-hook stdout/stderr retain only
lengths/hashes and exit status on success and failure. Scratch cleanup runs even
when receipt selection refuses; owned API/PG cleanup precedes retention.

The original 15 guards remain. Privacy guards cover random synthetic credential
files (including unknown files and inode aliases), byte-exact selected receipts,
schema refusal, pipe reduction, malformed/consequential unfinished input, hex
string decoding, successful unknown execs, legitimate pinned descendants,
changed executable bytes, actual producer probe bytes, daemon shutdown and
missing observations. A real strace child prints a canary before its unknown
executable path is rejected. NativeAudit separately refuses a random argv
canary without echoing it. No real credential is used in these guards.
The controller terminates only owned PIDs above 1 and never calls a process-group
signal function. PostgreSQL's internal shutdown signals remain process metadata.

Owner-proxy `c1571966-e783-4da0-9374-18d53c0a74fb/v1` and Root assignment
`3c9f7bb0-55e7-40fc-a49e-51c5436d6d54/v1` authorize iteration of these local
non-native gates. They supersede the completed `442cb347` local retry stop.
Frozen `c58b0c79` and its sealed failed packet remain historical failures; no
source change or passing run rewrites them. This permission makes no native,
client-import, model, live or production allowance and resets no real-run cap.

## Live Root admission

The ordinary native phase without both a plan and admission context still
returns 75/NOTRUN before path inspection, import or state creation. With both,
the checker queries authenticated Cairn event-status and the hook's exact source
read using the supplied installed profile. It never reads the token contents,
registers another session or claims another inbox. The request must be from
Release Root, addressed to this execution with the delivery currently leased;
its exact current active source must match the JSON admission schema, fresh
zoned expiry, one-attempt operation and every code/tree/case/image/runtime/tool/
provider binding. A file or phase flag is not authority.

Every actual native worker repeats that live authenticated readback before any
held client import. There is no offline receipt substituted for authorization.
A per-Root-event O_EXCL ledger is claimed immediately before the first held
client import, after readback; B and /2 require that same ledger/binding. Public
CLI phase shortcuts do not expose native workers. The current execution
correction admits no client run and obsolete wrapper failures are retained,
not used to reset or invent native caps. Any genuine final failure stops.

## Shared users and limits

| User | Shared configuration/state |
| --- | --- |
| CLI and memory manager | Agent route/credentials, provider lifecycle and active-session ownership |
| SessionDB | Native ID, both history projections, session and turn leases |
| Installed provider | Cached lifecycle selection, runtime bundle, six selected commands and five handles |
| Plugin manager | LLM/tool/command hooks, middleware and finalize plugins |
| Controls/MCP | Profile control/binding state, shared MCP process/connection and skills |
| Memory/selector workers | Engine configuration, journal/dedup state and Store observations |
| Gateway/native broker | Cached agents, per-turn environment reload, watcher/session-store, active lease/RPC and wake/queue consumers |
| Unknown users | Any other watcher, inherited hook, plugin, config or binding user sharing these paths; unknown does not mean independent |

No live gateway/watcher is started. These users still join R06; isolated fixture
results are not a sum proving complete-unit qualification. Actual native
callbacks, history/credentials through A/B/reverse, auxiliary/finalizer outcomes
and full native load coverage remain unmeasured until Root's exact admission.
AC4, full R06/HC05, live/production and observation remain open.

## Focused checks

```sh
mise run test:unit -- test_r03_hermes_continuity -v
mise run host:exec -- --timeout 8m --weight 2 -- python3 -B \
  scripts/check_r03_hermes_continuity.py --phase mechanism-setup --plan PLAN
```

`mechanism-plan` emits a fresh direct-host plan and admission template from the
frozen code and explicit retained runtime/PG/Root image inputs. Older sandbox
plans are refused. The later native command requires that plan and the actual
separately admitted native request context. Root owns the same 1415 delta
re-review and any later exact native admission; the author does not land/push
or launch a reviewer. All old cuts, runtime assets and failures
remain retained. The packet separates code/setup proof from native acceptance.
