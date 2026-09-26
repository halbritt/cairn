# Recent file hints verification — 2026-09-10

An opt-in OpenCode plugin now turns successful native file reads into hints for
subsequent ordinary Cairn searches. In a scripted native session, reading a file
made its associated note retrievable without a filename in the search. The same
read and unmatched query without the plugin returned no optional note. The
enabled session pulled the intended body.

This establishes a working discovery path. It does not establish a completed
model task, better judgment, net time savings or durable memory benefit. No
answering-model inference was used. Association maintenance and relevance across
ordinary multi-turn work still need observation.

## Capability and permission checks

The fixture used OpenCode **1.18.21**, the shipped adapter/plugin, and the actual
authenticated CLI against a disposable PostgreSQL-backed API. A scripted
loopback provider selected tool calls; it did not evaluate the result or supply
an answer through model inference.

Five native cases passed:

- Without the plugin, a successful file read followed by an unmatched query
  returned no associated optional note.
- With the plugin, the same path produced an entity match and a successful
  pull. Reading a second file and retrying with the original `request_id` and
  returned `query_entities` reused the original receipt. Explicit `entities: []`
  disabled hints; a subsequent capture retained no file associations.
- Globally denied search was absent from the offered tool schema. A scripted
  attempt to call it returned an unavailable-tool error and no note.
- Repository-specific denial left the search tool available, but its native
  `context.ask` refused the call for the configured repository.
- A denied read supplied no hint; the subsequent unmatched search returned no
  optional note.

The earlier minimal hook probe also distinguished successful text-file reads
from missing files and directory reads. The installed SDK's hook types and the
pinned [native read implementation](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/tool/read.ts)
identify the file metadata used by the plugin. The plugin does not call Cairn
from those observation hooks.

The Node contract check covers explicit overrides, request IDs and later pages,
session separation, independent returned snapshots, capture separation, invalid
or outside paths, 16-file/128-session bounds and per-file expiration. Installer
tests cover opt-in behavior, unchanged default installation, custom-file
replacement and refusal of plugin symlink destinations before configuration
writes. CI runs the plugin check with Node 24.

Required disposable PostgreSQL/race integration, `make check`, installer tests
and all **40 Python tests** passed. The complete existing native OpenCode
custom-tool suite also passed with the changed search presentation. No store schema change was needed. The
[manifest](recent-file-hints-2026-09-10.json) records checks, private evidence
locations, hashes and the reviewed decision receipt.

## Failed checks and limits

The initial installer test failed because `--recent-files` did not exist. The
initial plugin check could not import the absent implementation. These are
missing-capability checks, not evidence of a previously released defect.

Two native fixture assertions initially assumed denial would contain the words
"rejected" or "denied". OpenCode instead removed globally denied tools and
reported a repository-specific rule preventing the other call. The assertions
were corrected to those observed mechanisms; permission policy was unchanged.

A subsequent run timed out after 60 seconds during the first no-plugin session,
before any tool output. The subprocess was terminated by the fixture and its
logs retained. The next complete five-case run passed. Startup logs do not
establish the cause, and the timeout was not increased. Requests are now retained
as they arrive so future failures preserve partial transport evidence. This is
consistent with the still-unresolved [earlier startup observation](native-startup-2026-09-10.md),
without establishing a shared cause.

The plugin collects logical file names from one native tool. It neither resolves
renames/symlinks nor certifies editing or relevance. Idle expiration is lazy and
state is lost at process exit. New results expose effective hints within the
existing output budget; callers must preserve them on retries and pages.

Pincite packet `pkt-13875aa281866175` informed the permission, placement,
preservation and retention review. Its remaining co-change/recurring-change
obligations are nonmaterial to this host-specific capability: no shared harness
abstraction, duplication consolidation or frequency-based design claim was
selected. The manifest retains the packet identity and explicit limits.

## Local installation

Installed CLI **670cf19** and its matching adapter/plugin. The API stayed on
**3ff1fcd**, PID **3062046**, and PostgreSQL stayed at PID **163669**, schema034.
No restart or migration was needed. Recorded connection, identity, host
permission and semantic configuration hashes were preserved.

A native OpenCode 1.18.21 session in the actual Cairn repository read
`core/currentness.go`, called `cairn_search` with no arguments and pulled the
existing applicability guide **v2**. Its body SHA-256 remained
`617555818bf48ce98cdfdfb3204c4cbf821685e6d718c1006c4ab96dd26374ca`.
The returned `query_entities` contained the relative file name. The route used
the ordinary hosted-agent profile, four main scripted provider requests and no
answering-model inference. No synthetic fixture notes were written to the
operational store.

The first installed probe supplied an absolute read allow-pattern. The pinned
read implementation checks the worktree-relative path, so the read was refused
and supplied no hint. The probe was corrected to allow only
`core/currentness.go`; global host permissions and product code were unchanged.
The failed attempt and successful rerun are retained separately.

The existing OpenCode procedure advanced **v15 → v16**, adding setup, explicit
override, retry/page and read-permission guidance. It is deliberately associated
with the adapter, plugin and installer source files. Ordinary entity retrieval
and pull returned the new version, exact edit retry was idempotent, and history
retained the earlier body. Other draft metadata was preserved. The manifest
retains exact identities and hashes without copying operational note bodies.

These installation checks show that the feature reaches existing useful notes.
They do not establish that an answering agent applied the guidance or improved
its task outcome. Observe ordinary work before expanding automatic collection.

[Feature CI](https://github.com/halbritt/cairn/actions/runs/34513401855) passed
for `670cf19`, including the Node plugin contract check, PostgreSQL/race suite,
Python checks, build/static checks and authenticated CLI/startup verification.
