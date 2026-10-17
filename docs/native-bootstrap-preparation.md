# Offline native bootstrap source review

`trial_native_bootstrap.prepare_review(document, output)` copies the bounded
source portion of a prospective coding bootstrap and compiles its fixed launch.
It starts no process and exposes no mount, arbitrary argv, profile override,
account discovery or admission API. Existing paired-request `launch` still
refuses execution. The returned status is `source_prepared_for_review`, with
`complete_bootstrap=false` and `runtime_attested=false`.

The complete hook/gateway/selected-H producer interface is unavailable. This
module accepts only `hook_interface=null` and settings with empty hooks for the
partial review portion. Non-null interface declarations refuse with
`hook_interface_unavailable`; `owner.require_complete()` also always refuses
with that fixed code. Empty hooks are not a usable complete coding bootstrap.
No gateway command or authenticated producer is fabricated. A reviewed concrete
interface, its complete held source set, and independently owned observations
will require a separate source change before complete preparation can exist.

## Closed source declaration

The schema is `cairn.native-bootstrap-review/1`, with exactly these fields:

| Field | Meaning |
| --- | --- |
| `binding` | Exact request/occurrence/session canonical UUIDs, case (`T1`–`T5`, `T7`, `T8`), arm (`CURRENT`/`CAND`), and request/workspace/source-set SHA-256 declarations |
| `launch` | Frozen `model` and `effort` only |
| `sources` | Exactly seven `{path, sha256}` references listed below |
| `mcp_binding` | Existing `mcp_arguments` socket/token-file/repo/task/run declaration |
| `limits` | Exact integer-valued `trial_paired_request.ORIGINAL_LIMITS` |
| `permission_sha256` | Accepted canonical `implementation/1` settings-permission hash |
| `hook_interface` | `null`, documenting the missing complete interface |

The binding keys are `request_id`, `occurrence_id`, `session_id`, `case_id`,
`arm`, `request_sha256`, `workspace_sha256`, and `source_set_sha256`. The last is
the canonical JSON hash of the role-to-source-SHA mapping. Their values remain
root-supplied source declarations; hashing them does not authenticate a request,
workspace, account, session, observation or source producer.

| Role | Required leaf name | Byte bound | Source policy |
| --- | --- | ---: | --- |
| `cli` | `claude` | 512 MiB | Executable regular file |
| `mcp_binary` | `cairn` | 512 MiB | Executable regular file |
| `permission_source` | `trial_task_permissions.py` | 64 KiB | Accepted implementation source hash |
| `prompt` | `prompt.txt` | 9500 | UTF-8, one frozen user prompt |
| `instruction` | `instruction.txt` | 9500 | UTF-8 appended memory instruction |
| `settings` | `settings.json` | 64 KiB | Exact partial settings shape |
| `mcp` | `mcp.json` | 64 KiB | Exact single Cairn stdio declaration |

All source leaves must be nonempty, owned by the current effective UID, have
one link, and lack group/other write or special mode bits. Only the binary roles
may have execute bits. System-owned binaries therefore need a separately frozen,
owner-supplied regular source copy; this API does not provision that copy.
The existing request's `PIN_LIMIT` is per file. This preparer additionally
enforces a conservative aggregate 512 MiB source-copy ceiling across all seven
roles; it does not give each binary a separate aggregate allowance. Generated
input or control metadata is separate from that source-copy count. Disk/IO wall time is not externally supervised by this
offline helper. It does not claim the runtime cgroup's 512 MiB limit or a native
wall deadline was enforced. Copy and hash-only readback use at most 64 KiB IO
blocks; only files of at most 64 KiB can be read as text or collected bytes.

Preparation now reads prompt, instruction, settings, MCP and generated control
metadata through `Snapshot.read_text`. Native generated-input rechecks use that
path too. It hashes original bytes and incrementally decodes strict UTF-8, then
checks the original length, digest and held file identity before returning text.
Invalid UTF-8 still refuses; JSON parsing retains duplicate-key and nonfinite
value rejection. The accepted contents and file-size limits are unchanged.

Text reads request at most 8192 bytes at a time and discard each raw read before
the next. The UTF-8 decoder can also hold at most three carry bytes and a
carry-plus-read concatenation: these local raw payload backings are bounded by
`2 * 8192 + 6` bytes. There is no collected raw block list or complete raw join
on this path. This is a local backing bound, not an attempt allocation quote.
Decoded fragments coexist with joined text; parsed graphs, caller inputs,
compiler encodings, Python overhead and other owners still require complete
working/raw custody under the original combined limit. Legacy `Snapshot.read`
continues to return immutable bytes and retains its collecting implementation.

Settings contain exactly `permissions`, `hooks`, `autoMemoryEnabled`, and
`claudeMdExcludes`. Permissions match the compiler's accepted implementation/1
object; hooks must be empty, automatic memory false, and Claude-MD exclusions
exactly `["**"]`. MCP contains only `mcpServers.cairn`, type `stdio`, command
`/native/cairn`, and the exact `mcp_arguments` result including both 32000-byte
ceilings. No socket, repository or token contents are opened. No additional
server, command, environment, settings source or caller argument is accepted.

`compile_launch` still owns all flags and budgets: exactly one JSON user message,
eight turns, six builtins, fixed search/pull tool declaration, original suppressed
permission prompts, and no session persistence. Its 9500-byte total includes
the message, UTF-8 instruction, 512-byte reserve and at least 1000 hook bytes.
The original search/pull/final/tool/hook/time constraints remain unchanged.
No 600-second source ceiling or USD 4 estimate is substituted for the missing
frozen campaign-wide native wall and billing proof.

## Separate SDK source review

`trial_native_bootstrap.prepare_sdk_review(document, output)` accepts only
`cairn.sdk-native-bootstrap-review/1`. It uses the same closed declaration fields,
seven source roles, held-file checks, empty command hooks, settings/MCP agreement
and original limits. The legacy preparer accepts only its original schema and
still rejects an SDK launch before creating a user file.

The SDK preparer calls `compile_sdk_launch` once and holds its `SDKLaunch` and
original `SDKControl`. Compilation chooses the initialization and callback IDs;
review does not recompile them. The controller owns the single user message and
all future write frames. This route generates `sdk-control.json` containing only
the pristine control metadata, and never generates `input.jsonl`. A future SDK
driver must keep native stdin open for bound control replies rather than apply
the command route's close-after-user rule.

`SDKControl.pristine_binding()` returns a fresh, non-consuming projection of the
actual held initialization and user byte counts/hashes, initial IDs, instruction
byte count, guaranteed context and current ledger. It refuses after write
progress, closure, failure or checked initial-state drift without poisoning the
controller. Digest/decoded metadata working allocations remain under the outer
budget; this projection does not create another complete raw user or initialize
buffer. The separate 8500-byte wire and 9500-byte selected-input limits still
apply, as described in [SDK control](native-sdk-control.md).

`SDKReviewPreparation.review_bindings()` rechecks held sources and metadata,
the original controller identity and pristine projection, argv, permissions,
instruction and compiled user/context bindings. Progressing or replacing the
controller invalidates a new review. Cleanup always belongs to the original
controller, even if the launch field was replaced, and independently attempts
the Snapshot closes. Fixed controller cleanup uncertainty is
`sdk_control_cleanup_unknown`; a context-manager exit without a primary failure
reports any combined cleanup uncertainty as `sdk_review_cleanup_unknown`.
Inherited constructor cleanup annotations are inspected only after owned closes.
Unreadable, malformed or non-fixed annotations become
`cleanup_annotation_unknown`; attaching diagnostics to a custom primary remains
best effort and cannot replace it.

The SDK review reports `input_mode=controller_owned`, control metadata and user
count/hash instead of a separate writable input message. It retains the same
missing gates and false complete/runtime flags. `require_complete()` still
rechecks custody and refuses `hook_interface_unavailable`. This owner provides
no executable handoff, transport, native setting observation or runtime permit.

### Single-use in-process source custody

`review.consume_for_session()` returns `SDKSessionCustody` after one final
pristine review. It moves the original `Snapshot`, `SDKLaunch` and `SDKControl`
in the same process; it does not recompile IDs, serialize the controller, start
a process or grant runtime admission. Constructing `SDKSessionCustody(review)`
uses the same consuming path, rather than accepting caller-supplied raw handles
or metadata. Calls must be serialized.

After transfer, the old review refuses both another review and another transfer
with `sdk_review_transferred`; its `close()` becomes a no-op. The session now
owns cleanup. Its `launch` and `control` properties expose the original objects
as borrowed references. Replacing fields on the old review cannot redirect the
session's cleanup. Existing Python aliases are not revoked, and this is not an
isolation boundary against mutation by the caller.

`session.source_bindings()` rechecks held source/snapshot identities and hashes,
the original control IDs, compiled launch/user/context bindings, permissions and
instruction. It permits controller progress: the returned `initial_control`
contains the original pristine metadata from `sdk-control.json`, not a current
writer ledger or native delivery observation. The status is
`source_custodied_for_session`; the existing schema, limits, missing gates and
false complete/runtime flags remain unchanged. Returned metadata is a fresh
copy. A failed source check closes the session and refuses
`session_custody_unknown`. `session.require_complete()` rechecks this source
custody and still refuses `hook_interface_unavailable`.

Close the session explicitly or use its context manager. Failed construction
closes whichever owner holds the resources; interruptions retain their original
exception with best-effort cleanup annotations. Session cleanup independently
attempts the original control and held descriptors and retains unknown outcomes
without retry. A context exit with cleanup uncertainty and no primary exception
raises `sdk_session_cleanup_unknown`; an existing primary is preserved. These
are source-lifetime observations only. The move supplies no transport, native
end policy, hook evidence, process wait, funded account or execution permit.

## File custody and consumption

Each source path is opened from a held `/` directory through held nofollow
directory edges to a nofollow, nonblocking regular-file FD. The source FD,
initial device/inode/type/UID/GID/mode/link/size/mtime/ctime facts, and every
ancestor FD remain owned. Copies read/hash/write incrementally and refuse drift,
unexpected length, excess bytes or pin mismatch. Positive partial writes are
completed; a zero write or IO error refuses. Source hashes are not checked by
closing and reopening the original pathname.

The output directory is created exclusively with mode 0700 before declaration
inspection. An existing output refuses `preparation_consumed`. Once creation
succeeds, any inspection/copy/compile fault leaves a private partial directory;
it is never removed or retried by this helper. Failed declarations do not yield
a review owner. The caller must also prevent replay under a new output name:
this helper owns one output-directory intent, not a campaign registry.

Every snapshot file is exclusive, fsynced and read back through the held output
directory. A checked readonly FD replaces the owned writable FD on sealing;
snapshots are mode 0400 (binaries 0500), single-linked and owner-owned. Directory
entries are fsynced. The command route generates `input.jsonl` exclusively from
the compiler's one frozen message; the SDK route generates only its control
metadata alongside the seven source snapshots. There is no atomic
batch-publication claim.

`owner.review_bindings()` rechecks all held directory edges, source identities,
snapshot identities, lengths and hashes before returning fresh selected metadata.
It retains source and snapshot identity facts, compiled launch/input/instruction/
permission hashes, exact binding declarations and missing gates. Raw prompt,
instruction, settings and binary bytes are not placed in those metadata. The
snapshot contains those explicit source bytes privately for review, and the
owner's compiled launch remains transient in memory. No returned path/mount list
declares them safe for an actor. Actor candidate construction must exclude
evaluator controls, oracle, origin, grading and account materials; no roles for
those sources or automatic additions exist here. Correct classification of the
seven explicitly selected source contents remains the root's responsibility.

Call `close()` or use the context manager. Every identity-bound descriptor gets
one close attempt; a failed close is detached and never retried. If initial FD
anchor acquisition fails, its number is unbound: the helper records cleanup
unknown and refuses to close a possibly reused foreign descriptor. Such a fault
may leave a genuinely opened FD unreleased; it is never successful closure.
Changed/reused
device/inode/type refuses closing an unbound FD and records
`descriptor_cleanup_unknown`. All remaining closes are still attempted. Review
after close or custody fault refuses; primary refusal and cleanup uncertainty
remain separate fixed facts with no exception text. Preparation/review interrupts
close all bound leases and propagate the primary exception, with fixed cleanup
facts attached. Context cleanup does not mask a primary body exception. Cleanup
annotation is best effort for immutable/custom exceptions: the owner retains its
fixed cleanup result even when the primary cannot accept a `cleanup_faults`
attribute. A failed annotation never replaces that primary.
Cleanup
itself continues across any close failure/interruption and returns fixed unknown.
The source-review operation
does not claim hostile same-UID isolation, proof of open-file-description identity
after reuse to the same inode, immunity to changes after the final check, snapshot
mount lifetime, hard IO supervision, loaded native settings or runtime release.
No saved manifest is an FD capability or a runtime attestation.

## Remaining integration gates

Root owns a complete frozen hook/gateway/source interface, the actual native
bootstrap/capture and isolated account route; independent hook producer wait,
elapsed/input/output/attempt counters and selected H persistence; actual CLI
settings/init/source agreement; permission and B3/D1 policy; held mounts and
worktree/export; controller-owned actual waits and kernel release; and the
campaign wall/billing/account gates. None is discharged by this partial source
compiler. Future integration must consume the held source owner rather than
turning saved hashes or source request labels into authentication or admission.
